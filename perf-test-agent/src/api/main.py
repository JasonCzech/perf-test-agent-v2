"""FastAPI backend for the PERF-TEST-AGENT Web Dashboard.

Provides:
- REST endpoints for pipeline management
- WebSocket endpoint for real-time status updates
- HITL approval/rejection endpoints
- Pipeline state inspection
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError

from src.agents.story_analyzer import _artifacts_to_prompt_section
from src.config.settings import get_settings
from src.config.settings import LLMTask
from src.integrations.azure_openai import get_llm
from src.integrations.jira_client import JiraClient
from src.integrations.rag_retriever import RAGRetriever
from src.models.pipeline_state import (
    PhaseResult,
    PhaseStatus,
    PipelinePhase,
    PipelineState,
)
from src.pipeline import PipelineOrchestrator
from src.prompts import PHASE_PROMPT_FILES, load_prompt
from src.runtime import get_repo_root, get_workspace_root
from src.utils.logging import get_logger
from src.utils import env_reference_store

log = get_logger(__name__)

app = FastAPI(
    title="PERF-TEST-AGENT Dashboard",
    version="2.0.0",
    description="Agentic Performance Testing Pipeline — AT&T CTx CQE",
)

PROJECT_ROOT = get_repo_root()
WORKSPACE_ROOT = get_workspace_root()


def _resolve_dashboard_html_path() -> Path:
    dashboard_override = os.getenv("PERF_TEST_AGENT_DASHBOARD_HTML")
    override = Path(dashboard_override).expanduser().resolve() if dashboard_override else None
    candidates = [
        override,
        WORKSPACE_ROOT / "perf_test_dashboard.html",
        PROJECT_ROOT / "perf_test_dashboard.html",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return WORKSPACE_ROOT / "perf_test_dashboard.html"


DASHBOARD_HTML_PATH = _resolve_dashboard_html_path()

# CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── State ─────────────────────────────────────────────────────────────

# Active pipeline runs and their orchestrators
active_runs: dict[str, PipelineOrchestrator] = {}
active_states: dict[str, PipelineState] = {}
# HITL approval queues: run_id -> asyncio.Event
hitl_events: dict[str, asyncio.Event] = {}
hitl_decisions: dict[str, tuple[bool, str]] = {}
# WebSocket connections for real-time updates
ws_connections: list[WebSocket] = []


# ── Request/Response Models ───────────────────────────────────────────

class StartPipelineRequest(BaseModel):
    story_keys: Optional[list[str]] = None
    sprint_name: Optional[str] = None
    start_phase: Optional[str] = None
    stop_after: Optional[str] = None
    hitl_enabled: bool = True
    context_ids: list[str] = Field(default_factory=list)
    selected_app_id: Optional[str] = None
    selected_app_name: Optional[str] = None
    selected_app_reference_key: Optional[str] = None
    generated_summary: Optional[dict[str, Any]] = None
    user_artifacts: Optional[dict[str, Any]] = None


class SummaryEvidence(BaseModel):
    source: str
    title: str
    reference: str
    url: str = ""
    excerpt: str = ""


class SummarySourceCoverage(BaseModel):
    jira_status: str
    rag_status: str
    jira_count: int = 0
    rag_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class GenerateSummaryRequest(BaseModel):
    story_keys: list[str] = Field(default_factory=list)
    sprint_name: Optional[str] = None
    context_ids: list[str] = Field(default_factory=list)
    selected_app_id: str = Field(..., min_length=1)
    selected_app_name: str = Field(..., min_length=1)
    selected_app_reference_key: Optional[str] = None
    user_artifacts: Optional[dict[str, Any]] = None


class GenerateSummaryResponse(BaseModel):
    generated_at: str
    selected_app_id: str
    selected_app_name: str
    project_goals: list[str] = Field(default_factory=list)
    selected_app_impacts: list[str] = Field(default_factory=list)
    cross_system_impacts: list[str] = Field(default_factory=list)
    recommended_focus: list[str] = Field(default_factory=list)
    evidence: list[SummaryEvidence] = Field(default_factory=list)
    source_coverage: SummarySourceCoverage
    context_used: dict[str, Any] = Field(default_factory=dict)


class HITLDecisionRequest(BaseModel):
    approved: bool
    notes: str = ""


class PhasePromptPayloadResponse(BaseModel):
    run_id: str
    phase: str
    default_prompt: str
    prompt_override: str = ""
    pre_execution_context: str = ""
    effective_prompt: str


class PhasePromptUpdateRequest(BaseModel):
    prompt_override: Optional[str] = None
    pre_execution_context: Optional[str] = None


class PhaseExecuteRequest(BaseModel):
    prompt_override: Optional[str] = None
    pre_execution_context: Optional[str] = None


class PhaseActionRequest(BaseModel):
    notes: str = ""
    prompt_override: Optional[str] = None
    pre_execution_context: Optional[str] = None
    rerun: bool = True


class PhaseStepResponse(BaseModel):
    run_id: str
    phase: str
    status: str
    current_phase: str
    notes: str = ""


class PipelineStatusResponse(BaseModel):
    run_id: str
    current_phase: str
    created_at: str
    phase_results: dict[str, Any]
    is_active: bool


class EnvReferenceSummary(BaseModel):
    application_key: str
    application_name: str
    api_variant: str
    environment: str
    lab_environment: str
    release_code: str
    path: str
    tags: list[str] = []
    last_updated: datetime
    updated_by: str


class EnvReferenceDetail(BaseModel):
    descriptor: EnvReferenceSummary
    content: str


class EnvReferenceListResponse(BaseModel):
    references: list[EnvReferenceSummary]


class UpdateEnvReferenceRequest(BaseModel):
    content: str = Field(..., min_length=1)
    api_variant: str = "core"
    lab_environment: str = "PERF"
    release_code: str = "current"
    application_name: Optional[str] = None
    updated_by: str = "web-ui"


class AppCatalogItem(BaseModel):
    id: str
    reference_key: str
    application_name: str
    api_variant: str
    endpoint_url: str = ""
    owner_team: str = ""
    version: str = ""
    tags: list[str] = []
    display_on_new_test: bool = True
    is_active: bool = True
    updated_by: str
    last_updated: datetime
    reference_count: int = 0


class AppCatalogListResponse(BaseModel):
    applications: list[AppCatalogItem]


class CreateApplicationRequest(BaseModel):
    application_key: str = Field(..., min_length=2)
    application_name: str = Field(..., min_length=2)
    endpoint_url: str = Field(..., min_length=3)
    api_variant: str = "core"
    tags: list[str] = []
    owner_team: Optional[str] = None
    version: Optional[str] = None
    display_on_new_test: bool = False
    updated_by: str = "dashboard-ui"


class UpdateApplicationRequest(BaseModel):
    application_name: str = Field(..., min_length=2)
    endpoint_url: str = Field(..., min_length=3)
    api_variant: str = "core"
    tags: list[str] = []
    owner_team: Optional[str] = None
    version: Optional[str] = None
    display_on_new_test: Optional[bool] = None
    updated_by: str = "dashboard-ui"


class ArchiveApplicationRequest(BaseModel):
    updated_by: str = "dashboard-ui"


class ArchiveApplicationResponse(BaseModel):
    archived_count: int


class JiraTicket(BaseModel):
    key: str
    summary: str
    status: str
    issue_type: str
    created: str
    reporter: Optional[str] = None
    assignee: Optional[str] = None
    updated: str
    url: str
    labels: list[str] = []


class JiraTicketListResponse(BaseModel):
    tickets: list[JiraTicket]


class CreateJiraTicketRequest(BaseModel):
    application_key: str = Field(..., min_length=2)
    summary: str = Field(..., min_length=3)
    description: str = Field(..., min_length=1)
    issue_type: str = "Task"
    project_key: Optional[str] = None
    labels: list[str] = []


class PhasePromptResponse(BaseModel):
    phase_id: str
    prompt_file: str
    role_label: str
    default_prompt: str


PHASE_ROLE_LABELS = {
    "story_analysis": "StoryAnalyzerAgent",
    "test_planning": "TestPlanGeneratorAgent",
    "env_triage": "EnvConfigAgent",
    "script_data": "ScriptGeneratorAgent",
    "execution": "ExecutionOrchestratorAgent",
    "reporting": "ResultsAnalyzerAgent",
    "postmortem": "PostmortemAgent",
}


def _record_to_summary(
    record: env_reference_store.EnvironmentReferenceRecord,
) -> EnvReferenceSummary:
    return EnvReferenceSummary(
        application_key=record.application_key,
        application_name=record.application_name,
        api_variant=record.api_variant,
        environment=record.environment,
        lab_environment=record.lab_environment,
        release_code=record.release_code,
        path=record.path,
        tags=record.tags,
        last_updated=record.last_updated,
        updated_by=record.updated_by,
    )


def _catalog_item_to_response(item: dict[str, Any]) -> AppCatalogItem:
    return AppCatalogItem(
        id=item["id"],
        reference_key=item["reference_key"],
        application_name=item["application_name"],
        api_variant=item["api_variant"],
        endpoint_url=item.get("endpoint_url", ""),
        owner_team=item.get("owner_team", ""),
        version=item.get("version", ""),
        tags=item.get("tags", []),
        display_on_new_test=item.get("display_on_new_test", True),
        is_active=item.get("is_active", True),
        updated_by=item.get("updated_by", ""),
        last_updated=item["last_updated"],
        reference_count=item.get("reference_count", 0),
    )


def _application_label(application_key: str) -> str:
    return f"app:{application_key}".lower()


def _issue_to_ticket(issue: dict[str, Any], settings) -> JiraTicket:
    fields = issue.get("fields", {})
    assignee = fields.get("assignee") or {}
    reporter = fields.get("reporter") or {}
    status = fields.get("status") or {}
    issue_type = fields.get("issuetype") or {}
    # Force iTrack browse URLs so ticket links resolve to the expected ATT Jira host.
    base_url = "https://itrack.web.att.com"
    return JiraTicket(
        key=issue.get("key", ""),
        summary=fields.get("summary", ""),
        status=status.get("name", "Unknown"),
        issue_type=issue_type.get("name", ""),
        created=fields.get("created") or datetime.utcnow().isoformat(),
        reporter=reporter.get("displayName"),
        assignee=assignee.get("displayName"),
        updated=fields.get("updated") or datetime.utcnow().isoformat(),
        url=f"{base_url}/browse/{issue.get('key', '')}",
        labels=fields.get("labels") or [],
    )


def _truncate(value: str, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _safe_story_key(value: str) -> str:
    return value.strip().upper()


def _safe_context_id(value: str) -> str:
    return value.strip().upper()


def _escape_jira_text(value: str) -> str:
    return value.replace('"', '\\"')


def _normalize_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for item in values:
        next_item = item.strip()
        if not next_item or next_item in seen:
            continue
        seen.add(next_item)
        normalized.append(next_item)
    return normalized


def _extract_json_object(raw: str) -> str:
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        raise ValueError("No JSON object found in model response")
    return match.group(0)


def _fallback_summary(
    req: GenerateSummaryRequest,
    jira_evidence: list[SummaryEvidence],
    rag_evidence: list[SummaryEvidence],
    context_used: Optional[dict[str, Any]] = None,
) -> GenerateSummaryResponse:
    project_goals = [
        f"Validate performance readiness for {req.selected_app_name} within sprint scope {req.sprint_name or '-'}.",
        "Preserve SLA compliance and release confidence for impacted downstream systems.",
    ]
    selected_app_impacts = [
        f"{req.selected_app_name} is in direct test focus and should be prioritized for load, stress, and stability validation.",
        "Capture end-to-end transaction latency contribution from this application during scenario modeling.",
    ]
    cross_system_impacts = [
        "Review middleware/backend dependencies referenced in Jira and wiki context to prevent bottlenecks at integration boundaries.",
        "Track shared infrastructure capacity and connection pool behavior during high-throughput scenarios.",
    ]
    recommended_focus = [
        "Focus first on high-volume user journeys connected to selected app transactions.",
        "Map each critical flow to a measurable SLA target and failure threshold.",
    ]
    evidence = (jira_evidence + rag_evidence)[:5]
    return GenerateSummaryResponse(
        generated_at=datetime.utcnow().isoformat(),
        selected_app_id=req.selected_app_id,
        selected_app_name=req.selected_app_name,
        project_goals=project_goals,
        selected_app_impacts=selected_app_impacts,
        cross_system_impacts=cross_system_impacts,
        recommended_focus=recommended_focus,
        evidence=evidence,
        source_coverage=SummarySourceCoverage(
            jira_status="ok" if jira_evidence else "empty",
            rag_status="ok" if rag_evidence else "empty",
            jira_count=len(jira_evidence),
            rag_count=len(rag_evidence),
            warnings=["Summary generated via deterministic fallback due to LLM response issue."],
        ),
        context_used=context_used or {},
    )


def _is_run_active(state: PipelineState) -> bool:
    terminal = {PhaseStatus.COMPLETED}
    current = state.phase_results.get(state.current_phase.value)
    if not current:
        return True
    if state.current_phase == PipelinePhase.POSTMORTEM and current.status in terminal:
        return False
    return current.status not in terminal


def _load_run_state(run_id: str) -> PipelineState:
    state = active_states.get(run_id)
    if state:
        return state

    state_path = Path(get_settings().pipeline_run_dir) / run_id / "pipeline_state.json"
    if not state_path.exists():
        raise HTTPException(404, f"Run {run_id} not found")

    state = PipelineState.model_validate_json(state_path.read_text())
    active_states[run_id] = state
    return state


def _ensure_orchestrator(run_id: str) -> PipelineOrchestrator:
    existing = active_runs.get(run_id)
    if existing:
        return existing

    orchestrator = PipelineOrchestrator(get_settings())
    active_runs[run_id] = orchestrator
    return orchestrator


def _coerce_phase(phase_name: str) -> PipelinePhase:
    try:
        return PipelinePhase(phase_name)
    except ValueError as exc:
        raise HTTPException(400, f"Unknown phase '{phase_name}'") from exc


# ── WebSocket Broadcast ──────────────────────────────────────────────

async def broadcast(event: str, data: dict[str, Any]) -> None:
    """Broadcast an event to all connected WebSocket clients."""
    message = json.dumps({"event": event, "data": data, "timestamp": datetime.utcnow().isoformat()})
    disconnected = []
    for ws in ws_connections:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        ws_connections.remove(ws)


# ── HITL Callback ─────────────────────────────────────────────────────

async def hitl_callback(
    phase: PipelinePhase,
    phase_result: PhaseResult,
    state: PipelineState,
) -> tuple[bool, str]:
    """HITL callback used by the orchestrator.

    Publishes the phase result to the Web UI via WebSocket,
    then waits for human approval/rejection via the REST endpoint.
    """
    run_id = state.run_id

    # Broadcast awaiting approval
    await broadcast("hitl_awaiting", {
        "run_id": run_id,
        "phase": phase.value,
        "summary": phase_result.summary,
        "warnings": phase_result.errors,
        "artifacts": phase_result.artifacts,
    })

    # Create an event and wait for decision
    event = asyncio.Event()
    hitl_events[run_id] = event

    log.info("hitl_waiting", run_id=run_id, phase=phase.value)
    await event.wait()

    # Retrieve decision
    approved, notes = hitl_decisions.pop(run_id, (False, "No decision received"))
    hitl_events.pop(run_id, None)

    await broadcast("hitl_decided", {
        "run_id": run_id,
        "phase": phase.value,
        "approved": approved,
        "notes": notes,
    })

    return approved, notes


# ── REST Endpoints ────────────────────────────────────────────────────

@app.post("/api/pipeline/start", response_model=dict)
async def start_pipeline(req: StartPipelineRequest):
    """Initialize a new pipeline run in prompt-review state."""
    settings = get_settings()
    settings.hitl_enabled = req.hitl_enabled

    orchestrator = PipelineOrchestrator(settings)
    try:
        start = PipelinePhase(req.start_phase) if req.start_phase else None
        stop = PipelinePhase(req.stop_after) if req.stop_after else None
    except ValueError as exc:
        raise HTTPException(400, f"Invalid phase in request: {exc}") from exc

    state = await orchestrator.initialize_run(
        story_keys=req.story_keys,
        sprint_name=req.sprint_name,
        context_ids=req.context_ids,
        selected_app_id=req.selected_app_id,
        selected_app_name=req.selected_app_name,
        selected_app_reference_key=req.selected_app_reference_key,
        generated_summary=req.generated_summary,
        user_artifacts=req.user_artifacts,
        start_phase=start,
        stop_after=stop,
    )

    active_runs[state.run_id] = orchestrator
    active_states[state.run_id] = state

    current_result = state.phase_results.get(state.current_phase.value)
    await broadcast(
        "pipeline_initialized",
        {
            "run_id": state.run_id,
            "phase": state.current_phase.value,
            "status": current_result.status.value if current_result else PhaseStatus.PROMPT_REVIEW.value,
        },
    )

    return {
        "status": "started",
        "run_id": state.run_id,
        "current_phase": state.current_phase.value,
        "current_phase_status": current_result.status.value if current_result else PhaseStatus.PROMPT_REVIEW.value,
    }


@app.get("/api/pipeline/{run_id}/phase/{phase_name}/prompt", response_model=PhasePromptPayloadResponse)
async def get_phase_prompt_payload(run_id: str, phase_name: str):
    state = _load_run_state(run_id)
    phase = _coerce_phase(phase_name)

    if phase.value not in PHASE_PROMPT_FILES:
        raise HTTPException(404, f"No prompt file configured for phase '{phase.value}'")

    default_prompt = load_prompt(phase.value)
    prompt_override = (state.phase_prompt_overrides or {}).get(phase.value, "")
    pre_execution_context = (state.phase_pre_execution_context or {}).get(phase.value, "")
    effective_prompt = prompt_override.strip() or default_prompt

    return PhasePromptPayloadResponse(
        run_id=run_id,
        phase=phase.value,
        default_prompt=default_prompt,
        prompt_override=prompt_override,
        pre_execution_context=pre_execution_context,
        effective_prompt=effective_prompt,
    )


@app.put("/api/pipeline/{run_id}/phase/{phase_name}/prompt", response_model=PhasePromptPayloadResponse)
async def update_phase_prompt_payload(run_id: str, phase_name: str, req: PhasePromptUpdateRequest):
    state = _load_run_state(run_id)
    phase = _coerce_phase(phase_name)

    if phase.value not in PHASE_PROMPT_FILES:
        raise HTTPException(404, f"No prompt file configured for phase '{phase.value}'")

    if req.prompt_override is not None:
        state.phase_prompt_overrides[phase.value] = req.prompt_override
    if req.pre_execution_context is not None:
        state.phase_pre_execution_context[phase.value] = req.pre_execution_context

    state.save(get_settings().pipeline_run_dir)
    active_states[run_id] = state

    default_prompt = load_prompt(phase.value)
    prompt_override = (state.phase_prompt_overrides or {}).get(phase.value, "")
    pre_execution_context = (state.phase_pre_execution_context or {}).get(phase.value, "")

    return PhasePromptPayloadResponse(
        run_id=run_id,
        phase=phase.value,
        default_prompt=default_prompt,
        prompt_override=prompt_override,
        pre_execution_context=pre_execution_context,
        effective_prompt=prompt_override.strip() or default_prompt,
    )


@app.post("/api/pipeline/{run_id}/phase/{phase_name}/execute", response_model=PhaseStepResponse)
async def execute_phase_step(run_id: str, phase_name: str, req: PhaseExecuteRequest):
    state = _load_run_state(run_id)
    phase = _coerce_phase(phase_name)
    orchestrator = _ensure_orchestrator(run_id)

    if phase != state.current_phase:
        raise HTTPException(409, f"Current phase is {state.current_phase.value}; cannot execute {phase.value}")

    if req.prompt_override is not None:
        state.phase_prompt_overrides[phase.value] = req.prompt_override
    if req.pre_execution_context is not None:
        state.phase_pre_execution_context[phase.value] = req.pre_execution_context

    try:
        phase_result = await orchestrator.execute_phase(state, phase)
    except Exception as exc:
        raise HTTPException(500, f"Phase execution failed: {exc}") from exc

    active_states[run_id] = state
    await broadcast(
        "phase_executed",
        {
            "run_id": run_id,
            "phase": phase.value,
            "status": phase_result.status.value,
        },
    )

    return PhaseStepResponse(
        run_id=run_id,
        phase=phase.value,
        status=phase_result.status.value,
        current_phase=state.current_phase.value,
        notes="",
    )


@app.post("/api/pipeline/{run_id}/phase/{phase_name}/approve", response_model=PhaseStepResponse)
async def approve_phase_step(run_id: str, phase_name: str, req: PhaseActionRequest):
    state = _load_run_state(run_id)
    phase = _coerce_phase(phase_name)
    orchestrator = _ensure_orchestrator(run_id)

    if req.prompt_override is not None:
        state.phase_prompt_overrides[phase.value] = req.prompt_override
    if req.pre_execution_context is not None:
        state.phase_pre_execution_context[phase.value] = req.pre_execution_context

    state = await orchestrator.approve_phase(state, phase, approved=True, notes=req.notes)
    active_states[run_id] = state

    current_result = state.phase_results.get(state.current_phase.value)
    status = current_result.status.value if current_result else PhaseStatus.PENDING.value

    await broadcast(
        "phase_approved",
        {
            "run_id": run_id,
            "phase": phase.value,
            "next_phase": state.current_phase.value,
            "next_status": status,
        },
    )

    return PhaseStepResponse(
        run_id=run_id,
        phase=phase.value,
        status=status,
        current_phase=state.current_phase.value,
        notes=req.notes,
    )


@app.post("/api/pipeline/{run_id}/phase/{phase_name}/modify", response_model=PhaseStepResponse)
async def modify_phase_step(run_id: str, phase_name: str, req: PhaseActionRequest):
    state = _load_run_state(run_id)
    phase = _coerce_phase(phase_name)
    orchestrator = _ensure_orchestrator(run_id)

    if req.prompt_override is not None:
        state.phase_prompt_overrides[phase.value] = req.prompt_override
    if req.pre_execution_context is not None:
        state.phase_pre_execution_context[phase.value] = req.pre_execution_context
    if req.notes:
        state.phase_post_execution_notes[phase.value] = req.notes

    state = await orchestrator.mark_phase_for_modify(state, phase)

    existing = state.phase_results.get(phase.value)
    response_status = existing.status.value if existing else PhaseStatus.PROMPT_REVIEW.value
    if req.rerun:
        phase_result = await orchestrator.execute_phase(state, phase)
        response_status = phase_result.status.value

    active_states[run_id] = state
    await broadcast(
        "phase_modified",
        {
            "run_id": run_id,
            "phase": phase.value,
            "status": response_status,
        },
    )

    return PhaseStepResponse(
        run_id=run_id,
        phase=phase.value,
        status=response_status,
        current_phase=state.current_phase.value,
        notes=req.notes,
    )


@app.post("/api/pipeline/{run_id}/approve")
async def approve_phase(run_id: str, req: HITLDecisionRequest):
    """Backward-compatible approval endpoint."""
    if run_id in hitl_events:
        hitl_decisions[run_id] = (req.approved, req.notes)
        hitl_events[run_id].set()
        return {"status": "decision_recorded", "approved": req.approved}

    state = _load_run_state(run_id)
    orchestrator = _ensure_orchestrator(run_id)
    state = await orchestrator.approve_phase(
        state,
        state.current_phase,
        approved=req.approved,
        notes=req.notes,
    )
    active_states[run_id] = state
    return {
        "status": "decision_recorded",
        "approved": req.approved,
        "current_phase": state.current_phase.value,
    }


@app.get("/api/pipeline/{run_id}/status", response_model=PipelineStatusResponse)
async def get_pipeline_status(run_id: str):
    """Get the current status of a pipeline run."""
    state = _load_run_state(run_id)

    return PipelineStatusResponse(
        run_id=state.run_id,
        current_phase=state.current_phase.value,
        created_at=state.created_at.isoformat(),
        phase_results={k: v.model_dump() for k, v in state.phase_results.items()},
        is_active=_is_run_active(state),
    )


@app.get("/api/pipeline/runs")
async def list_runs():
    """List all pipeline runs."""
    run_dir = Path(get_settings().pipeline_run_dir)
    runs = []
    if run_dir.exists():
        for d in sorted(run_dir.iterdir(), reverse=True):
            state_file = d / "pipeline_state.json"
            if state_file.exists():
                state = PipelineState.model_validate_json(state_file.read_text())
                phase_results = state.phase_results or {}
                completed_phases = sum(
                    1
                    for phase in PipelinePhase
                    if phase_results.get(phase.value)
                    and phase_results[phase.value].status == PhaseStatus.COMPLETED
                )
                failed_phase = next(
                    (
                        phase.value
                        for phase in PipelinePhase
                        if phase_results.get(phase.value)
                        and phase_results[phase.value].status == PhaseStatus.FAILED
                    ),
                    None,
                )
                awaiting_approval_phase = next(
                    (
                        phase.value
                        for phase in PipelinePhase
                        if phase_results.get(phase.value)
                        and phase_results[phase.value].status
                        in {
                            PhaseStatus.AWAITING_APPROVAL,
                            PhaseStatus.RESULTS_READY,
                            PhaseStatus.PROMPT_REVIEW,
                        }
                    ),
                    None,
                )
                reporting_result = phase_results.get(PipelinePhase.REPORTING.value)
                verdict = "-"
                if reporting_result and reporting_result.summary:
                    summary_upper = reporting_result.summary.upper()
                    if "CONDITIONAL GO" in summary_upper:
                        verdict = "CONDITIONAL"
                    elif "NO-GO" in summary_upper:
                        verdict = "NO-GO"
                    elif "GO" in summary_upper:
                        verdict = "GO"

                duration_seconds = 0.0
                for result in phase_results.values():
                    try:
                        duration_seconds += float(result.duration_seconds or 0.0)
                    except Exception:
                        continue

                overall_status = "running"
                if failed_phase:
                    overall_status = "failed"
                elif completed_phases == len(PipelinePhase):
                    overall_status = "completed"

                runs.append({
                    "run_id": state.run_id,
                    "current_phase": state.current_phase.value,
                    "created_at": state.created_at.isoformat(),
                    "story_keys": state.jira_story_keys,
                    "is_active": _is_run_active(state),
                    "completed_phases": completed_phases,
                    "total_phases": len(PipelinePhase),
                    "failed_phase": failed_phase,
                    "awaiting_approval_phase": awaiting_approval_phase,
                    "overall_status": overall_status,
                    "verdict": verdict,
                    "duration_seconds": round(duration_seconds, 1),
                })
    return {"runs": runs}


@app.get("/api/pipeline/{run_id}/phase/{phase_name}")
async def get_phase_details(run_id: str, phase_name: str):
    """Get detailed output for a specific phase."""
    phase_dir = Path(get_settings().pipeline_run_dir) / run_id / phase_name
    output_file = phase_dir / "output.json"

    if not output_file.exists():
        raise HTTPException(404, f"Phase output not found for {phase_name}")

    return json.loads(output_file.read_text())


@app.get("/api/prompts/{phase_id}", response_model=PhasePromptResponse)
async def get_phase_prompt(phase_id: str):
    if phase_id not in PHASE_PROMPT_FILES:
        raise HTTPException(
            404,
            f"Unknown phase_id '{phase_id}'. Valid: {list(PHASE_PROMPT_FILES.keys())}",
        )

    try:
        default_prompt = load_prompt(phase_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    return PhasePromptResponse(
        phase_id=phase_id,
        prompt_file=PHASE_PROMPT_FILES[phase_id],
        role_label=PHASE_ROLE_LABELS.get(phase_id, phase_id),
        default_prompt=default_prompt,
    )


# ── Environment Reference Endpoints ─────────────────────────────────

@app.get("/api/apps", response_model=AppCatalogListResponse)
async def list_applications(include_inactive: bool = False):
    items = env_reference_store.list_applications(include_inactive=include_inactive)
    return AppCatalogListResponse(applications=[_catalog_item_to_response(item) for item in items])


@app.post("/api/apps", response_model=AppCatalogItem)
async def create_application(req: CreateApplicationRequest):
    key = req.application_key.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", key):
        raise HTTPException(400, "application_key must be lowercase slug format (letters, numbers, hyphen)")

    try:
        env_reference_store.register_application(
            application_key=key,
            application_name=req.application_name.strip(),
            endpoint_url=req.endpoint_url.strip(),
            api_variant=req.api_variant or "core",
            tags=req.tags or [],
            owner_team=req.owner_team,
            version=req.version,
            display_on_new_test=req.display_on_new_test,
            updated_by=req.updated_by,
        )
        items = env_reference_store.list_applications()
        created = next((item for item in items if item["reference_key"] == key), None)
        if not created:
            raise RuntimeError(f"Created app '{key}' could not be loaded")
        return _catalog_item_to_response(created)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValidationError, yaml.YAMLError) as exc:
        raise HTTPException(400, f"Invalid application payload: {exc}") from exc
    except Exception as exc:  # pragma: no cover - unexpected failure path
        log.error("application_create_failed", error=str(exc), application_key=key)
        raise HTTPException(500, "Unable to create application") from exc


@app.patch("/api/apps/{application_key}", response_model=AppCatalogItem)
async def update_application(application_key: str, req: UpdateApplicationRequest):
    key = application_key.strip().lower()
    try:
        env_reference_store.update_application(
            application_key=key,
            application_name=req.application_name.strip(),
            endpoint_url=req.endpoint_url.strip(),
            api_variant=req.api_variant or "core",
            tags=req.tags or [],
            owner_team=req.owner_team,
            version=req.version,
            display_on_new_test=req.display_on_new_test,
            updated_by=req.updated_by,
        )
        items = env_reference_store.list_applications()
        updated = next((item for item in items if item["reference_key"] == key), None)
        if not updated:
            raise RuntimeError(f"Updated app '{key}' could not be loaded")
        return _catalog_item_to_response(updated)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValidationError, yaml.YAMLError) as exc:
        raise HTTPException(400, f"Invalid update payload: {exc}") from exc
    except Exception as exc:  # pragma: no cover - unexpected failure path
        log.error("application_update_failed", error=str(exc), application_key=key)
        raise HTTPException(500, "Unable to update application") from exc


@app.delete("/api/apps/{application_key}", response_model=ArchiveApplicationResponse)
async def archive_application(application_key: str, req: ArchiveApplicationRequest):
    key = application_key.strip().lower()
    try:
        archived_count = env_reference_store.archive_application(
            application_key=key,
            updated_by=req.updated_by,
        )
        return ArchiveApplicationResponse(archived_count=archived_count)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # pragma: no cover - unexpected failure path
        log.error("application_archive_failed", error=str(exc), application_key=key)
        raise HTTPException(500, "Unable to archive application") from exc

@app.get("/api/env/configs", response_model=EnvReferenceListResponse)
async def list_env_configs(
    application_key: Optional[str] = None,
    environment: Optional[str] = None,
    lab_environment: Optional[str] = None,
    release_code: Optional[str] = None,
    api_variant: Optional[str] = None,
):
    records = env_reference_store.list_references(
        application_key=application_key,
        environment=environment,
        lab_environment=lab_environment,
        release_code=release_code,
        api_variant=api_variant,
    )
    summaries = [_record_to_summary(r) for r in records]
    return EnvReferenceListResponse(references=summaries)


@app.get("/api/env/configs/{application_key}/{environment}", response_model=EnvReferenceDetail)
async def get_env_config(
    application_key: str,
    environment: str,
    api_variant: str = "core",
    lab_environment: str = "PERF",
    release_code: str = "current",
):
    try:
        record = env_reference_store.get_reference_record(
            application_key,
            environment,
            lab_environment,
            release_code,
            api_variant,
        )
        content = env_reference_store.read_reference_yaml(record)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, f"Reference file missing for {application_key}/{environment}") from exc

    return EnvReferenceDetail(descriptor=_record_to_summary(record), content=content)


@app.put("/api/env/configs/{application_key}/{environment}", response_model=EnvReferenceDetail)
async def update_env_config(application_key: str, environment: str, req: UpdateEnvReferenceRequest):
    api_variant = req.api_variant or "core"
    lab_environment = req.lab_environment or "PERF"
    release_code = req.release_code or "current"

    try:
        env_reference_store.save_reference(
            application_key=application_key,
            environment=environment,
            lab_environment=lab_environment,
            release_code=release_code,
            api_variant=api_variant,
            yaml_content=req.content,
            updated_by=req.updated_by,
            application_name=req.application_name,
        )
        record = env_reference_store.get_reference_record(
            application_key,
            environment,
            lab_environment,
            release_code,
            api_variant,
        )
        content = env_reference_store.read_reference_yaml(record)
    except (ValidationError, yaml.YAMLError) as exc:
        raise HTTPException(400, f"Invalid environment reference: {exc}") from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # pragma: no cover - unexpected failure path
        log.error("env_reference_update_failed", error=str(exc))
        raise HTTPException(500, "Unable to save environment reference") from exc

    return EnvReferenceDetail(descriptor=_record_to_summary(record), content=content)


# ── Jira Ticket Endpoints ────────────────────────────────────────────

@app.get("/api/jira/tickets", response_model=JiraTicketListResponse)
async def list_jira_tickets(
    application_key: Optional[str] = None,
    team_scope: bool = False,
    status: Optional[str] = None,
    issue_type: Optional[str] = None,
    reportee: Optional[str] = None,
    opened_from: Optional[str] = None,
    opened_to: Optional[str] = None,
    max_results: int = 25,
):
    if not team_scope and not application_key:
        raise HTTPException(400, "application_key is required when team_scope is false")

    max_results = max(1, min(max_results, 100))
    if team_scope:
        jql_parts = ['labels = "support"']
    else:
        label = _application_label(application_key or "")
        jql_parts = [f'labels = "{label}"']
    if issue_type:
        jql_parts.append(f'issuetype = "{issue_type}"')
    if status:
        jql_parts.append(f'status = "{status}"')
    if reportee:
        safe_reportee = _escape_jira_text(reportee)
        jql_parts.append(f'reporter = "{safe_reportee}"')
    if opened_from:
        jql_parts.append(f'created >= "{opened_from}"')
    if opened_to:
        jql_parts.append(f'created <= "{opened_to}"')
    jql = " AND ".join(jql_parts) + " ORDER BY updated DESC"

    client = JiraClient()
    settings = get_settings()
    try:
        issues = await client.get_stories_by_jql(jql, max_results=max_results)
    except Exception as exc:  # pragma: no cover - Jira failures propagated to UI
        raise HTTPException(502, f"Failed to fetch Jira tickets: {exc}") from exc
    finally:
        await client.close()

    tickets = [_issue_to_ticket(issue, settings) for issue in issues]
    return JiraTicketListResponse(tickets=tickets)


@app.post("/api/jira/tickets", response_model=JiraTicket)
async def create_jira_ticket(req: CreateJiraTicketRequest):
    if not req.application_key:
        raise HTTPException(400, "application_key is required")

    label = _application_label(req.application_key)
    label_set = {label, "support"}
    label_set.update(l.strip() for l in (req.labels or []) if l.strip())
    labels = sorted(label_set)

    client = JiraClient()
    settings = get_settings()
    try:
        key = await client.create_story(
            summary=req.summary,
            description=req.description,
            story_type=req.issue_type,
            labels=labels,
            project_key=req.project_key,
        )
        issue = await client.get_story(key)
    except Exception as exc:  # pragma: no cover - surface Jira errors
        raise HTTPException(502, f"Failed to create Jira ticket: {exc}") from exc
    finally:
        await client.close()

    return _issue_to_ticket(issue, settings)


@app.post("/api/context/summary", response_model=GenerateSummaryResponse)
async def generate_context_summary(req: GenerateSummaryRequest):
    story_keys = _normalize_list([_safe_story_key(k) for k in req.story_keys])
    context_ids = _normalize_list([_safe_context_id(cid) for cid in req.context_ids])
    artifacts_section = _artifacts_to_prompt_section(req.user_artifacts)

    if not context_ids:
        raise HTTPException(400, "At least one searched PID/E1 context ID is required")
    if not req.selected_app_name.strip():
        raise HTTPException(400, "selected_app_name is required")

    settings = get_settings()
    coverage = SummarySourceCoverage(jira_status="skipped", rag_status="skipped")
    jira_evidence: list[SummaryEvidence] = []
    rag_evidence: list[SummaryEvidence] = []

    # Jira retrieval path
    jira_client: Optional[JiraClient] = None
    try:
        if settings.jira_url and settings.jira_username and settings.jira_api_token:
            jira_client = JiraClient(settings)
            seen_keys: set[str] = set()

            for key in story_keys[:10]:
                try:
                    issue = await jira_client.get_story(key)
                    fields = issue.get("fields", {})
                    issue_key = issue.get("key", key)
                    if issue_key in seen_keys:
                        continue
                    seen_keys.add(issue_key)
                    jira_evidence.append(SummaryEvidence(
                        source="jira",
                        title=fields.get("summary", issue_key),
                        reference=issue_key,
                        url=f"{settings.jira_url.rstrip('/')}/browse/{issue_key}",
                        excerpt=_truncate(fields.get("description") or fields.get("summary", ""), 280),
                    ))
                except Exception as exc:
                    coverage.warnings.append(f"Jira story lookup failed for {key}: {exc}")

            for context_id in context_ids[:8]:
                escaped = _escape_jira_text(context_id)
                jql = (
                    f'project = {settings.jira_project_key} '
                    f'AND text ~ "\\"{escaped}\\"" '
                    "ORDER BY updated DESC"
                )
                try:
                    issues = await jira_client.get_stories_by_jql(jql, max_results=5)
                    for issue in issues:
                        issue_key = issue.get("key")
                        if not issue_key or issue_key in seen_keys:
                            continue
                        seen_keys.add(issue_key)
                        fields = issue.get("fields", {})
                        jira_evidence.append(SummaryEvidence(
                            source="jira",
                            title=fields.get("summary", issue_key),
                            reference=issue_key,
                            url=f"{settings.jira_url.rstrip('/')}/browse/{issue_key}",
                            excerpt=_truncate(fields.get("description") or fields.get("summary", ""), 280),
                        ))
                except Exception as exc:
                    coverage.warnings.append(f"Jira search failed for {context_id}: {exc}")

            coverage.jira_status = "ok" if jira_evidence else "empty"
            coverage.jira_count = len(jira_evidence)
        else:
            coverage.jira_status = "skipped"
            coverage.warnings.append("Jira credentials are not configured; skipping Jira retrieval.")
    except Exception as exc:
        coverage.jira_status = "error"
        coverage.warnings.append(f"Jira retrieval unavailable: {exc}")
    finally:
        if jira_client:
            await jira_client.close()

    # RAG retrieval path
    rag_client: Optional[RAGRetriever] = None
    try:
        if settings.azure_search_endpoint and settings.azure_search_key:
            rag_client = RAGRetriever(settings)
            query_parts = [
                req.selected_app_name,
                req.selected_app_reference_key or "",
                " ".join(context_ids),
                " ".join(story_keys),
                "performance testing project goals impact",
            ]
            rag_query = " ".join(part for part in query_parts if part).strip()
            docs = await rag_client.search(rag_query, top=8)
            for doc in docs:
                rag_evidence.append(SummaryEvidence(
                    source=f"rag:{doc.source}",
                    title=doc.title or doc.doc_id,
                    reference=doc.doc_id,
                    url=doc.url,
                    excerpt=_truncate(doc.content, 280),
                ))
            coverage.rag_status = "ok" if rag_evidence else "empty"
            coverage.rag_count = len(rag_evidence)
        else:
            coverage.rag_status = "skipped"
            coverage.warnings.append("Azure Search is not configured; skipping wiki/RAG retrieval.")
    except Exception as exc:
        coverage.rag_status = "error"
        coverage.warnings.append(f"RAG retrieval unavailable: {exc}")
    finally:
        if rag_client:
            await rag_client.close()

    if not jira_evidence and not rag_evidence:
        raise HTTPException(
            422,
            "No matching Jira or wiki context was found for the searched IDs. Refine IDs and retry.",
        )

    evidence = (jira_evidence + rag_evidence)[:8]
    evidence_payload = [e.model_dump() for e in evidence]
    context_used = {
        "request_payload": {
            "story_keys": story_keys,
            "sprint_name": req.sprint_name,
            "context_ids": context_ids,
            "selected_app_id": req.selected_app_id,
            "selected_app_name": req.selected_app_name,
            "selected_app_reference_key": req.selected_app_reference_key,
            "user_artifacts": req.user_artifacts,
        },
        "evidence_considered": evidence_payload,
    }
    prompt = (
        "You are an AT&T performance test planning assistant. "
        "Generate concise JSON only with keys: project_goals, selected_app_impacts, cross_system_impacts, recommended_focus. "
        "Each key must contain 2-5 bullet-style strings. "
        "Prioritize selected app impact while including cross-system dependencies.\n\n"
        f"Selected app: {req.selected_app_name} ({req.selected_app_id})\n"
        f"Story keys: {story_keys}\n"
        f"Context IDs: {context_ids}\n"
        f"Evidence: {json.dumps(evidence_payload, ensure_ascii=True)}"
        f"{artifacts_section}"
    )

    try:
        llm = get_llm(LLMTask.COMPLEX_REASONING, settings=settings, temperature=0.1)
        llm_result = await llm.ainvoke(prompt)
        llm_text = getattr(llm_result, "content", "") if llm_result is not None else ""
        if isinstance(llm_text, list):
            llm_text = "\n".join(str(item) for item in llm_text)
        json_text = _extract_json_object(str(llm_text))
        parsed = json.loads(json_text)
        response = GenerateSummaryResponse(
            generated_at=datetime.utcnow().isoformat(),
            selected_app_id=req.selected_app_id,
            selected_app_name=req.selected_app_name,
            project_goals=_normalize_list(parsed.get("project_goals") or []),
            selected_app_impacts=_normalize_list(parsed.get("selected_app_impacts") or []),
            cross_system_impacts=_normalize_list(parsed.get("cross_system_impacts") or []),
            recommended_focus=_normalize_list(parsed.get("recommended_focus") or []),
            evidence=evidence,
            source_coverage=coverage,
            context_used=context_used,
        )
        if not response.project_goals:
            response.project_goals = [
                f"Validate release readiness for {req.selected_app_name} with context from Jira and enterprise docs."
            ]
        if not response.selected_app_impacts:
            response.selected_app_impacts = [
                f"Prioritize latency, throughput, and failure behavior for {req.selected_app_name}."
            ]
        if not response.cross_system_impacts:
            response.cross_system_impacts = [
                "Track dependency and integration risks across upstream/downstream systems."
            ]
        if not response.recommended_focus:
            response.recommended_focus = [
                "Translate findings into measurable SLAs and scenario coverage before launch."
            ]
        return response
    except Exception as exc:
        log.warning("summary_llm_fallback", error=str(exc))
        fallback = _fallback_summary(req, jira_evidence, rag_evidence, context_used=context_used)
        fallback.source_coverage = coverage
        fallback.source_coverage.warnings.append(f"LLM summary fallback used: {exc}")
        return fallback


# ── WebSocket ─────────────────────────────────────────────────────────

@app.get("/", response_model=None)
async def root():
    if DASHBOARD_HTML_PATH.exists():
        return FileResponse(DASHBOARD_HTML_PATH)
    return RedirectResponse(url="/docs", status_code=307)

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for real-time pipeline status updates."""
    await ws.accept()
    ws_connections.append(ws)
    log.info("ws_connected", total=len(ws_connections))

    try:
        while True:
            # Keep connection alive, handle incoming messages
            data = await ws.receive_text()
            # Client can send ping/pong or commands
            if data == "ping":
                await ws.send_text(json.dumps({"event": "pong"}))
    except WebSocketDisconnect:
        ws_connections.remove(ws)
        log.info("ws_disconnected", total=len(ws_connections))


# ── Health Check ──────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "active_runs": len(active_states),
        "hitl_pending": len(hitl_events),
        "ws_connections": len(ws_connections),
    }
