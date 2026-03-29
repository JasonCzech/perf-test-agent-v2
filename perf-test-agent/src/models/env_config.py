"""Phase 3: Environment Configuration & Triage models.

Defines the reference configuration schema, inspection results,
and golden configuration baseline for the PERF environment.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class InspectionTool(str, Enum):
    AZURE_CLI = "azure_cli"
    ARGOCD_API = "argocd_api"
    AMDOCS_API = "amdocs_api"
    AMDOCS_CONFIG_FILE = "amdocs_config_file"
    KUBERNETES_API = "kubernetes_api"
    HTTP_PROBE = "http_probe"
    SOLACE_SEMP = "solace_semp"
    DB_QUERY = "db_query"


class RemediationLevel(str, Enum):
    """Graduated autonomy for remediation."""
    ALERT_ONLY = "alert_only"           # Just notify the team
    SUGGEST_FIX = "suggest_fix"         # Provide remediation command, wait for approval
    AUTO_REMEDIATE = "auto_remediate"   # Execute fix with approval


class ConfigField(BaseModel):
    """A single configuration field to check."""
    field_name: str
    expected_perf_value: str
    inspection_tool: InspectionTool
    description: str = ""
    known_qc_values: list[str] = Field(
        default_factory=list,
        description="Known QC environment values to flag"
    )
    remediation_level: RemediationLevel = RemediationLevel.SUGGEST_FIX
    remediation_command: Optional[str] = None


class ApplicationConfig(BaseModel):
    """Configuration reference for a single application/service."""
    app_name: str
    app_description: str = ""
    tags: list[str] = []
    backend_systems: list[str] = Field(default_factory=list)
    mots_id: Optional[str] = None
    additional_information: str = ""
    config_fields: list[ConfigField]


class EnvironmentReference(BaseModel):
    """The complete PERF environment reference — source of truth for config checks."""
    version: str = "1.0.0"
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    updated_by: str = "perf-engineering-team"
    environment_name: str = "PERF"
    lab_environment: str = "PERF"
    release_code: str = "current"
    application_key: str = "global"
    application_display_name: str = "Global Reference"
    api_variant: str = "core"
    applications: list[ApplicationConfig]

    def get_all_tags(self) -> list[str]:
        tags: list[str] = []
        for app in self.applications:
            for tag in app.tags:
                if tag not in tags:
                    tags.append(tag)
        return tags


class ConfigCheckResult(BaseModel):
    """Result of checking a single config field."""
    app_name: str
    field_name: str
    expected_value: str
    actual_value: Optional[str] = None
    matches: bool = False
    is_qc_value: bool = False
    inspection_tool: InspectionTool
    error: Optional[str] = None
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class ConfigValidationReport(BaseModel):
    """Complete report of an environment configuration check run."""
    run_id: str
    environment: str = "PERF"
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    results: list[ConfigCheckResult] = []
    is_golden: bool = Field(
        default=False,
        description="True if all checks passed — this becomes the golden config"
    )
    mismatches_summary: list[str] = []
    remediation_actions: list[dict[str, Any]] = []


class GoldenConfig(BaseModel):
    """The validated golden configuration baseline.

    Saved after the first successful full validation and used as the
    reference for daily config drift checks throughout the test cycle.
    """
    config_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    environment: str = "PERF"
    validation_run_id: str
    application_configs: dict[str, dict[str, str]] = Field(
        description="app_name -> {field_name: validated_value}"
    )
    next_scheduled_check: Optional[datetime] = None
