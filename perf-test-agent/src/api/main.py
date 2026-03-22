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
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config.settings import get_settings
from src.models.pipeline_state import (
    PhaseResult,
    PhaseStatus,
    PipelinePhase,
    PipelineState,
)
from src.pipeline import PipelineOrchestrator
from src.utils.logging import get_logger

log = get_logger(__name__)

app = FastAPI(
    title="PERF-TEST-AGENT Dashboard",
    version="2.0.0",
    description="Agentic Performance Testing Pipeline — AT&T CTx CQE",
)

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


class HITLDecisionRequest(BaseModel):
    approved: bool
    notes: str = ""


class PipelineStatusResponse(BaseModel):
    run_id: str
    current_phase: str
    created_at: str
    phase_results: dict[str, Any]
    is_active: bool


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
    """Start a new pipeline run."""
    settings = get_settings()
    settings.hitl_enabled = req.hitl_enabled

    orchestrator = PipelineOrchestrator(settings)
    orchestrator.register_hitl_callback(hitl_callback)

    # Run pipeline in background
    async def run_pipeline():
        try:
            start = PipelinePhase(req.start_phase) if req.start_phase else None
            stop = PipelinePhase(req.stop_after) if req.stop_after else None

            state = await orchestrator.run(
                story_keys=req.story_keys,
                sprint_name=req.sprint_name,
                start_phase=start,
                stop_after=stop,
            )
            active_states[state.run_id] = state
            await broadcast("pipeline_complete", {"run_id": state.run_id})
        except Exception as e:
            log.error("pipeline_error", error=str(e))
            await broadcast("pipeline_error", {"error": str(e)})

    task = asyncio.create_task(run_pipeline())

    # Return immediately with a placeholder run_id
    # The actual run_id will come via WebSocket once the pipeline initializes
    return {"status": "started", "message": "Pipeline starting..."}


@app.post("/api/pipeline/{run_id}/approve")
async def approve_phase(run_id: str, req: HITLDecisionRequest):
    """Approve or reject the current HITL gate."""
    if run_id not in hitl_events:
        raise HTTPException(404, f"No active HITL gate for run {run_id}")

    hitl_decisions[run_id] = (req.approved, req.notes)
    hitl_events[run_id].set()

    return {"status": "decision_recorded", "approved": req.approved}


@app.get("/api/pipeline/{run_id}/status", response_model=PipelineStatusResponse)
async def get_pipeline_status(run_id: str):
    """Get the current status of a pipeline run."""
    state = active_states.get(run_id)
    if not state:
        # Try loading from disk
        state_path = Path(get_settings().pipeline_run_dir) / run_id / "pipeline_state.json"
        if state_path.exists():
            state = PipelineState.model_validate_json(state_path.read_text())
        else:
            raise HTTPException(404, f"Run {run_id} not found")

    return PipelineStatusResponse(
        run_id=state.run_id,
        current_phase=state.current_phase.value,
        created_at=state.created_at.isoformat(),
        phase_results={k: v.model_dump() for k, v in state.phase_results.items()},
        is_active=run_id in hitl_events,
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
                runs.append({
                    "run_id": state.run_id,
                    "current_phase": state.current_phase.value,
                    "created_at": state.created_at.isoformat(),
                    "story_keys": state.jira_story_keys,
                    "is_active": state.run_id in hitl_events,
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


# ── WebSocket ─────────────────────────────────────────────────────────

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
