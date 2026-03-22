"""Phase 5: Execution & Monitoring models.

Defines test run state, real-time monitoring data, anomaly detection,
and peak/break/stability point results.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .pipeline_state import TestType


class RunStatus(str, Enum):
    INITIALIZING = "initializing"
    RAMPING_UP = "ramping_up"
    STEADY_STATE = "steady_state"
    RAMPING_DOWN = "ramping_down"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class AnomalySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Anomaly(BaseModel):
    """An anomaly detected during test execution."""
    anomaly_id: str
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    severity: AnomalySeverity
    category: str = Field(description="e.g. 'error_spike', 'response_degradation', 'resource_exhaustion'")
    description: str
    affected_transaction: Optional[str] = None
    affected_system: Optional[str] = None
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    threshold: Optional[float] = None
    source: str = Field(description="dynatrace | prometheus | elk | lre | jmeter")
    routed_to_team: Optional[str] = None
    jira_defect_key: Optional[str] = None
    resolved: bool = False
    resolution_notes: str = ""


class TransactionMetrics(BaseModel):
    """Aggregated metrics for a single transaction during a run."""
    transaction_name: str
    total_requests: int = 0
    passed: int = 0
    failed: int = 0
    error_rate_pct: float = 0.0
    avg_response_time_ms: float = 0.0
    p50_response_time_ms: float = 0.0
    p90_response_time_ms: float = 0.0
    p95_response_time_ms: float = 0.0
    p99_response_time_ms: float = 0.0
    min_response_time_ms: float = 0.0
    max_response_time_ms: float = 0.0
    tps_achieved: float = 0.0
    sla_met: bool = False


class ResourceMetrics(BaseModel):
    """Infrastructure resource metrics snapshot."""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    system_name: str
    cpu_pct: Optional[float] = None
    memory_pct: Optional[float] = None
    jvm_heap_pct: Optional[float] = None
    gc_pause_ms: Optional[float] = None
    thread_count: Optional[int] = None
    db_connection_pool_used: Optional[int] = None
    mq_queue_depth: Optional[int] = None
    disk_io_pct: Optional[float] = None


class TestRun(BaseModel):
    """A single test execution run."""
    run_id: str
    scenario_id: str
    test_type: TestType
    harness: str  # "loadrunner_enterprise" | "jmeter"
    harness_run_id: Optional[str] = None  # LRE run ID or JMeter test ID
    jenkins_build_id: Optional[str] = None

    status: RunStatus = RunStatus.INITIALIZING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_minutes: Optional[float] = None

    vusers_target: int = 0
    vusers_peak: int = 0
    total_transactions: int = 0
    total_errors: int = 0
    overall_error_rate_pct: float = 0.0

    transaction_metrics: list[TransactionMetrics] = []
    resource_snapshots: list[ResourceMetrics] = []
    anomalies: list[Anomaly] = []


class PeakPointResult(BaseModel):
    """Peak point — maximum TPS while conforming to SLA response times."""
    identified: bool = False
    peak_tps: float = 0.0
    vusers_at_peak: int = 0
    p90_at_peak_ms: float = 0.0
    error_rate_at_peak_pct: float = 0.0
    sla_compliant: bool = False
    run_id: str = ""


class BreakpointResult(BaseModel):
    """Breakpoint — where errors exceed 5% or timeouts dominate."""
    identified: bool = False
    breakpoint_tps: float = 0.0
    vusers_at_break: int = 0
    error_rate_at_break_pct: float = 0.0
    primary_failure_mode: str = ""  # "timeout" | "error_5xx" | "connection_refused"
    bottleneck_system: Optional[str] = None
    run_id: str = ""


class StabilityResult(BaseModel):
    """Stability test — sustained peak point for 1-4 hours."""
    passed: bool = False
    duration_hours: float = 0.0
    tps_sustained: float = 0.0
    p90_drift_pct: float = 0.0  # How much p90 changed over the duration
    error_rate_drift_pct: float = 0.0
    memory_leak_detected: bool = False
    resource_degradation_notes: list[str] = []
    run_id: str = ""


class ExecutionOutput(BaseModel):
    """Complete output of Phase 5: Execution & Monitoring."""
    test_runs: list[TestRun]
    peak_point: PeakPointResult
    breakpoint: BreakpointResult
    stability: StabilityResult
    all_anomalies: list[Anomaly]
    execution_summary: str = ""
    sla_compliance_summary: dict[str, bool] = {}  # transaction_name -> met SLA?
