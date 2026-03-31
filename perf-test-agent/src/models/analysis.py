"""Phase 6: Results Analysis & Reporting + Phase 7: Postmortem models."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .pipeline_state import RiskLevel


# ═══════════════════════════════════════════════════════════════════════
# Phase 6: Results Analysis & Reporting
# ═══════════════════════════════════════════════════════════════════════

class DefectSeverity(str, Enum):
    BLOCKER = "blocker"
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class PerformanceDefect(BaseModel):
    """A performance defect to be logged in Jira."""
    defect_id: str
    title: str
    description: str
    severity: DefectSeverity
    affected_system: str
    affected_transaction: str
    observed_value: str
    expected_value: str
    evidence: list[str] = Field(description="Paths to screenshots, logs, charts")
    recommended_action: str = ""
    assigned_team: str = ""
    jira_key: Optional[str] = None  # Populated after Jira creation


class SLAComplianceEntry(BaseModel):
    """SLA compliance result for a single transaction."""
    transaction_name: str
    sla_p90_ms: float
    actual_p90_ms: float
    sla_error_rate_pct: float
    actual_error_rate_pct: float
    compliant: bool
    deviation_pct: float = 0.0  # How far off from SLA (negative = better than SLA)


class ComparisonBaseline(BaseModel):
    """Comparison against historical baselines from Snowflake."""
    transaction_name: str
    current_p90_ms: float
    baseline_p90_ms: float
    delta_pct: float
    trend: str = "stable"  # improving | stable | degrading


class AnalysisReport(BaseModel):
    """Complete analysis report — output of Phase 6."""
    report_id: str
    pipeline_run_id: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    title: str
    executive_summary: str
    test_scope: str

    # Results
    sla_compliance: list[SLAComplianceEntry]
    overall_sla_pass: bool = False
    baseline_comparisons: list[ComparisonBaseline] = []
    peak_point_summary: str = ""
    breakpoint_summary: str = ""
    stability_summary: str = ""

    # Defects
    defects: list[PerformanceDefect] = []
    total_defects: int = 0
    blockers: int = 0

    # Artifacts
    word_report_path: Optional[str] = None
    sharepoint_url: Optional[str] = None

    # Recommendations
    recommendations: list[str] = []
    go_no_go: str = "pending"  # go | no_go | conditional | pending


class AnalysisReportEvaluation(BaseModel):
    """Evaluator output for an AnalysisReport candidate."""
    score: float = Field(ge=0.0, le=1.0, description="Overall quality score for this iteration")
    pass_threshold: bool = Field(description="Whether this iteration meets evaluator quality threshold")
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    actionable_feedback: list[str] = Field(
        default_factory=list,
        description="Specific instructions to improve the next optimization iteration"
    )
    stop_reason_hint: str = Field(
        default="",
        description="Optional evaluator hint for stopping optimization"
    )


class ResultsAnalysisOptimizationIteration(BaseModel):
    """Captured data for one optimization iteration of Results Analysis."""
    iteration: int = Field(ge=1)
    score: float = Field(ge=0.0, le=1.0)
    score_delta: float = 0.0
    accepted_as_best: bool = False
    stop_reason: str = ""
    feedback: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    generated_summary: str = ""


# ═══════════════════════════════════════════════════════════════════════
# Phase 7: Postmortem & Feedback
# ═══════════════════════════════════════════════════════════════════════

class PainPointCategory(str, Enum):
    ENVIRONMENT = "environment"
    DATA = "data"
    TOOLING = "tooling"
    PROCESS = "process"
    COMMUNICATION = "communication"
    TECHNICAL = "technical"


class PostmortemEntry(BaseModel):
    """A single postmortem observation logged during the test cycle."""
    entry_id: str
    phase: str  # Which pipeline phase this occurred in
    category: PainPointCategory
    title: str
    description: str
    impact: str
    resolution: str = ""
    resolved: bool = False
    time_lost_hours: float = 0.0
    logged_at: datetime = Field(default_factory=datetime.utcnow)


class LessonLearned(BaseModel):
    """A structured lesson for the RAG knowledge base."""
    lesson_id: str
    title: str
    context: str = Field(description="When does this lesson apply?")
    lesson: str = Field(description="What was learned?")
    recommendation: str = Field(description="What should be done differently?")
    applicable_systems: list[str] = []
    applicable_phases: list[str] = []
    tags: list[str] = []


class PostmortemOutput(BaseModel):
    """Complete output of Phase 7: Postmortem."""
    pipeline_run_id: str
    completed_at: datetime = Field(default_factory=datetime.utcnow)
    entries: list[PostmortemEntry]
    lessons_learned: list[LessonLearned]
    total_time_lost_hours: float = 0.0
    top_pain_points: list[str] = []
    process_improvements: list[str] = []
    snowflake_archived: bool = False
    rag_indexed: bool = False
