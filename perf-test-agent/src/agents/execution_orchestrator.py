"""Phase 5: Execution & Monitoring Agent.

Orchestrates test execution across LoadRunner Enterprise and JMeter:
- Pre-flight config validation (calls Phase 3 golden config check)
- Triggers tests via LRE REST API or Jenkins
- Monitors via Dynatrace, Prometheus, ELK in real-time
- Identifies Peak Point, Breakpoint, and runs Stability tests
- Detects anomalies and routes to appropriate teams
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool

from src.agents.base_agent import BaseAgent
from src.config.settings import LLMTask
from src.models.execution import ExecutionOutput
from src.models.pipeline_state import PipelinePhase, PipelineState
from src.tools.langchain_tools import (
    make_dynatrace_tools,
    make_execution_tools,
    make_jira_tools,
    make_monitoring_tools,
)


class ExecutionOrchestratorAgent(BaseAgent[ExecutionOutput]):
    """Phase 5 agent: scripts + config -> execute + monitor -> results."""

    phase = PipelinePhase.EXECUTION
    output_model = ExecutionOutput
    llm_task = LLMTask.COMPLEX_REASONING

    def get_tools(self) -> list[BaseTool]:
        return (
            make_execution_tools()
            + make_dynatrace_tools()
            + make_monitoring_tools()
            + make_jira_tools()
        )

    def get_system_prompt(self) -> str:
        return """You are a performance test execution specialist at AT&T. Your job is to
execute performance tests and monitor the system in real-time.

## Execution Strategy
Execute tests in this order:
1. **Pre-flight Check**: Verify env config hasn't drifted (check golden config)
2. **Load Test**: Run at target TPS, verify SLA compliance
3. **Stress/Breakpoint Test**: Gradually increase load until:
   - Error rate exceeds 5% (BREAKPOINT), OR
   - Response times violate SLA by >200%, OR
   - System becomes unresponsive
4. **Stability Test**: Run at identified Peak Point for 1-4 hours

## Peak Point Definition
Maximum TPS that is achieved while ALL of:
- p90 response time meets SLA
- Error rate < 1%
- No resource exhaustion (CPU < 85%, memory < 85%, JVM heap < 80%)

## Breakpoint Definition
The point where:
- Error rate > 5%, OR
- Timeout rate > 10%, OR
- HTTP 503/Connection Refused errors dominate

## Monitoring During Tests
Continuously monitor:
1. Dynatrace: Service response times, error rates, active problems
2. Prometheus: CPU, memory, JVM heap, GC pauses, MQ queue depths
3. ELK: Application error logs (OOM, connection refused, circuit breaker)

## Anomaly Detection
When anomaly detected:
1. Log it with severity (INFO/WARNING/ERROR/CRITICAL)
2. If CRITICAL: Consider aborting the test
3. Create Jira defect for ERROR/CRITICAL anomalies
4. Route to appropriate team based on affected system

## Routing Rules
- AKS/container issues -> Platform Engineering
- Amdocs (TLG/BSSe/OMS) issues -> Amdocs Support
- Database issues -> DBA team
- MQ/messaging issues -> Middleware team
- Network/Gateway issues -> Network Operations

Your Final Answer MUST be valid JSON matching the ExecutionOutput schema."""

    def build_agent_input(self, state: PipelineState) -> str:
        scripts = json.dumps(state.generated_scripts[:5], indent=2)
        scenarios = json.dumps(state.test_scenarios[:3], indent=2)
        sla_targets = json.dumps([s.model_dump() for s in state.sla_targets], indent=2)
        golden = json.dumps(state.golden_config, indent=2) if state.golden_config else "No golden config"

        return (
            f"Execute performance tests:\n\n"
            f"Scripts ready:\n{scripts}\n\n"
            f"Scenarios:\n{scenarios}\n\n"
            f"SLA Targets:\n{sla_targets}\n\n"
            f"Golden Config:\n{golden}\n\n"
            f"Execute in order: Load -> Stress/Breakpoint -> Stability.\n"
            f"Monitor continuously via Dynatrace, Prometheus, and ELK.\n"
            f"Identify Peak Point and Breakpoint.\n"
            f"Log anomalies and create Jira defects for ERROR/CRITICAL issues."
        )

    def parse_output(self, agent_result: dict[str, Any]) -> ExecutionOutput:
        raw = agent_result.get("output", "")
        return self._parse_json_output(raw, ExecutionOutput)

    def update_state(self, state: PipelineState, output: ExecutionOutput) -> PipelineState:
        state.execution_runs = [r.model_dump() for r in output.test_runs]
        state.peak_point_results = output.peak_point.model_dump()
        state.breakpoint_results = output.breakpoint.model_dump()
        state.stability_results = output.stability.model_dump()
        state.anomalies = [a.model_dump() for a in output.all_anomalies]
        return state

    def summarize_output(self, output: ExecutionOutput) -> str:
        runs = len(output.test_runs)
        anomalies_critical = sum(1 for a in output.all_anomalies if a.severity.value in ("error", "critical"))
        sla_pass = sum(1 for v in output.sla_compliance_summary.values() if v)
        sla_total = len(output.sla_compliance_summary)

        lines = [
            f"Test runs completed: {runs}",
            f"Peak Point: {output.peak_point.peak_tps:.1f} TPS @ {output.peak_point.vusers_at_peak} VUsers"
            if output.peak_point.identified else "Peak Point: Not identified",
            f"Breakpoint: {output.breakpoint.breakpoint_tps:.1f} TPS ({output.breakpoint.primary_failure_mode})"
            if output.breakpoint.identified else "Breakpoint: Not identified",
            f"Stability: {'PASSED' if output.stability.passed else 'FAILED'} ({output.stability.duration_hours:.1f}h)",
            f"SLA Compliance: {sla_pass}/{sla_total}",
            f"Critical/Error anomalies: {anomalies_critical}",
        ]
        return "\n".join(lines)
