import json
from pathlib import Path

import pytest

from src.agents.results_analyzer import ResultsAnalyzerAgent
from src.models.analysis import AnalysisReportEvaluation
from src.models.pipeline_state import PhaseStatus, PipelineState


class DummySettings:
    def __init__(self, run_dir: Path, max_iterations: int = 5) -> None:
        self.pipeline_run_dir = run_dir
        self.results_analysis_optimizer_enabled = True
        self.results_analysis_optimizer_max_iterations = max_iterations
        self.results_analysis_optimizer_score_threshold = 0.85
        self.results_analysis_optimizer_min_improvement_delta = 0.02


def _analysis_report_json(run_id: str, title: str = "Phase 6 Report") -> str:
    payload = {
        "report_id": f"RPT-{run_id}",
        "pipeline_run_id": run_id,
        "title": title,
        "executive_summary": "SLA mostly compliant with one major defect.",
        "test_scope": "Checkout and order flows",
        "sla_compliance": [
            {
                "transaction_name": "Checkout_Create_Order",
                "sla_p90_ms": 500,
                "actual_p90_ms": 520,
                "sla_error_rate_pct": 1.0,
                "actual_error_rate_pct": 0.8,
                "compliant": False,
                "deviation_pct": 4.0,
            }
        ],
        "overall_sla_pass": False,
        "baseline_comparisons": [
            {
                "transaction_name": "Checkout_Create_Order",
                "current_p90_ms": 520,
                "baseline_p90_ms": 500,
                "delta_pct": 4.0,
                "trend": "stable",
            }
        ],
        "peak_point_summary": "Sustained 120 TPS",
        "breakpoint_summary": "Failures started at 140 TPS",
        "stability_summary": "Minor p90 drift over endurance window",
        "defects": [
            {
                "defect_id": "DEF-1",
                "title": "Checkout latency above SLA",
                "description": "P90 exceeds target under load",
                "severity": "major",
                "affected_system": "order_graph",
                "affected_transaction": "Checkout_Create_Order",
                "observed_value": "520ms",
                "expected_value": "<=500ms",
                "evidence": ["chart://latency"],
                "recommended_action": "Tune query plan",
                "assigned_team": "orders",
                "jira_key": "TELECOM-999",
            }
        ],
        "total_defects": 1,
        "blockers": 0,
        "word_report_path": "./runs/report.docx",
        "sharepoint_url": "https://example.sharepoint.com/report",
        "recommendations": ["Tune DB indexes", "Increase connection pool"],
        "go_no_go": "conditional",
    }
    return json.dumps(payload)


@pytest.mark.asyncio
async def test_results_optimizer_stops_on_threshold(tmp_path: Path) -> None:
    agent = ResultsAnalyzerAgent(settings=DummySettings(tmp_path))
    agent._save_artifacts = lambda output: None

    async def fake_execute(agent_input: str, executor=None):
        return {"output": _analysis_report_json("run-r1")}

    async def fake_evaluate(output, threshold: float):
        return AnalysisReportEvaluation(
            score=0.91,
            pass_threshold=True,
            strengths=["Complete report"],
            gaps=[],
            actionable_feedback=["No change needed"],
            stop_reason_hint="",
        )

    agent._execute_agent = fake_execute
    agent._evaluate_output = fake_evaluate

    state = PipelineState(run_id="run-r1", jira_story_keys=["TELECOM-1"])
    updated = await agent.run(state)

    meta = updated.results_analysis_optimization_meta
    assert meta["iterations_run"] == 1
    assert meta["stop_reason"] == "threshold_met"
    assert updated.results_analysis_optimization_history[0]["score"] == 0.91
    assert updated.get_phase_result(agent.phase).status == PhaseStatus.AWAITING_APPROVAL


