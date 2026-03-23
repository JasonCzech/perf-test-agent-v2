"""Phase 3: Environment Configuration & Triage Agent.

Validates that the PERF environment is correctly configured before testing:
- AKS deployments, replicas, env vars, config maps
- Azure Application Gateway backend pools and routing
- Amdocs backend endpoints (TLG, BSSe, OMS)
- Database connectivity, MQ topics
- Ensures PERF configs, NOT QC

Produces a golden config baseline for daily drift checks.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, tool

from src.agents.base_agent import BaseAgent
from src.config.settings import LLMTask
from src.models.env_config import ConfigValidationReport, GoldenConfig
from src.models.pipeline_state import PipelinePhase, PipelineState
from src.prompts import load_prompt
from src.utils import env_reference_store


class EnvConfigAgent(BaseAgent[ConfigValidationReport]):
    """Phase 3 agent: validate environment configs against reference."""

    phase = PipelinePhase.ENV_TRIAGE
    output_model = ConfigValidationReport
    llm_task = LLMTask.EXTRACTION  # Mostly comparison work, GPT-4o-mini is fine

    def get_tools(self) -> list[BaseTool]:
        return [
            self._make_read_reference_tool(),
            self._make_check_aks_config_tool(),
            self._make_check_appgw_tool(),
            self._make_check_endpoint_tool(),
            self._make_save_golden_config_tool(),
        ]

    def _make_read_reference_tool(self) -> BaseTool:
        @tool
        def read_env_reference(
            application_key: str | None = None,
            environment_name: str = "PERF",
            api_variant: str = "core",
        ) -> str:
            """Read environment references for a given application/API/environment.

            If no application_key is provided (or "all"), all references for the
            requested environment are concatenated so the agent can validate the
            entire scope in one pass.
            """

            scope_key = application_key if application_key and application_key.lower() != "all" else None
            records = env_reference_store.list_references(
                application_key=scope_key,
                environment=environment_name,
                api_variant=api_variant if scope_key else None,
            )

            if not records:
                return (
                    f"No environment reference registered for application={application_key or 'ALL'} "
                    f"env={environment_name} variant={api_variant}"
                )

            documents: list[str] = []
            for record in records:
                try:
                    yaml_content = env_reference_store.read_reference_yaml(record)
                except FileNotFoundError:
                    documents.append(
                        f"# Missing reference file for {record.application_name} ({record.environment}/{record.api_variant})"
                    )
                    continue

                header = (
                    f"# Application: {record.application_name} | Env: {record.environment} | Variant: {record.api_variant}\n"
                    f"# Last Updated: {record.last_updated.isoformat()} by {record.updated_by}\n"
                )
                documents.append(header + yaml_content)

            return "\n\n".join(documents)

        return read_env_reference

    def _make_check_aks_config_tool(self) -> BaseTool:
        @tool
        def check_aks_deployment(namespace: str, deployment_name: str) -> str:
            """Check an AKS deployment's env vars, replicas, and config maps.
            Returns the actual configuration for comparison against expected values."""
            # In production, this calls Azure CLI or Kubernetes API
            # Placeholder for the tool structure
            return json.dumps({
                "status": "tool_not_connected",
                "message": f"Would check {namespace}/{deployment_name} via Azure CLI / kubectl",
                "instructions": "Connect Azure credentials and AKS cluster to enable live checks",
            })
        return check_aks_deployment

    def _make_check_appgw_tool(self) -> BaseTool:
        @tool
        def check_app_gateway(resource_group: str, gateway_name: str) -> str:
            """Check Azure Application Gateway backend pools and routing rules.
            Verifies backends point to PERF environment, not QC."""
            return json.dumps({
                "status": "tool_not_connected",
                "message": f"Would check {resource_group}/{gateway_name} via Azure CLI",
            })
        return check_app_gateway

    def _make_check_endpoint_tool(self) -> BaseTool:
        @tool
        def check_endpoint_health(url: str, expected_status: int = 200) -> str:
            """HTTP health check against an endpoint to verify connectivity.
            Tests that the PERF environment backends are reachable."""
            import httpx
            try:
                resp = httpx.get(url, timeout=10.0, verify=False)
                return json.dumps({
                    "url": url,
                    "status_code": resp.status_code,
                    "healthy": resp.status_code == expected_status,
                    "response_time_ms": resp.elapsed.total_seconds() * 1000,
                })
            except Exception as e:
                return json.dumps({"url": url, "healthy": False, "error": str(e)})
        return check_endpoint_health

    def _make_save_golden_config_tool(self) -> BaseTool:
        @tool
        def save_golden_config(config_json: str) -> str:
            """Save the validated golden configuration to Snowflake for daily drift checks."""
            from src.integrations.snowflake_client import SnowflakeClient
            sf = SnowflakeClient()
            config = json.loads(config_json)
            sf.save_golden_config(config)
            return f"Golden config saved: {config.get('config_id', 'unknown')}"
        return save_golden_config

    def get_system_prompt(self) -> str:
        return load_prompt("env_triage")

    def build_agent_input(self, state: PipelineState) -> str:
        systems = state.environment_requirements.get("systems_required", []) if state.environment_requirements else []
        return (
            f"Validate the PERF environment configuration for the following systems: "
            f"{', '.join(systems) if systems else 'all systems in the reference file'}.\n\n"
            f"1. Read the environment reference YAML\n"
            f"2. Check each application's configuration against expected PERF values\n"
            f"3. Flag any QC environment pointers\n"
            f"4. If all pass, save the golden config\n"
            f"5. Report all findings"
        )

    def parse_output(self, agent_result: dict[str, Any]) -> ConfigValidationReport:
        raw = agent_result.get("output", "")
        return self._parse_json_output(raw, ConfigValidationReport)

    def update_state(self, state: PipelineState, output: ConfigValidationReport) -> PipelineState:
        state.env_config_results = [r.model_dump() for r in output.results]
        state.config_mismatches = [
            r.model_dump() for r in output.results if not r.matches
        ]
        state.env_validation_passed = output.is_golden

        if output.is_golden:
            state.golden_config = {
                "config_id": self.generate_id("gc-"),
                "validation_run_id": output.run_id,
                "environment": output.environment,
                "application_configs": {
                    r.app_name: {r.field_name: r.actual_value}
                    for r in output.results if r.matches and r.actual_value
                },
            }

        return state

    def summarize_output(self, output: ConfigValidationReport) -> str:
        return (
            f"Environment: {output.environment}\n"
            f"Total checks: {output.total_checks}\n"
            f"Passed: {output.passed} | Failed: {output.failed} | Errors: {output.errors}\n"
            f"Golden config: {'YES' if output.is_golden else 'NO'}\n"
            f"Mismatches: {'; '.join(output.mismatches_summary) if output.mismatches_summary else 'None'}"
        )
