"""Phase 1: Story Analyzer Agent.

Extracts performance test cases from Jira user stories, enriched by:
- Enterprise RAG (Microsoft Graph: SharePoint wikis, PowerBI, ServiceNow)
- Snowflake historical baselines
- Dynatrace service topology

Produces structured TestCase objects with SLA targets, risk profiles,
transaction flows, and data requirements.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool

from src.agents.base_agent import BaseAgent
from src.config.settings import LLMTask
from src.models.pipeline_state import PipelinePhase, PipelineState
from src.prompts import load_prompt
from src.models.test_case import StoryAnalysisOutput
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
        "Use these artifacts as supplemental context only and merge them with Jira/RAG findings when extracting test cases."
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
