import json
from pathlib import Path

import pytest

from src.agents.story_analyzer import StoryAnalyzerAgent
from src.models.pipeline_state import PhaseStatus, PipelineState
from src.models.test_case import StoryAnalysisEvaluation


class DummySettings:
    def __init__(self, run_dir: Path, max_iterations: int = 5) -> None:
        self.pipeline_run_dir = run_dir
        self.story_analysis_optimizer_enabled = True
        self.story_analysis_optimizer_max_iterations = max_iterations
        self.story_analysis_optimizer_score_threshold = 0.8
        self.story_analysis_optimizer_min_improvement_delta = 0.02


def _story_output_json(story_key: str, confidence: float = 0.6) -> str:
    payload = {
        "analyzed_stories": [story_key],
        "test_cases": [
            {
                "id": f"TC-{story_key}-1",
                "source_story_key": story_key,
                "source_story_summary": "Checkout performance",
                "title": "Checkout API load",
                "description": "Validate checkout under load",
                "transaction_flows": [
                    {
                        "name": "Checkout_Create_Order",
                        "description": "Create order via API",
                        "transaction_type": "create",
                        "protocol": "rest_json",
                        "entry_system": "app_gateway",
                        "systems_involved": ["app_gateway", "order_graph"],
                        "api_endpoints": ["POST /orders"],
                        "expected_sequence": ["app_gateway -> order_graph"],
                        "has_async_component": False,
                        "mq_topics": [],
                    }
                ],
                "sla_targets": [
                    {
                        "transaction_name": "Checkout_Create_Order",
                        "response_time_p90_ms": 500,
                        "response_time_p95_ms": 800,
                        "response_time_p99_ms": 1200,
                        "error_rate_threshold_pct": 1.0,
                        "throughput_tps_target": 45,
                    }
                ],
                "risk_level": "high",
                "risk_rationale": "Order path is business critical",
                "recommended_harness": "loadrunner_enterprise",
                "harness_rationale": "Complex backend chain",
                "recommended_protocol": "rest_json",
                "data_requirements": [],
                "preconditions": [],
                "tags": ["checkout"],
                "related_incidents": [],
                "historical_baselines": {"Checkout_Create_Order": 430},
            }
        ],
        "sla_targets": [
            {
                "transaction_name": "Checkout_Create_Order",
                "response_time_p90_ms": 500,
                "response_time_p95_ms": 800,
                "response_time_p99_ms": 1200,
                "error_rate_threshold_pct": 1.0,
                "throughput_tps_target": 45,
            }
        ],
        "risk_summary": "High risk around checkout latency",
        "rag_documents_consulted": ["doc://checkout-wiki"],
        "confidence_score": confidence,
        "warnings": [],
        "suggested_followups": [],
    }
    return json.dumps(payload)


@pytest.mark.asyncio
async def test_optimizer_stops_on_threshold(tmp_path: Path) -> None:
    agent = StoryAnalyzerAgent(settings=DummySettings(tmp_path))
    agent._save_artifacts = lambda output: None

    async def fake_execute(agent_input: str, executor=None):
        return {"output": _story_output_json("TELECOM-1", confidence=0.65)}

    async def fake_evaluate(output, threshold: float):
        return StoryAnalysisEvaluation(
            score=0.91,
            pass_threshold=True,
            strengths=["Good coverage"],
            gaps=[],
            actionable_feedback=["No changes needed"],
            stop_reason_hint="",
        )

    agent._execute_agent = fake_execute
    agent._evaluate_output = fake_evaluate

    state = PipelineState(run_id="run-1", jira_story_keys=["TELECOM-1"])
    updated = await agent.run(state)

    meta = updated.story_analysis_optimization_meta
    assert meta["iterations_run"] == 1
    assert meta["stop_reason"] == "threshold_met"
    assert updated.story_analysis_optimization_history[0]["score"] == 0.91
    assert updated.get_phase_result(agent.phase).status == PhaseStatus.AWAITING_APPROVAL


