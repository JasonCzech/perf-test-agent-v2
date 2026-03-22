"""Phase 4: Script & Data Creation models.

Defines generated script metadata, validation results, and bulk data status.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .pipeline_state import Protocol, TestHarness


class ScriptLanguage(str, Enum):
    VUGEN_C = "vugen_c"              # LoadRunner VuGen C scripts
    VUGEN_TRUCLIENT = "vugen_truclient"  # TruClient (not used for OPUS per decision)
    JMETER_JMX = "jmeter_jmx"       # JMeter XML test plans
    JMETER_GROOVY = "jmeter_groovy"  # JMeter JSR223 Groovy scripts
    K6_JS = "k6_js"                  # K6 JavaScript (future)


class GeneratedScript(BaseModel):
    """Metadata for a generated performance test script."""
    script_id: str
    test_case_id: str
    transaction_name: str
    harness: TestHarness
    language: ScriptLanguage
    protocol: Protocol
    file_path: str
    file_content_hash: str = ""

    # Script structure
    has_parameterization: bool = True
    parameter_files: list[str] = []
    has_correlation: bool = True
    correlation_rules: list[str] = []
    has_think_time: bool = True
    think_time_seconds: float = 3.0

    # Validation
    syntax_valid: bool = False
    dry_run_passed: bool = False
    validation_errors: list[str] = []
    validation_warnings: list[str] = []


class ScriptValidationResult(BaseModel):
    """Result of validating a generated script."""
    script_id: str
    validated_at: datetime = Field(default_factory=datetime.utcnow)
    syntax_check_passed: bool = False
    syntax_errors: list[str] = []
    dry_run_passed: bool = False
    dry_run_response_code: Optional[int] = None
    dry_run_response_time_ms: Optional[int] = None
    dry_run_errors: list[str] = []
    correlation_check_passed: bool = False
    parameterization_check_passed: bool = False
    overall_passed: bool = False


class BulkDataRequest(BaseModel):
    """Request to create bulk test data."""
    request_id: str
    entity_type: str
    quantity: int
    target_system: str
    method: str  # sql_insert | api_provisioning | data_tool | manual
    sql_template: Optional[str] = None
    api_endpoint: Optional[str] = None
    api_payload_template: Optional[str] = None
    dependencies: list[str] = []  # Other request_ids


class BulkDataStatus(BaseModel):
    """Status of bulk data provisioning."""
    request_id: str
    entity_type: str
    target_system: str
    quantity_requested: int
    quantity_created: int = 0
    status: str = "pending"  # pending | in_progress | completed | failed
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    errors: list[str] = []


class ScriptDataOutput(BaseModel):
    """Complete output of Phase 4: Script & Data Creation."""
    generated_scripts: list[GeneratedScript]
    validation_results: list[ScriptValidationResult]
    bulk_data_statuses: list[BulkDataStatus]
    all_scripts_valid: bool = False
    all_data_ready: bool = False
    ready_for_execution: bool = False
    blockers: list[str] = []
