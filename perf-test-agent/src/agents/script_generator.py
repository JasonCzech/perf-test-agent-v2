"""Phase 4: Script & Data Creation Agent.

Generates performance test scripts and provisions bulk test data:
- VuGen C scripts for OPUS (Web HTTP/HTML protocol)
- JMeter .jmx test plans for REST/SOAP/Solace APIs
- Bulk data via SQL inserts, API provisioning, or data tools
- Validates scripts via syntax check and dry-run
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from src.agents.base_agent import BaseAgent
from src.config.settings import LLMTask
from src.models.pipeline_state import PipelinePhase, PipelineState
from src.models.script_gen import ScriptDataOutput
from src.prompts import load_prompt


class ScriptGeneratorAgent(BaseAgent[ScriptDataOutput]):
    """Phase 4 agent: test plan -> scripts + data."""

    phase = PipelinePhase.SCRIPT_DATA
    output_model = ScriptDataOutput
    llm_task = LLMTask.CODE_GENERATION  # GPT-4o for quality code generation

    def get_tools(self) -> list[BaseTool]:
        return [
            self._make_generate_vugen_tool(),
            self._make_generate_jmeter_tool(),
            self._make_validate_script_tool(),
            self._make_provision_data_tool(),
            self._make_read_template_tool(),
        ]

    def _make_generate_vugen_tool(self) -> BaseTool:
        @tool
        def generate_vugen_script(
            transaction_name: str,
            protocol: str,
            endpoints: str,
            correlation_rules: str = "",
            parameterization: str = "",
        ) -> str:
            """Generate a VuGen C script for LoadRunner Enterprise.
            protocol: web_http_html
            endpoints: comma-separated URLs
            correlation_rules: comma-separated correlation patterns
            parameterization: comma-separated parameter names"""
            script_dir = Path("./runs/scripts/vugen") / transaction_name
            script_dir.mkdir(parents=True, exist_ok=True)

            endpoint_list = [e.strip() for e in endpoints.split(",")]
            corr_list = [c.strip() for c in correlation_rules.split(",") if c.strip()]
            param_list = [p.strip() for p in parameterization.split(",") if p.strip()]

            # Generate VuGen C script structure
            action_c = _build_vugen_action(transaction_name, endpoint_list, corr_list, param_list)

            script_path = script_dir / "Action.c"
            script_path.write_text(action_c)

            # Generate globals.h and vuser_init/end
            (script_dir / "globals.h").write_text('#include "lrun.h"\n#include "web_api.h"\n')
            (script_dir / "vuser_init.c").write_text(
                'vuser_init()\n{\n\tweb_set_max_html_param_len("262144");\n\treturn 0;\n}\n'
            )
            (script_dir / "vuser_end.c").write_text('vuser_end()\n{\n\treturn 0;\n}\n')

            return json.dumps({
                "script_path": str(script_dir),
                "files": ["Action.c", "globals.h", "vuser_init.c", "vuser_end.c"],
                "transaction_name": transaction_name,
                "protocol": protocol,
            })
        return generate_vugen_script

    def _make_generate_jmeter_tool(self) -> BaseTool:
        @tool
        def generate_jmeter_script(
            transaction_name: str,
            protocol: str,
            endpoints: str,
            method: str = "GET",
            headers: str = "",
            body_template: str = "",
        ) -> str:
            """Generate a JMeter .jmx test plan.
            protocol: rest_json | soap_xml | solace_mq
            endpoints: comma-separated URLs
            method: HTTP method (GET, POST, PUT, DELETE)
            headers: key:value pairs, comma-separated
            body_template: request body template"""
            script_dir = Path("./runs/scripts/jmeter")
            script_dir.mkdir(parents=True, exist_ok=True)

            endpoint_list = [e.strip() for e in endpoints.split(",")]
            header_dict = {}
            if headers:
                for h in headers.split(","):
                    k, v = h.strip().split(":", 1)
                    header_dict[k.strip()] = v.strip()

            jmx_content = _build_jmeter_jmx(
                transaction_name, protocol, endpoint_list, method, header_dict, body_template
            )

            script_path = script_dir / f"{transaction_name}.jmx"
            script_path.write_text(jmx_content)

            return json.dumps({
                "script_path": str(script_path),
                "transaction_name": transaction_name,
                "protocol": protocol,
            })
        return generate_jmeter_script

    def _make_validate_script_tool(self) -> BaseTool:
        @tool
        def validate_script(script_path: str, harness: str) -> str:
            """Validate a generated script (syntax check + dry run attempt).
            harness: loadrunner | jmeter"""
            path = Path(script_path)
            if not path.exists():
                return json.dumps({"valid": False, "error": f"Script not found: {script_path}"})

            errors = []
            warnings = []

            if harness == "jmeter":
                content = path.read_text()
                if "<jmeterTestPlan" not in content:
                    errors.append("Missing jmeterTestPlan root element")
                if "<ThreadGroup" not in content:
                    errors.append("Missing ThreadGroup")
                if "HTTPSamplerProxy" not in content and "JMSSampler" not in content:
                    warnings.append("No HTTP or JMS sampler found")
            elif harness == "loadrunner":
                # Check for Action.c
                action_path = path / "Action.c" if path.is_dir() else path
                if action_path.exists():
                    content = action_path.read_text()
                    if "Action()" not in content:
                        errors.append("Missing Action() function")
                    if "lr_start_transaction" not in content:
                        errors.append("Missing lr_start_transaction")
                    if "lr_end_transaction" not in content:
                        errors.append("Missing lr_end_transaction")
                else:
                    errors.append("Action.c not found")

            return json.dumps({
                "script_path": str(script_path),
                "harness": harness,
                "syntax_valid": len(errors) == 0,
                "errors": errors,
                "warnings": warnings,
            })
        return validate_script

    def _make_provision_data_tool(self) -> BaseTool:
        @tool
        def provision_bulk_data(
            entity_type: str,
            quantity: int,
            method: str,
            target_system: str,
            sql_or_endpoint: str = "",
        ) -> str:
            """Provision bulk test data.
            method: sql_insert | api_provisioning | data_tool
            sql_or_endpoint: SQL template or API endpoint"""
            return json.dumps({
                "status": "placeholder",
                "entity_type": entity_type,
                "quantity": quantity,
                "method": method,
                "target_system": target_system,
                "message": f"Would provision {quantity} {entity_type} records via {method} on {target_system}",
            })
        return provision_bulk_data

    def _make_read_template_tool(self) -> BaseTool:
        @tool
        def read_script_template(template_name: str) -> str:
            """Read a script template file (vugen_rest.c, jmeter_rest.jmx, etc.)."""
            template_dir = Path(__file__).parent.parent.parent / "templates" / "scripts"
            template_path = template_dir / template_name
            if template_path.exists():
                return template_path.read_text()
            return f"Template not found: {template_name}"
        return read_script_template

    def get_system_prompt(self) -> str:
        return load_prompt("script_data")

    def build_agent_input(self, state: PipelineState) -> str:
        plan = json.dumps(state.test_plan, indent=2) if state.test_plan else "No test plan available"
        scenarios = json.dumps(state.test_scenarios[:5], indent=2)
        data_reqs = json.dumps(state.bulk_data_requirements, indent=2)

        return (
            f"Generate performance test scripts and provision bulk data based on:\n\n"
            f"Test Plan:\n{plan[:3000]}\n\n"
            f"Scenarios:\n{scenarios}\n\n"
            f"Data Requirements:\n{data_reqs}\n\n"
            f"For each transaction flow, generate the appropriate script "
            f"(VuGen for OPUS HTTP/HTML, JMeter for REST/SOAP/Solace). "
            f"Validate each script after generation. "
            f"Provision bulk data for any data requirements."
        )

    def parse_output(self, agent_result: dict[str, Any]) -> ScriptDataOutput:
        raw = agent_result.get("output", "")
        return self._parse_json_output(raw, ScriptDataOutput)

    def update_state(self, state: PipelineState, output: ScriptDataOutput) -> PipelineState:
        state.generated_scripts = [s.model_dump() for s in output.generated_scripts]
        state.bulk_data_status = {
            s.request_id: s.model_dump() for s in output.bulk_data_statuses
        }
        state.script_validation_results = [v.model_dump() for v in output.validation_results]
        return state

    def summarize_output(self, output: ScriptDataOutput) -> str:
        scripts = len(output.generated_scripts)
        valid = sum(1 for v in output.validation_results if v.overall_passed)
        data_ready = sum(1 for d in output.bulk_data_statuses if d.status == "completed")
        data_total = len(output.bulk_data_statuses)
        return (
            f"Scripts generated: {scripts} ({valid} passed validation)\n"
            f"Data provisioned: {data_ready}/{data_total}\n"
            f"Ready for execution: {'YES' if output.ready_for_execution else 'NO'}\n"
            f"Blockers: {'; '.join(output.blockers) if output.blockers else 'None'}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Script Generation Helpers
# ═══════════════════════════════════════════════════════════════════════

def _build_vugen_action(
    txn_name: str,
    endpoints: list[str],
    correlations: list[str],
    params: list[str],
) -> str:
    """Build a VuGen C Action.c file."""
    lines = ['#include "globals.h"', "", "Action()", "{"]

    # Correlation rules
    for corr in correlations:
        lines.append(f'\tweb_reg_save_param("{corr}",')
        lines.append(f'\t\t"LB={corr}=",')
        lines.append(f'\t\t"RB=&",')
        lines.append(f'\t\t"Ord=1",')
        lines.append(f'\t\tLAST);')
        lines.append("")

    # Transaction
    lines.append(f'\tlr_start_transaction("{txn_name}");')
    lines.append("")

    for i, endpoint in enumerate(endpoints):
        lines.append(f'\tweb_url("Step_{i+1}",')
        lines.append(f'\t\t"URL={endpoint}",')
        lines.append(f'\t\tLAST);')
        lines.append("")

    # Error check
    lines.append('\tif (web_get_int_property(HTTP_INFO_RETURN_CODE) >= 400) {')
    lines.append(f'\t\tlr_end_transaction("{txn_name}", LR_FAIL);')
    lines.append('\t\treturn -1;')
    lines.append('\t}')
    lines.append("")

    lines.append(f'\tlr_end_transaction("{txn_name}", LR_AUTO);')
    lines.append('\tlr_think_time(3);')
    lines.append("")
    lines.append("\treturn 0;")
    lines.append("}")

    return "\n".join(lines)


def _build_jmeter_jmx(
    txn_name: str,
    protocol: str,
    endpoints: list[str],
    method: str,
    headers: dict[str, str],
    body: str,
) -> str:
    """Build a JMeter .jmx test plan XML."""
    # Simplified JMX template — production version would be much more detailed
    header_xml = ""
    for k, v in headers.items():
        header_xml += f"""
                <elementProp name="{k}" elementType="Header">
                  <stringProp name="Header.name">{k}</stringProp>
                  <stringProp name="Header.value">{v}</stringProp>
                </elementProp>"""

    samplers_xml = ""
    for i, ep in enumerate(endpoints):
        # Parse URL into domain/path
        from urllib.parse import urlparse
        parsed = urlparse(ep)
        domain = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"

        samplers_xml += f"""
          <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy"
                           testname="{txn_name}_Step{i+1}" enabled="true">
            <stringProp name="HTTPSampler.domain">{domain}</stringProp>
            <stringProp name="HTTPSampler.port">{port}</stringProp>
            <stringProp name="HTTPSampler.protocol">{parsed.scheme or 'https'}</stringProp>
            <stringProp name="HTTPSampler.path">{path}</stringProp>
            <stringProp name="HTTPSampler.method">{method}</stringProp>
            <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
            {"<stringProp name='HTTPSampler.postBodyRaw'>" + body + "</stringProp>" if body else ""}
          </HTTPSamplerProxy>
          <hashTree/>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="{txn_name} Test Plan">
      <boolProp name="TestPlan.functional_mode">false</boolProp>
    </TestPlan>
    <hashTree>
      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="{txn_name}_Users">
        <stringProp name="ThreadGroup.num_threads">${{__P(users,10)}}</stringProp>
        <stringProp name="ThreadGroup.ramp_time">${{__P(rampup,60)}}</stringProp>
        <stringProp name="ThreadGroup.duration">${{__P(duration,300)}}</stringProp>
        <boolProp name="ThreadGroup.scheduler">true</boolProp>
      </ThreadGroup>
      <hashTree>
        <HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="Headers">
          <collectionProp name="HeaderManager.headers">{header_xml}
          </collectionProp>
        </HeaderManager>
        <hashTree/>{samplers_xml}
        <ConstantTimer guiclass="ConstantTimerGui" testclass="ConstantTimer" testname="Think Time">
          <stringProp name="ConstantTimer.delay">3000</stringProp>
        </ConstantTimer>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>"""
