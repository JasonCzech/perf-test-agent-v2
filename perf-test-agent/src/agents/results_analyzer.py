"""Phase 6: Results Analyzer & Phase 7: Postmortem agents."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from src.agents.base_agent import BaseAgent
from src.config.settings import LLMTask
from src.models.analysis import (
    AnalysisReport,
    AnalysisReportEvaluation,
    PostmortemOutput,
    ResultsAnalysisOptimizationIteration,
)
from src.models.pipeline_state import (
    PhaseResult,
    PhaseStatus,
    PipelinePhase,
    PipelineState,
)
from src.prompts import load_prompt
from src.tools.langchain_tools import (
    make_jira_tools,
    make_rag_tools,
    make_sharepoint_tools,
    make_snowflake_tools,
)


# ═══════════════════════════════════════════════════════════════════════
# Phase 6: Results Analyzer
# ═══════════════════════════════════════════════════════════════════════

class ResultsAnalyzerAgent(BaseAgent[AnalysisReport]):
    """Phase 6 agent: execution results -> analysis report + defects."""

    phase = PipelinePhase.REPORTING
    output_model = AnalysisReport
    llm_task = LLMTask.COMPLEX_REASONING

    def get_tools(self) -> list[BaseTool]:
        return (
            make_jira_tools()
            + make_snowflake_tools()
            + make_sharepoint_tools()
            + [self._make_generate_word_report_tool()]
        )

    def _make_generate_word_report_tool(self) -> BaseTool:
        @tool
        def generate_word_report(report_json: str, output_path: str = "./runs/report.docx") -> str:
            """Generate a MS Word performance test report from the analysis data.
            report_json: JSON string of the analysis report data.
            Returns the file path of the generated report."""
            # In production, this uses python-docx to create a formatted report
            from pathlib import Path
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            # Placeholder: would call the docx generation pipeline
            Path(output_path).write_text(f"PLACEHOLDER: Full Word report generation\n{report_json[:500]}")
            return f"Report generated: {output_path}"
        return generate_word_report

    def get_system_prompt(self) -> str:
        return load_prompt("reporting")

    def build_agent_input(self, state: PipelineState) -> str:
        execution = json.dumps({
            "peak_point": state.peak_point_results,
            "breakpoint": state.breakpoint_results,
            "stability": state.stability_results,
            "anomalies_count": len(state.anomalies),
        }, indent=2)
        sla = json.dumps([s.model_dump() for s in state.sla_targets], indent=2)

        return (
            f"Analyze the performance test results and produce a report:\n\n"
            f"Execution Results:\n{execution}\n\n"
            f"SLA Targets:\n{sla}\n\n"
            f"Anomalies logged: {len(state.anomalies)}\n\n"
            f"Run ID: {state.run_id}\n"
            f"Stories: {', '.join(state.jira_story_keys)}\n\n"
            f"Tasks:\n"
            f"1. Evaluate SLA compliance for all transactions\n"
            f"2. Compare against historical baselines in Snowflake\n"
            f"3. Generate Word report\n"
            f"4. Upload to SharePoint\n"
            f"5. Create Jira defects for violations\n"
            f"6. Provide go/no-go recommendation"
        )

    def parse_output(self, agent_result: dict[str, Any]) -> AnalysisReport:
        raw = agent_result.get("output", "")
        return self._parse_json_output(raw, AnalysisReport)

    def update_state(self, state: PipelineState, output: AnalysisReport) -> PipelineState:
        state.report_path = output.word_report_path
        state.sharepoint_url = output.sharepoint_url
        state.jira_defect_keys = [d.jira_key for d in output.defects if d.jira_key]
        return state

    def summarize_output(self, output: AnalysisReport) -> str:
        return (
            f"Report: {output.title}\n"
            f"SLA Compliance: {'PASS' if output.overall_sla_pass else 'FAIL'}\n"
            f"Defects: {output.total_defects} ({output.blockers} blockers)\n"
            f"Go/No-Go: {output.go_no_go.upper()}\n"
            f"SharePoint: {output.sharepoint_url or 'Not published'}\n"
            f"Recommendations: {len(output.recommendations)}"
        )

    async def run(self, state: PipelineState) -> PipelineState:
        """Execute results analysis with an optimizer-evaluator feedback loop."""
        self.run_dir = Path(self.settings.pipeline_run_dir) / state.run_id / self.phase.value
        self.run_dir.mkdir(parents=True, exist_ok=True)

        phase_result = PhaseResult(
            phase=self.phase,
            status=PhaseStatus.RUNNING,
            started_at=datetime.utcnow(),
        )

        self.log.info("phase_starting", phase=self.phase.value, run_id=state.run_id)

        try:
            agent_input = self.build_agent_input(state)

            phase_context = (state.phase_pre_execution_context or {}).get(self.phase.value, "").strip()
            if phase_context:
                agent_input = (
                    f"{agent_input}\n\n"
                    "HITL pre-execution context for current phase:\n"
                    f"{phase_context}"
                )

            phase_prompt_override = (state.phase_prompt_overrides or {}).get(self.phase.value, "").strip()
            generation_executor = None
            if phase_prompt_override:
                generation_executor = self._build_agent_executor(system_prompt_override=phase_prompt_override)

            max_iterations = max(1, int(self.settings.results_analysis_optimizer_max_iterations))
            score_threshold = float(self.settings.results_analysis_optimizer_score_threshold)
            min_delta = float(self.settings.results_analysis_optimizer_min_improvement_delta)
            optimizer_enabled = bool(self.settings.results_analysis_optimizer_enabled)

            result = await self._execute_agent(agent_input, executor=generation_executor)
            output = self.parse_output(result)

            optimization_history: list[ResultsAnalysisOptimizationIteration] = []
            all_reasoning_trace: list[dict[str, Any]] = []
            all_tool_calls: dict[str, int] = {}

            initial_trace, initial_tools = self._extract_reasoning_trace(result)
            all_reasoning_trace.extend(self._tag_iteration_trace(initial_trace, 1))
            all_tool_calls = self._merge_tool_summaries(all_tool_calls, initial_tools)

            best_output = output
            best_score = -1.0
            previous_score: float | None = None
            stop_reason = "optimizer_disabled"
            latest_feedback: list[str] = []

            if optimizer_enabled:
                for iteration in range(1, max_iterations + 1):
                    candidate_output = output if iteration == 1 else None

                    if iteration > 1:
                        refined_input = self._build_refinement_input(agent_input, latest_feedback, best_output)
                        run_result = await self._execute_agent(refined_input, executor=generation_executor)
                        candidate_output = self.parse_output(run_result)
                        iter_trace, iter_tools = self._extract_reasoning_trace(run_result)
                        all_reasoning_trace.extend(self._tag_iteration_trace(iter_trace, iteration))
                        all_tool_calls = self._merge_tool_summaries(all_tool_calls, iter_tools)

                    if candidate_output is None:
                        break

                    try:
                        evaluation = await self._evaluate_output(candidate_output, score_threshold)
                    except Exception as eval_exc:
                        warning = (
                            f"Optimizer evaluator failed on iteration {iteration}: {eval_exc}. "
                            "Using latest valid candidate."
                        )
                        candidate_output.recommendations.append(warning)
                        output.recommendations.append(warning)
                        stop_reason = "evaluator_failure"
                        optimization_history.append(
                            ResultsAnalysisOptimizationIteration(
                                iteration=iteration,
                                score=previous_score if previous_score is not None else 0.0,
                                score_delta=0.0,
                                accepted_as_best=False,
                                stop_reason=stop_reason,
                                feedback=[],
                                strengths=[],
                                gaps=[str(eval_exc)],
                                generated_summary=self.summarize_output(candidate_output),
                            )
                        )
                        break

                    score_delta = 0.0 if previous_score is None else evaluation.score - previous_score
                    accepted = evaluation.score > best_score
                    if accepted:
                        best_output = candidate_output
                        best_score = evaluation.score

                    iteration_stop_reason = ""
                    if evaluation.score >= score_threshold or evaluation.pass_threshold:
                        iteration_stop_reason = "threshold_met"
                    elif previous_score is not None and score_delta < min_delta:
                        iteration_stop_reason = "no_improvement"
                    elif iteration >= max_iterations:
                        iteration_stop_reason = "max_iterations_reached"
                    elif not evaluation.actionable_feedback:
                        iteration_stop_reason = "no_actionable_feedback"

                    optimization_history.append(
                        ResultsAnalysisOptimizationIteration(
                            iteration=iteration,
                            score=evaluation.score,
                            score_delta=score_delta,
                            accepted_as_best=accepted,
                            stop_reason=iteration_stop_reason,
                            feedback=evaluation.actionable_feedback,
                            strengths=evaluation.strengths,
                            gaps=evaluation.gaps,
                            generated_summary=self.summarize_output(candidate_output),
                        )
                    )

                    latest_feedback = evaluation.actionable_feedback
                    previous_score = evaluation.score
                    stop_reason = iteration_stop_reason or "continue"

                    if iteration_stop_reason:
                        break

            else:
                output.recommendations.append(
                    "Results analyzer optimizer is disabled; proceeding with first-pass output."
                )
                optimization_history.append(
                    ResultsAnalysisOptimizationIteration(
                        iteration=1,
                        score=0.0,
                        score_delta=0.0,
                        accepted_as_best=True,
                        stop_reason=stop_reason,
                        feedback=[],
                        strengths=[],
                        gaps=[],
                        generated_summary=self.summarize_output(output),
                    )
                )

            final_output = best_output if best_score >= 0 else output

            if not optimizer_enabled:
                all_reasoning_trace = all_reasoning_trace or initial_trace
                all_tool_calls = all_tool_calls or initial_tools

            state = self.update_state(state, final_output)
            state.results_analysis_optimization_history = [
                entry.model_dump() for entry in optimization_history
            ]
            state.results_analysis_optimization_meta = {
                "enabled": optimizer_enabled,
                "max_iterations": max_iterations,
                "score_threshold": score_threshold,
                "min_improvement_delta": min_delta,
                "iterations_run": len(optimization_history),
                "best_score": best_score if best_score >= 0 else 0.0,
                "stop_reason": stop_reason,
            }

            self._save_artifacts(final_output)

            phase_result.status = PhaseStatus.AWAITING_APPROVAL
            phase_result.completed_at = datetime.utcnow()
            phase_result.duration_seconds = (
                phase_result.completed_at - phase_result.started_at
            ).total_seconds()
            phase_result.summary = self.summarize_output(final_output)
            phase_result.artifacts["effective_prompt"] = phase_prompt_override or self.get_system_prompt()
            phase_result.artifacts["optimization_history"] = state.results_analysis_optimization_history
            phase_result.artifacts["optimization_meta"] = state.results_analysis_optimization_meta
            if phase_context:
                phase_result.artifacts["pre_execution_context"] = phase_context
            phase_result.reasoning_trace = all_reasoning_trace
            phase_result.tool_calls_summary = all_tool_calls

            self.log.info(
                "phase_completed",
                phase=self.phase.value,
                duration_s=phase_result.duration_seconds,
                optimization_iterations=len(optimization_history),
                optimization_stop_reason=stop_reason,
            )

        except Exception as e:
            phase_result.status = PhaseStatus.FAILED
            phase_result.errors.append(str(e))
            phase_result.completed_at = datetime.utcnow()
            self.log.error("phase_failed", phase=self.phase.value, error=str(e))

        state.set_phase_result(phase_result)
        return state

    async def _evaluate_output(
        self,
        output: AnalysisReport,
        score_threshold: float,
    ) -> AnalysisReportEvaluation:
        evaluator_prompt = load_prompt("results_analysis_evaluator")
        candidate_json = output.model_dump_json(indent=2)
        prompt = (
            f"{evaluator_prompt}\n\n"
            f"Required pass threshold: {score_threshold:.2f}\n\n"
            "Candidate AnalysisReport JSON:\n"
            f"{candidate_json}"
        )
        llm_response = await self.llm.ainvoke(prompt)
        raw = self._extract_llm_text(llm_response)
        return self._parse_json_output(raw, AnalysisReportEvaluation)

    def _build_refinement_input(
        self,
        original_input: str,
        feedback: list[str],
        best_output: AnalysisReport,
    ) -> str:
        feedback_lines = "\n".join(f"- {item}" for item in feedback) if feedback else "- Improve overall quality."
        return (
            f"{original_input}\n\n"
            "Optimizer feedback from prior iteration:\n"
            f"{feedback_lines}\n\n"
            "Current best candidate summary:\n"
            f"{self.summarize_output(best_output)}\n\n"
            "Regenerate a full improved JSON output that addresses all feedback."
        )

    @staticmethod
    def _extract_llm_text(response: Any) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts)
        return str(content)

    @staticmethod
    def _tag_iteration_trace(trace: list[dict[str, Any]], iteration: int) -> list[dict[str, Any]]:
        tagged: list[dict[str, Any]] = []
        for item in trace:
            tagged_item = dict(item)
            tagged_item["optimization_iteration"] = iteration
            tagged.append(tagged_item)
        return tagged

    @staticmethod
    def _merge_tool_summaries(base: dict[str, int], new: dict[str, int]) -> dict[str, int]:
        merged = dict(base)
        for tool_name, count in new.items():
            merged[tool_name] = merged.get(tool_name, 0) + count
        return merged


# ═══════════════════════════════════════════════════════════════════════
# Phase 7: Postmortem
# ═══════════════════════════════════════════════════════════════════════

class PostmortemAgent(BaseAgent[PostmortemOutput]):
    """Phase 7 agent: logs + issues -> postmortem + lessons learned -> Snowflake + RAG."""

    phase = PipelinePhase.POSTMORTEM
    output_model = PostmortemOutput
    llm_task = LLMTask.COMPLEX_REASONING

    def get_tools(self) -> list[BaseTool]:
        return (
            make_snowflake_tools()
            + make_rag_tools()
            + [self._make_archive_postmortem_tool(), self._make_index_lessons_tool()]
        )

    def _make_archive_postmortem_tool(self) -> BaseTool:
        @tool
        def archive_to_snowflake(run_id: str, entries_json: str) -> str:
            """Archive postmortem entries to Snowflake for structured querying."""
            from src.integrations.snowflake_client import SnowflakeClient
            sf = SnowflakeClient()
            entries = json.loads(entries_json)
            sf.save_postmortem(run_id, entries)
            return f"Archived {len(entries)} entries to Snowflake"
        return archive_to_snowflake

    def _make_index_lessons_tool(self) -> BaseTool:
        @tool
        def index_lessons_to_rag(lessons_json: str) -> str:
            """Index lessons learned to the RAG knowledge base for future pipeline runs.
            This makes lessons searchable by future Story Analysis and Planning phases."""
            # In production, this pushes to Azure AI Search indexer
            lessons = json.loads(lessons_json)
            return f"Indexed {len(lessons)} lessons to RAG knowledge base (placeholder)"
        return index_lessons_to_rag

    def get_system_prompt(self) -> str:
        return load_prompt("postmortem")

    def build_agent_input(self, state: PipelineState) -> str:
        phase_summaries = {}
        for name, result in state.phase_results.items():
            phase_summaries[name] = {
                "status": result.status.value,
                "duration_s": result.duration_seconds,
                "errors": result.errors,
                "warnings": result.warnings,
                "summary": result.summary,
            }

        return (
            f"Conduct a postmortem for pipeline run {state.run_id}:\n\n"
            f"Phase Results:\n{json.dumps(phase_summaries, indent=2)}\n\n"
            f"Anomalies: {len(state.anomalies)}\n"
            f"Defects: {state.jira_defect_keys}\n"
            f"Config mismatches found: {len(state.config_mismatches)}\n\n"
            f"Review each phase for issues, categorize them, calculate time lost, "
            f"and extract lessons learned. Archive to Snowflake and index to RAG."
        )

    def parse_output(self, agent_result: dict[str, Any]) -> PostmortemOutput:
        raw = agent_result.get("output", "")
        return self._parse_json_output(raw, PostmortemOutput)

    def update_state(self, state: PipelineState, output: PostmortemOutput) -> PipelineState:
        state.postmortem_entries = [e.model_dump() for e in output.entries]
        state.lessons_learned = [l.lesson for l in output.lessons_learned]
        state.feedback_indexed = output.rag_indexed
        return state

    def summarize_output(self, output: PostmortemOutput) -> str:
        return (
            f"Postmortem entries: {len(output.entries)}\n"
            f"Lessons learned: {len(output.lessons_learned)}\n"
            f"Time lost: {output.total_time_lost_hours:.1f} hours\n"
            f"Top pain points: {'; '.join(output.top_pain_points[:3])}\n"
            f"Snowflake archived: {'YES' if output.snowflake_archived else 'NO'}\n"
            f"RAG indexed: {'YES' if output.rag_indexed else 'NO'}"
        )