@pytest.mark.asyncio
async def test_optimizer_stops_on_no_improvement(tmp_path: Path) -> None:
    agent = StoryAnalyzerAgent(settings=DummySettings(tmp_path))
    agent._save_artifacts = lambda output: None

    outputs = [
        {"output": _story_output_json("TELECOM-2", confidence=0.50)},
        {"output": _story_output_json("TELECOM-2", confidence=0.55)},
    ]

    async def fake_execute(agent_input: str, executor=None):
        return outputs.pop(0)

    evaluations = [
        StoryAnalysisEvaluation(
            score=0.60,
            pass_threshold=False,
            strengths=["Baseline set"],
            gaps=["Missing edge flow"],
            actionable_feedback=["Add one more transaction flow"],
            stop_reason_hint="",
        ),
        StoryAnalysisEvaluation(
            score=0.61,
            pass_threshold=False,
            strengths=["Added some detail"],
            gaps=["Still limited coverage"],
            actionable_feedback=["Expand risk rationale"],
            stop_reason_hint="",
        ),
    ]

    async def fake_evaluate(output, threshold: float):
        return evaluations.pop(0)

    agent._execute_agent = fake_execute
    agent._evaluate_output = fake_evaluate

    state = PipelineState(run_id="run-2", jira_story_keys=["TELECOM-2"])
    updated = await agent.run(state)

    meta = updated.story_analysis_optimization_meta
    assert meta["iterations_run"] == 2
    assert meta["stop_reason"] == "no_improvement"


@pytest.mark.asyncio
async def test_optimizer_respects_max_iterations(tmp_path: Path) -> None:
    agent = StoryAnalyzerAgent(settings=DummySettings(tmp_path, max_iterations=2))
    agent._save_artifacts = lambda output: None

    outputs = [
        {"output": _story_output_json("TELECOM-3", confidence=0.40)},
        {"output": _story_output_json("TELECOM-3", confidence=0.45)},
    ]

    async def fake_execute(agent_input: str, executor=None):
        return outputs.pop(0)

    evaluations = [
        StoryAnalysisEvaluation(
            score=0.50,
            pass_threshold=False,
            strengths=["Started coverage"],
            gaps=["SLA lacks detail"],
            actionable_feedback=["Refine SLA targets"],
            stop_reason_hint="",
        ),
        StoryAnalysisEvaluation(
            score=0.58,
            pass_threshold=False,
            strengths=["Better SLAs"],
            gaps=["Risk rationale can improve"],
            actionable_feedback=["Improve risk rationale"],
            stop_reason_hint="",
        ),
    ]

    async def fake_evaluate(output, threshold: float):
        return evaluations.pop(0)

    agent._execute_agent = fake_execute
    agent._evaluate_output = fake_evaluate

    state = PipelineState(run_id="run-3", jira_story_keys=["TELECOM-3"])
    updated = await agent.run(state)

    meta = updated.story_analysis_optimization_meta
    assert meta["iterations_run"] == 2
    assert meta["stop_reason"] == "max_iterations_reached"


@pytest.mark.asyncio
async def test_optimizer_handles_evaluator_failure(tmp_path: Path) -> None:
    agent = StoryAnalyzerAgent(settings=DummySettings(tmp_path))
    agent._save_artifacts = lambda output: None

    async def fake_execute(agent_input: str, executor=None):
        return {"output": _story_output_json("TELECOM-4", confidence=0.62)}

    async def fake_evaluate(output, threshold: float):
        raise RuntimeError("bad evaluator response")

    agent._execute_agent = fake_execute
    agent._evaluate_output = fake_evaluate

    state = PipelineState(run_id="run-4", jira_story_keys=["TELECOM-4"])
    updated = await agent.run(state)

    meta = updated.story_analysis_optimization_meta
    assert meta["stop_reason"] == "evaluator_failure"
    phase_result = updated.get_phase_result(agent.phase)
    assert phase_result.status == PhaseStatus.AWAITING_APPROVAL
    assert updated.story_analysis_optimization_history[-1]["stop_reason"] == "evaluator_failure"
    assert "bad evaluator response" in updated.story_analysis_optimization_history[-1]["gaps"][0]
