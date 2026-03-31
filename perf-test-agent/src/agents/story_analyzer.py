"""Phase 1: Story Analyzer Agent.

Extracts performance test cases from Jira user stories, enriched by:
- Enterprise RAG (Microsoft Graph: SharePoint wikis, PowerBI, ServiceNow)
- Snowflake historical baselines
- Dynatrace service topology

Produces structured TestCase objects with SLA targets, risk profiles,
transaction flows, and data requirements.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

from src.agents.base_agent import BaseAgent
from src.config.settings import LLMTask
from src.models.pipeline_state import (
    PhaseResult,
    PhaseStatus,
    PipelinePhase,
    PipelineState,
)
from src.models.test_case import (
    StoryAnalysisEvaluation,
    StoryAnalysisOptimizationIteration,
    StoryAnalysisOutput,
)
from src.prompts import load_prompt
from src.tools.langchain_tools import (
    make_dynatrace_tools,
    make_jira_tools,
    make_rag_tools,
    make_snowflake_tools,
)


def _artifacts_to_prompt_section(user_artifacts: dict[str, Any] | None) -> str:
    if not isinstance(user_artifacts, dict):
        return ""

    mode = str(user_artifacts.get("mode") or "augment").strip().lower() or "augment"
    free_text = str(user_artifacts.get("free_text") or "").strip()

    jira_links: list[str] = []
    raw_links = user_artifacts.get("jira_links")
    if isinstance(raw_links, list):
        jira_links = [str(link).strip() for link in raw_links if str(link).strip()]

    docs: list[dict[str, str]] = []
    raw_docs = user_artifacts.get("doc_blocks")
    if isinstance(raw_docs, list):
        for doc in raw_docs:
            if not isinstance(doc, dict):
                continue
            title = str(doc.get("title") or "").strip()
            content = str(doc.get("content") or "").strip()
            if title or content:
                docs.append({"title": title, "content": content})

    file_placeholders: list[dict[str, str]] = []
    raw_files = user_artifacts.get("file_placeholders")
    if isinstance(raw_files, list):
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            notes = str(item.get("notes") or "").strip()
            if name or notes:
                file_placeholders.append({"name": name, "notes": notes})

    if not free_text and not jira_links and not docs and not file_placeholders:
        return ""

    lines: list[str] = [
        "",
        "Additional user-provided artifacts (augment mode):",
        f"- Mode: {mode}",
    ]

    if free_text:
        lines.append("- Free text notes:")
        lines.append(free_text)

    if jira_links:
        lines.append("- Jira links to include:")
        lines.extend([f"  - {link}" for link in jira_links])

    if docs:
        lines.append("- Document blocks:")
        for idx, doc in enumerate(docs, start=1):
            title = doc["title"] or f"Document {idx}"
            content = doc["content"] or "(no content provided)"
            lines.append(f"  - {title}: {content}")

    if file_placeholders:
        lines.append("- File placeholders (content pending):")
        for item in file_placeholders:
            name = item["name"] or "Unnamed file"
            notes = item["notes"] or "No notes"
            lines.append(f"  - {name}: {notes}")

    lines.append(
        "Use these artifacts as supplemental context only and merge them with Jira/RAG "
        "findings when extracting test cases."
    )
    return "\n".join(lines)


class StoryAnalyzerAgent(BaseAgent[StoryAnalysisOutput]):
    """Phase 1 agent: Jira stories -> structured test cases."""

    phase = PipelinePhase.STORY_ANALYSIS
    output_model = StoryAnalysisOutput
    llm_task = LLMTask.COMPLEX_REASONING  # Needs GPT-4o for nuanced extraction

    def get_tools(self) -> list[BaseTool]:
        return (
            make_jira_tools()
            + make_rag_tools()
            + make_snowflake_tools()
            + make_dynatrace_tools()
        )

    def get_system_prompt(self) -> str:
        return load_prompt("story_analysis")

    async def run(self, state: PipelineState) -> PipelineState:
        """Execute story analysis with an optimizer-evaluator feedback loop."""
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

            max_iterations = max(1, int(self.settings.story_analysis_optimizer_max_iterations))
            score_threshold = float(self.settings.story_analysis_optimizer_score_threshold)
            min_delta = float(self.settings.story_analysis_optimizer_min_improvement_delta)
            optimizer_enabled = bool(self.settings.story_analysis_optimizer_enabled)

            result = await self._execute_agent(agent_input, executor=generation_executor)
            output = self.parse_output(result)

            optimization_history: list[StoryAnalysisOptimizationIteration] = []
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
                latest_input = agent_input
                for iteration in range(1, max_iterations + 1):
                    candidate_output = output if iteration == 1 else None

                    if iteration > 1:
                        latest_input = self._build_refinement_input(agent_input, latest_feedback, best_output)
                        run_result = await self._execute_agent(latest_input, executor=generation_executor)
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
                        candidate_output.warnings.append(warning)
                        output.warnings.append(warning)
                        stop_reason = "evaluator_failure"
                        optimization_history.append(
                            StoryAnalysisOptimizationIteration(
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
                        StoryAnalysisOptimizationIteration(
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
                output.warnings.append("Story analyzer optimizer is disabled; proceeding with first-pass output.")
                optimization_history.append(
                    StoryAnalysisOptimizationIteration(
                        iteration=1,
                        score=output.confidence_score,
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
            state.story_analysis_optimization_history = [
                entry.model_dump() for entry in optimization_history
            ]
            state.story_analysis_optimization_meta = {
                "enabled": optimizer_enabled,
                "max_iterations": max_iterations,
                "score_threshold": score_threshold,
                "min_improvement_delta": min_delta,
                "iterations_run": len(optimization_history),
                "best_score": best_score if best_score >= 0 else final_output.confidence_score,
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
            phase_result.artifacts["optimization_history"] = state.story_analysis_optimization_history
            phase_result.artifacts["optimization_meta"] = state.story_analysis_optimization_meta
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

    def build_agent_input(self, state: PipelineState) -> str:
        stories = state.jira_story_keys
        sprint = state.sprint_name
        artifacts_section = _artifacts_to_prompt_section(state.user_artifacts)

        if stories:
            return (
                f"Analyze the following Jira stories and extract performance test cases: "
                f"{', '.join(stories)}. "
                f"For each story, fetch the details, search the enterprise knowledge base "
                f"for related documentation, check for historical baselines, and identify "
                f"all transaction flows that need performance testing."
                f"{artifacts_section}"
            )
        elif sprint:
            return (
                f"Fetch all stories from sprint '{sprint}' and analyze them for "
                f"performance test case extraction. For each story, search the enterprise "
                f"knowledge base for related documentation and identify transaction flows."
                f"{artifacts_section}"
            )
        else:
            return f"No stories or sprint specified. Please provide input.{artifacts_section}"

    def parse_output(self, agent_result: dict[str, Any]) -> StoryAnalysisOutput:
        raw = agent_result.get("output", "")
        return self._parse_json_output(raw, StoryAnalysisOutput)

    async def _evaluate_output(
        self,
        output: StoryAnalysisOutput,
        score_threshold: float,
    ) -> StoryAnalysisEvaluation:
        evaluator_prompt = load_prompt("story_analysis_evaluator")
        candidate_json = output.model_dump_json(indent=2)
        prompt = (
            f"{evaluator_prompt}\n\n"
            f"Required pass threshold: {score_threshold:.2f}\n\n"
            "Candidate StoryAnalysisOutput JSON:\n"
            f"{candidate_json}"
        )
        llm_response = await self.llm.ainvoke(prompt)
        raw = self._extract_llm_text(llm_response)
        return self._parse_json_output(raw, StoryAnalysisEvaluation)

    def _build_refinement_input(
        self,
        original_input: str,
        feedback: list[str],
        best_output: StoryAnalysisOutput,
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

    def update_state(self, state: PipelineState, output: StoryAnalysisOutput) -> PipelineState:
        state.test_cases = [tc.model_dump() for tc in output.test_cases]
        state.sla_targets = output.sla_targets
        state.risk_profiles = [
            {"test_case_id": tc.id, "risk_level": tc.risk_level, "rationale": tc.risk_rationale}
            for tc in output.test_cases
        ]
        state.transaction_flows = [
            {"test_case_id": tc.id, "flows": [f.model_dump() for f in tc.transaction_flows]}
            for tc in output.test_cases
        ]
        state.source_documents = output.rag_documents_consulted
        return state

    def summarize_output(self, output: StoryAnalysisOutput) -> str:
        tc_count = len(output.test_cases)
        sla_count = len(output.sla_targets)
        high_risk = sum(1 for tc in output.test_cases if tc.risk_level.value in ("high", "critical"))

        lines = [
            f"Analyzed {len(output.analyzed_stories)} stories, extracted {tc_count} test cases.",
            f"SLA targets defined: {sla_count}",
            f"High/Critical risk test cases: {high_risk}",
            f"Confidence: {output.confidence_score:.0%}",
        ]
        if output.warnings:
            lines.append(f"Warnings: {'; '.join(output.warnings)}")
        if output.suggested_followups:
            lines.append(f"Follow-ups needed: {'; '.join(output.suggested_followups)}")
        return "\n".join(lines)
