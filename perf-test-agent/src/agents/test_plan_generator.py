"""Phase 2: Test Plan Generator Agent.

Takes test cases from Phase 1 and produces a complete performance test plan
including workload models, test scenarios, environment specs, data prep steps,
monitoring configs, and entry/exit criteria.

Outputs: Jira stories for perf testing + Word document for stakeholder review.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, tool

from src.agents.base_agent import BaseAgent
from src.config.settings import LLMTask
from src.models.pipeline_state import PipelinePhase, PipelineState
from src.models.test_plan import TestPlan
from src.tools.langchain_tools import (
from src.prompts import load_prompt
    make_dynatrace_tools,
    make_jira_tools,
    make_rag_tools,
    make_snowflake_tools,
)


class TestPlanGeneratorAgent(BaseAgent[TestPlan]):
    """Phase 2 agent: test cases -> comprehensive test plan."""

    phase = PipelinePhase.TEST_PLANNING
    output_model = TestPlan
    llm_task = LLMTask.COMPLEX_REASONING

    def get_tools(self) -> list[BaseTool]:
        tools = (
            make_jira_tools()
            + make_rag_tools()
            + make_snowflake_tools()
            + make_dynatrace_tools()
        )
        # Add the app config reader tool
        tools.append(self._make_app_config_tool())
        return tools

    def _make_app_config_tool(self) -> BaseTool:
        @tool
        def read_app_config(app_name: str) -> str:
            """Read the application-specific .md configuration file for test planning instructions.
            These contain app-specific SLA targets, known constraints, and testing instructions."""
            import os
            config_dir = os.path.join(os.path.dirname(__file__), "..", "..", "templates", "app_configs")
            config_path = os.path.join(config_dir, f"{app_name.lower()}.md")
            if os.path.exists(config_path):
                with open(config_path) as f:
                    return f.read()
            return f"No app config found for '{app_name}'. Available configs: {os.listdir(config_dir) if os.path.exists(config_dir) else 'none'}"
        return read_app_config

    def get_system_prompt(self) -> str:
        return load_prompt("test_planning")

    def build_agent_input(self, state: PipelineState) -> str:
        test_cases_summary = json.dumps(state.test_cases[:10], indent=2)  # Limit for context
        sla_targets = json.dumps([s.model_dump() for s in state.sla_targets], indent=2)

        return (
            f"Create a performance test plan based on these test cases:\n\n"
            f"Test Cases:\n{test_cases_summary}\n\n"
            f"SLA Targets:\n{sla_targets}\n\n"
            f"Risk profiles: {json.dumps(state.risk_profiles, indent=2)}\n\n"
            f"Source stories: {', '.join(state.jira_story_keys)}\n\n"
            f"Read any available app config files for the involved systems. "
            f"Search for Dynatrace production metrics to inform the workload model. "
            f"Get Snowflake baselines for known transactions."
        )

    def parse_output(self, agent_result: dict[str, Any]) -> TestPlan:
        raw = agent_result.get("output", "")
        return self._parse_json_output(raw, TestPlan)

    def update_state(self, state: PipelineState, output: TestPlan) -> PipelineState:
        state.test_plan = output.model_dump()
        state.workload_model = output.test_scenarios[0].workload_model.model_dump() if output.test_scenarios else None
        state.test_scenarios = [s.model_dump() for s in output.test_scenarios]
        state.environment_requirements = output.environment_spec.model_dump()
        state.bulk_data_requirements = [d.model_dump() for d in output.data_preparation]
        return state

    def summarize_output(self, output: TestPlan) -> str:
        scenarios = [f"{s.test_type.value}({s.duration_minutes}min)" for s in output.test_scenarios]
        risks = sum(1 for r in output.risk_register if r.level.value in ("high", "critical"))
        return (
            f"Test Plan: {output.title}\n"
            f"Scenarios: {', '.join(scenarios)}\n"
            f"Systems required: {', '.join(output.environment_spec.systems_required)}\n"
            f"Data prep steps: {len(output.data_preparation)}\n"
            f"High/Critical risks: {risks}\n"
            f"Estimated duration: {output.estimated_duration_days} days"
        )
