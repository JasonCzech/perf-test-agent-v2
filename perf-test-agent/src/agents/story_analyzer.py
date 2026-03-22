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
from src.models.test_case import StoryAnalysisOutput
from src.tools.langchain_tools import (
    make_dynatrace_tools,
    make_jira_tools,
    make_rag_tools,
    make_snowflake_tools,
)


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
        return """You are a senior performance test engineer at AT&T specializing in
telecom systems. Your job is to analyze Jira user stories and extract structured
performance test cases.

## Your Environment
You are testing an AT&T telecom stack:
- Frontend: OPUS (legacy HTTP/HTML), Salesforce/Mulesoft (REST), IDP (mobile REST)
- Middleware: CSI (REST+SOAP gateway), GDDN (Solace MQ), CAS/iCAS (payments)
- Backend: TLG/Amdocs (customer system of record)
- Periphery: BSSe, Customer/Order/Identity Graphs (SOLr), Cassandra, Oracle, LDAP

## Your Task
For each Jira story:
1. Fetch the story details using fetch_jira_story
2. Search enterprise knowledge base for related documentation (solution intent, architecture)
3. Search for past performance incidents on involved systems
4. Get historical baselines from Snowflake for known transactions
5. Discover related Dynatrace services and their current metrics

## Output Requirements
For each story, produce one or more TestCase objects containing:
- Transaction flows with system call sequences (e.g., IDP -> CSI -> TLG -> BSSe)
- Protocol identification (REST/JSON, SOAP/XML, Web HTTP/HTML, Solace MQ)
- SLA targets (p90, p95 response times; error rate thresholds)
- Risk level with rationale
- Recommended test harness (LoadRunner for OPUS HTTP/HTML, JMeter for REST/SOAP)
- Bulk data requirements
- Preconditions

## Harness Selection Rules
- OPUS (Web HTTP/HTML) -> LoadRunner Enterprise (VuGen Web HTTP/HTML protocol)
- Salesforce/Mulesoft REST APIs -> JMeter
- IDP REST APIs -> JMeter
- CSI SOAP endpoints -> JMeter (SOAP/XML sampler)
- CSI REST endpoints -> JMeter
- Solace MQ flows -> JMeter (Solace plugin) or LoadRunner (custom protocol)

## SLA Guidelines (if not specified in story)
- Interactive transactions (UI): p90 < 3000ms, error rate < 1%
- API calls: p90 < 1000ms, error rate < 0.5%
- Batch/async: p90 < 10000ms, error rate < 2%
- Authentication: p90 < 500ms, error rate < 0.1%

## Risk Assessment
- HIGH: New system integration, first-time testing, Amdocs backend changes
- MEDIUM: Existing flows with configuration changes, new API versions
- LOW: Regression testing of stable flows

Your Final Answer MUST be valid JSON matching the StoryAnalysisOutput schema."""

    def build_agent_input(self, state: PipelineState) -> str:
        stories = state.jira_story_keys
        sprint = state.sprint_name

        if stories:
            return (
                f"Analyze the following Jira stories and extract performance test cases: "
                f"{', '.join(stories)}. "
                f"For each story, fetch the details, search the enterprise knowledge base "
                f"for related documentation, check for historical baselines, and identify "
                f"all transaction flows that need performance testing."
            )
        elif sprint:
            return (
                f"Fetch all stories from sprint '{sprint}' and analyze them for "
                f"performance test case extraction. For each story, search the enterprise "
                f"knowledge base for related documentation and identify transaction flows."
            )
        else:
            return "No stories or sprint specified. Please provide input."

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
