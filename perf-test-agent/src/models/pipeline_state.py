"""Pipeline state and shared models.

This module defines the central PipelineState that flows through all phases,
plus shared enumerations and base models used across the pipeline.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════
# Enumerations
# ═══════════════════════════════════════════════════════════════════════

class PipelinePhase(str, Enum):
    STORY_ANALYSIS = "story_analysis"
    TEST_PLANNING = "test_planning"
    ENV_TRIAGE = "env_triage"
    SCRIPT_DATA = "script_data"
    EXECUTION = "execution"
    REPORTING = "reporting"
    POSTMORTEM = "postmortem"


class PhaseStatus(str, Enum):
    PENDING = "pending"
    PROMPT_REVIEW = "prompt_review"
    RUNNING = "running"
    RESULTS_READY = "results_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Protocol(str, Enum):
    REST_JSON = "rest_json"
    SOAP_XML = "soap_xml"
    WEB_HTTP_HTML = "web_http_html"
    TRUCLIENT = "truclient"
    SOLACE_MQ = "solace_mq"
    IBM_MQ = "ibm_mq"
    KAFKA = "kafka"
    JDBC = "jdbc"


class TestHarness(str, Enum):
    LOADRUNNER = "loadrunner_enterprise"
    JMETER = "jmeter"
    K6 = "k6"  # Future


class TestType(str, Enum):
    LOAD = "load"
    STRESS = "stress"
    ENDURANCE = "endurance"
    SPIKE = "spike"
    BREAKPOINT = "breakpoint"


class SystemTier(str, Enum):
    FRONTEND = "frontend"
    MIDDLEWARE = "middleware"
    BACKEND = "backend"
    PERIPHERY = "periphery"


# ═══════════════════════════════════════════════════════════════════════
# Shared Base Models
# ═══════════════════════════════════════════════════════════════════════

class SystemComponent(BaseModel):
    """A system component in the AT&T environment."""
    name: str
    tier: SystemTier
    protocols: list[Protocol] = []
    description: str = ""
    is_directly_tested: bool = True
    environment_endpoints: dict[str, str] = {}  # env_name -> url


class SLATarget(BaseModel):
    """A Service Level Agreement target for a transaction."""
    transaction_name: str
    response_time_p90_ms: int
    response_time_p95_ms: Optional[int] = None
    response_time_p99_ms: Optional[int] = None
    error_rate_threshold_pct: float = 1.0
    throughput_tps_target: Optional[float] = None


class PhaseResult(BaseModel):
    """Result of a single pipeline phase execution."""
    phase: PipelinePhase
    status: PhaseStatus
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    artifacts: dict[str, Any] = {}  # artifact_name -> path or data
    errors: list[str] = []
    warnings: list[str] = []
    summary: str = ""
    approved_by: Optional[str] = None
    approval_notes: str = ""
    reasoning_trace: list[dict[str, Any]] = []
    tool_calls_summary: dict[str, int] = {}


# ═══════════════════════════════════════════════════════════════════════
# Pipeline State — the central data structure flowing through all phases
# ═══════════════════════════════════════════════════════════════════════

class PipelineState(BaseModel):
    """Central state object that accumulates data as the pipeline progresses.

    Each phase reads from and writes to this state.  It is persisted to disk
    between phases so the pipeline can be resumed after HITL approval.
    """
    # ── Identity ──────────────────────────────────────────────────────
    run_id: str = Field(description="Unique pipeline run identifier")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Source ────────────────────────────────────────────────────────
    jira_story_keys: list[str] = Field(default_factory=list)
    sprint_name: Optional[str] = None
    context_ids: list[str] = Field(default_factory=list)
    selected_app_id: Optional[str] = None
    selected_app_name: Optional[str] = None
    selected_app_reference_key: Optional[str] = None
    generated_summary: Optional[dict[str, Any]] = None
    user_artifacts: Optional[dict[str, Any]] = None
    phase_prompt_overrides: dict[str, str] = {}
    phase_pre_execution_context: dict[str, str] = {}
    phase_post_execution_notes: dict[str, str] = {}
    source_documents: list[str] = []  # RAG document references

    # ── Phase Results ─────────────────────────────────────────────────
    current_phase: PipelinePhase = PipelinePhase.STORY_ANALYSIS
    phase_results: dict[str, PhaseResult] = {}

    # ── Phase 1: Story Analysis outputs ───────────────────────────────
    test_cases: list[dict[str, Any]] = []       # Full TestCase models (see test_case.py)
    sla_targets: list[SLATarget] = []
    risk_profiles: list[dict[str, Any]] = []
    transaction_flows: list[dict[str, Any]] = []
    story_analysis_optimization_history: list[dict[str, Any]] = []
    story_analysis_optimization_meta: dict[str, Any] = {}

    # ── Phase 2: Test Planning outputs ────────────────────────────────
    test_plan: Optional[dict[str, Any]] = None  # Full TestPlan model
    workload_model: Optional[dict[str, Any]] = None
    test_scenarios: list[dict[str, Any]] = []
    environment_requirements: dict[str, Any] = {}
    bulk_data_requirements: list[dict[str, Any]] = []

    # ── Phase 3: Env Triage outputs ───────────────────────────────────
    env_config_results: list[dict[str, Any]] = []
    golden_config: Optional[dict[str, Any]] = None
    config_mismatches: list[dict[str, Any]] = []
    env_validation_passed: bool = False

    # ── Phase 4: Script & Data outputs ────────────────────────────────
    generated_scripts: list[dict[str, Any]] = []  # path, harness, protocol
    bulk_data_status: dict[str, Any] = {}
    script_validation_results: list[dict[str, Any]] = []

    # ── Phase 5: Execution outputs ────────────────────────────────────
    execution_runs: list[dict[str, Any]] = []
    peak_point_results: Optional[dict[str, Any]] = None
    breakpoint_results: Optional[dict[str, Any]] = None
    stability_results: Optional[dict[str, Any]] = None
    anomalies: list[dict[str, Any]] = []

    # ── Phase 6: Reporting outputs ────────────────────────────────────
    report_path: Optional[str] = None
    sharepoint_url: Optional[str] = None
    jira_defect_keys: list[str] = []
    results_analysis_optimization_history: list[dict[str, Any]] = []
    results_analysis_optimization_meta: dict[str, Any] = {}

    # ── Phase 7: Postmortem outputs ───────────────────────────────────
    postmortem_entries: list[dict[str, Any]] = []
    lessons_learned: list[str] = []
    feedback_indexed: bool = False

    def get_phase_result(self, phase: PipelinePhase) -> Optional[PhaseResult]:
        return self.phase_results.get(phase.value)

    def set_phase_result(self, result: PhaseResult) -> None:
        self.phase_results[result.phase.value] = result
        self.updated_at = datetime.utcnow()
