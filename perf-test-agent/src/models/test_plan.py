"""Phase 2: Test Planning models.

Defines the complete performance test plan including workload models,
test scenarios, environment specs, and entry/exit criteria.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .pipeline_state import Protocol, RiskLevel, SLATarget, TestHarness, TestType


class TransactionMixEntry(BaseModel):
    """A single transaction in the workload mix."""
    transaction_name: str
    percentage: float = Field(ge=0.0, le=100.0)
    tps_target: float = Field(ge=0.0, description="Transactions per second at peak")
    think_time_seconds: float = Field(default=3.0, ge=0.0)
    pacing_seconds: float = Field(default=0.0, ge=0.0)
    protocol: Protocol
    harness: TestHarness


class WorkloadModel(BaseModel):
    """Complete workload model for the test."""
    transaction_mix: list[TransactionMixEntry]
    total_tps_target: float
    total_vusers_estimate: int
    ramp_up_minutes: int = 10
    ramp_up_step_users: int = Field(
        default=10,
        description="Users added per ramp step (telecom: keep low to avoid MQ/Cassandra flooding)"
    )
    ramp_down_minutes: int = 5
    steady_state_minutes: int = 30

    @field_validator("transaction_mix")
    @classmethod
    def validate_mix_totals(cls, v: list[TransactionMixEntry]) -> list[TransactionMixEntry]:
        total = sum(e.percentage for e in v)
        if abs(total - 100.0) > 0.1:
            raise ValueError(f"Transaction mix must sum to 100%, got {total}%")
        return v


class TestScenario(BaseModel):
    """A single test scenario (load, stress, endurance, spike, breakpoint)."""
    scenario_id: str
    test_type: TestType
    description: str
    workload_model: WorkloadModel
    duration_minutes: int
    success_criteria: list[str]
    sla_targets: list[SLATarget]
    monitoring_focus: list[str] = Field(
        default_factory=list,
        description="Specific metrics to watch: ['JVM heap', 'Cassandra read latency', 'MQ depth']"
    )


class EnvironmentSpec(BaseModel):
    """Environment requirements for the test."""
    target_environment: str = "PERF"
    systems_required: list[str]
    aks_namespaces: list[str] = []
    expected_replica_counts: dict[str, int] = {}  # service -> min replicas
    database_requirements: dict[str, str] = {}     # db_name -> connection info
    mq_requirements: dict[str, str] = {}           # queue/topic -> broker
    external_dependencies: list[str] = []
    network_requirements: list[str] = []


class DataPreparationStep(BaseModel):
    """A step in bulk data preparation."""
    step_id: str
    entity_type: str
    quantity: int
    method: str = Field(description="sql_insert | api_provisioning | data_tool | manual")
    source_system: str
    script_path: Optional[str] = None
    sql_template: Optional[str] = None
    api_endpoint: Optional[str] = None
    estimated_duration_minutes: int = 0
    dependencies: list[str] = []  # Other step_ids that must complete first


class MonitoringConfig(BaseModel):
    """Monitoring configuration for test execution."""
    dynatrace_management_zone: str = ""
    dynatrace_tags: list[str] = []
    prometheus_targets: list[str] = []
    grafana_dashboards: list[str] = []
    elk_index_patterns: list[str] = []
    alert_thresholds: dict[str, float] = {}  # metric_name -> threshold
    log_watch_patterns: list[str] = Field(
        default_factory=lambda: [
            "OutOfMemoryError",
            "Connection refused",
            "timeout",
            "Circuit breaker",
            "503 Service Unavailable",
        ]
    )


class EntryCriteria(BaseModel):
    """Conditions that must be met before test execution."""
    env_config_validated: bool = True
    scripts_validated: bool = True
    bulk_data_loaded: bool = True
    monitoring_configured: bool = True
    stakeholder_approval: bool = True
    custom_criteria: list[str] = []


class ExitCriteria(BaseModel):
    """Conditions that define test completion."""
    all_slas_evaluated: bool = True
    peak_point_identified: bool = True
    breakpoint_identified: bool = True
    stability_test_passed: bool = True
    no_critical_defects: bool = True
    results_reported: bool = True
    custom_criteria: list[str] = []


class RiskEntry(BaseModel):
    """A risk identified during planning."""
    risk_id: str
    description: str
    level: RiskLevel
    mitigation: str
    owner: str = ""


class TestPlan(BaseModel):
    """Complete performance test plan — output of Phase 2."""
    plan_id: str
    title: str
    version: str = "1.0"
    created_by: str = "perf-test-agent"
    description: str
    scope: str
    assumptions: list[str] = []
    exclusions: list[str] = []

    # Core content
    test_scenarios: list[TestScenario]
    environment_spec: EnvironmentSpec
    data_preparation: list[DataPreparationStep]
    monitoring: MonitoringConfig
    entry_criteria: EntryCriteria
    exit_criteria: ExitCriteria
    risk_register: list[RiskEntry] = []

    # Schedule
    estimated_duration_days: int = 5
    phase_schedule: dict[str, str] = {}  # phase_name -> date range

    # Traceability
    source_story_keys: list[str] = []
    test_case_ids: list[str] = []