@pytest.mark.asyncio
async def test_results_optimizer_stops_on_no_improvement(tmp_path: Path) -> None:
    agent = ResultsAnalyzerAgent(settings=DummySettings(tmp_path))
    agent._save_artifacts = lambda output: None

    outputs = [
        {"output": _analysis_report_json("run-r2", title="Iteration 1")},
        {"output": _analysis_report_json("run-r2", title="Iteration 2")},
    ]

    async def fake_execute(agent_input: str, executor=None):
        return outputs.pop(0)

    evaluations = [
        AnalysisReportEvaluation(
            score=0.80,
            pass_threshold=False,
            strengths=["Good structure"],
            gaps=["Need stronger rationale"],
            actionable_feedback=["Expand go/no-go rationale"],
            stop_reason_hint="",
        ),
        AnalysisReportEvaluation(
            score=0.81,
            pass_threshold=False,
            strengths=["Better rationale"],
            gaps=["Still limited baseline analysis"],
            actionable_feedback=["Add more baseline context"],
            stop_reason_hint="",
        ),
    ]

    async def fake_evaluate(output, threshold: float):
        return evaluations.pop(0)

    agent._execute_agent = fake_execute
    agent._evaluate_output = fake_evaluate

    state = PipelineState(run_id="run-r2", jira_story_keys=["TELECOM-2"])
    updated = await agent.run(state)

    meta = updated.results_analysis_optimization_meta
    assert meta["iterations_run"] == 2
    assert meta["stop_reason"] == "no_improvement"


@pytest.mark.asyncio
async def test_results_optimizer_respects_max_iterations(tmp_path: Path) -> None:
    agent = ResultsAnalyzerAgent(settings=DummySettings(tmp_path, max_iterations=2))
    agent._save_artifacts = lambda output: None

    outputs = [
        {"output": _analysis_report_json("run-r3", title="Iteration 1")},
        {"output": _analysis_report_json("run-r3", title="Iteration 2")},
    ]

    async def fake_execute(agent_input: str, executor=None):
        return outputs.pop(0)

    evaluations = [
        AnalysisReportEvaluation(
            score=0.70,
            pass_threshold=False,
            strengths=["Has key sections"],
            gaps=["Needs better defect detail"],
            actionable_feedback=["Increase defect evidence detail"],
            stop_reason_hint="",
        ),
        AnalysisReportEvaluation(
            score=0.79,
            pass_threshold=False,
            strengths=["Improved defect section"],
            gaps=["Still below threshold"],
            actionable_feedback=["Improve SLA narrative"],
            stop_reason_hint="",
        ),
    ]

    async def fake_evaluate(output, threshold: float):
        return evaluations.pop(0)

    agent._execute_agent = fake_execute
    agent._evaluate_output = fake_evaluate

    state = PipelineState(run_id="run-r3", jira_story_keys=["TELECOM-3"])
    updated = await agent.run(state)

    meta = updated.results_analysis_optimization_meta
    assert meta["iterations_run"] == 2
    assert meta["stop_reason"] == "max_iterations_reached"


@pytest.mark.asyncio
async def test_results_optimizer_handles_evaluator_failure(tmp_path: Path) -> None:
    agent = ResultsAnalyzerAgent(settings=DummySettings(tmp_path))
    agent._save_artifacts = lambda output: None

    async def fake_execute(agent_input: str, executor=None):
        return {"output": _analysis_report_json("run-r4")}

    async def fake_evaluate(output, threshold: float):
        raise RuntimeError("bad evaluator response")

    agent._execute_agent = fake_execute
    agent._evaluate_output = fake_evaluate

    state = PipelineState(run_id="run-r4", jira_story_keys=["TELECOM-4"])
    updated = await agent.run(state)

    meta = updated.results_analysis_optimization_meta
    assert meta["stop_reason"] == "evaluator_failure"
    phase_result = updated.get_phase_result(agent.phase)
    assert phase_result.status == PhaseStatus.AWAITING_APPROVAL
    assert updated.results_analysis_optimization_history[-1]["stop_reason"] == "evaluator_failure"
    assert "bad evaluator response" in updated.results_analysis_optimization_history[-1]["gaps"][0]
