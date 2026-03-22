"""Phase 6: Results Analyzer & Phase 7: Postmortem agents."""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, tool

from src.agents.base_agent import BaseAgent
from src.config.settings import LLMTask
from src.models.analysis import AnalysisReport, PostmortemOutput
from src.models.pipeline_state import PipelinePhase, PipelineState
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
        return """You are a performance test analyst at AT&T. Your job is to analyze test
results and produce a comprehensive report.

## Analysis Tasks
1. **SLA Compliance**: Compare each transaction's p90/p95 against SLA targets
2. **Baseline Comparison**: Compare against Snowflake historical baselines
3. **Peak/Break/Stability Summary**: Summarize findings from Phase 5
4. **Defect Identification**: Create Jira defects for SLA violations
5. **Go/No-Go Recommendation**: Based on all findings

## Defect Severity Rules
- BLOCKER: System crashes, data corruption, complete SLA failure
- CRITICAL: >50% SLA violation, resource exhaustion
- MAJOR: 10-50% SLA violation, intermittent errors
- MINOR: <10% SLA deviation, cosmetic issues

## Report Structure
- Executive Summary
- Test Scope & Configuration
- SLA Compliance Results
- Peak Point / Breakpoint / Stability Results
- Anomaly Summary
- Defect List
- Baseline Comparison
- Recommendations
- Go/No-Go Decision

## Publishing
1. Generate Word report
2. Save results to Snowflake for future baselining
3. Upload report to SharePoint
4. Create Jira defects for each finding

Your Final Answer MUST be valid JSON matching the AnalysisReport schema."""

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
        return """You are a continuous improvement specialist for AT&T performance testing.
Your job is to conduct a postmortem of the test cycle and capture lessons learned.

## Postmortem Tasks
1. Review all phase results for pain points and issues
2. Categorize issues: environment, data, tooling, process, communication, technical
3. Calculate time lost for each issue
4. Identify resolutions and process improvements
5. Extract structured lessons learned for the knowledge base
6. Archive structured data to Snowflake
7. Index unstructured lessons to RAG for future pipeline runs

## Lesson Format
Each lesson should specify:
- Context: When does this lesson apply?
- Lesson: What was learned?
- Recommendation: What should be done differently?
- Applicable systems and phases

## Feedback Loop
Lessons indexed to RAG will be retrieved by future Phase 1 (Story Analysis)
and Phase 2 (Test Planning) runs, creating a continuous improvement cycle.

Your Final Answer MUST be valid JSON matching the PostmortemOutput schema."""

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
