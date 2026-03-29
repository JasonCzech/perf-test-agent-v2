"""Pipeline Orchestrator — the central coordinator for all phases.

Implements a LangChain ReAct agent that sequences through the 7 phases,
with HITL (Human-in-the-Loop) approval gates between each phase.

The orchestrator:
1. Initializes pipeline state from Jira story keys or sprint
2. Runs each phase agent sequentially
3. Persists state between phases
4. Publishes phase results to the Web UI for HITL review
5. Waits for human approval before proceeding
6. Handles failures and enables phase retry/skip
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.agents.base_agent import BaseAgent
from src.config.settings import Settings, get_settings
from src.models.pipeline_state import (
    PhaseResult,
    PhaseStatus,
    PipelinePhase,
    PipelineState,
)
from src.utils.logging import get_logger, setup_logging

log = get_logger(__name__)

# Phase execution order
PHASE_ORDER: list[PipelinePhase] = [
    PipelinePhase.STORY_ANALYSIS,
    PipelinePhase.TEST_PLANNING,
    PipelinePhase.ENV_TRIAGE,
    PipelinePhase.SCRIPT_DATA,
    PipelinePhase.EXECUTION,
    PipelinePhase.REPORTING,
    PipelinePhase.POSTMORTEM,
]


class PipelineOrchestrator:
    """End-to-end pipeline orchestrator with HITL gates.

    Usage:
        orchestrator = PipelineOrchestrator()
        state = await orchestrator.run(story_keys=["TELECOM-1234"])

    Or resume from a saved state:
        state = PipelineState.model_validate_json(saved_json)
        state = await orchestrator.resume(state)
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        setup_logging(self.settings.log_level)
        self._agents: dict[PipelinePhase, BaseAgent] = {}
        self._hitl_callback: Optional[Any] = None  # Set by Web UI

    def register_hitl_callback(self, callback: Any) -> None:
        """Register the HITL callback for Web UI integration.

        The callback receives (phase, phase_result, state) and must
        return (approved: bool, notes: str, modified_state: PipelineState).
        """
        self._hitl_callback = callback

    def _get_agent(self, phase: PipelinePhase) -> BaseAgent:
        """Lazy-load the agent for a phase."""
        if phase not in self._agents:
            agent_class = self._resolve_agent_class(phase)
            self._agents[phase] = agent_class(self.settings)
        return self._agents[phase]

    @staticmethod
    def _resolve_agent_class(phase: PipelinePhase) -> type:
        """Import and return the agent class for a phase.

        Lazy imports to avoid circular dependencies and speed up startup.
        """
        if phase == PipelinePhase.STORY_ANALYSIS:
            from src.agents.story_analyzer import StoryAnalyzerAgent
            return StoryAnalyzerAgent
        elif phase == PipelinePhase.TEST_PLANNING:
            from src.agents.test_plan_generator import TestPlanGeneratorAgent
            return TestPlanGeneratorAgent
        elif phase == PipelinePhase.ENV_TRIAGE:
            from src.agents.env_config_checker import EnvConfigAgent
            return EnvConfigAgent
        elif phase == PipelinePhase.SCRIPT_DATA:
            from src.agents.script_generator import ScriptGeneratorAgent
            return ScriptGeneratorAgent
        elif phase == PipelinePhase.EXECUTION:
            from src.agents.execution_orchestrator import ExecutionOrchestratorAgent
            return ExecutionOrchestratorAgent
        elif phase == PipelinePhase.REPORTING:
            from src.agents.results_analyzer import ResultsAnalyzerAgent
            return ResultsAnalyzerAgent
        elif phase == PipelinePhase.POSTMORTEM:
            from src.agents.postmortem_agent import PostmortemAgent
            return PostmortemAgent
        else:
            raise ValueError(f"Unknown phase: {phase}")

    # ── Pipeline Execution ────────────────────────────────────────────

    @staticmethod
    def _next_phase(current_phase: PipelinePhase) -> Optional[PipelinePhase]:
        try:
            idx = PHASE_ORDER.index(current_phase)
        except ValueError:
            return None
        if idx >= len(PHASE_ORDER) - 1:
            return None
        return PHASE_ORDER[idx + 1]

    async def initialize_run(
        self,
        story_keys: Optional[list[str]] = None,
        sprint_name: Optional[str] = None,
        context_ids: Optional[list[str]] = None,
        selected_app_id: Optional[str] = None,
        selected_app_name: Optional[str] = None,
        selected_app_reference_key: Optional[str] = None,
        generated_summary: Optional[dict[str, Any]] = None,
        user_artifacts: Optional[dict[str, Any]] = None,
        start_phase: Optional[PipelinePhase] = None,
    ) -> PipelineState:
        """Initialize a run in prompt-review state without executing a phase."""
        run_id = f"run-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        initial_phase = start_phase or PipelinePhase.STORY_ANALYSIS

        state = PipelineState(
            run_id=run_id,
            jira_story_keys=story_keys or [],
            sprint_name=sprint_name,
            context_ids=context_ids or [],
            selected_app_id=selected_app_id,
            selected_app_name=selected_app_name,
            selected_app_reference_key=selected_app_reference_key,
            generated_summary=generated_summary,
            user_artifacts=user_artifacts,
            current_phase=initial_phase,
        )

        state.set_phase_result(
            PhaseResult(
                phase=initial_phase,
                status=PhaseStatus.PROMPT_REVIEW,
                summary="Ready for prompt review and execution.",
            )
        )
        self._save_state(state)
        log.info("pipeline_initialized", run_id=run_id, current_phase=initial_phase.value)
        return state

    async def execute_phase(
        self,
        state: PipelineState,
        phase: Optional[PipelinePhase] = None,
        prompt_override: Optional[str] = None,
        pre_execution_context: Optional[str] = None,
    ) -> PipelineState:
        """Execute a single phase without auto-advancing to the next phase."""
        phase_to_run = phase or state.current_phase
        state.current_phase = phase_to_run

        if prompt_override is not None:
            next_prompt = prompt_override.strip()
            if next_prompt:
                state.phase_prompt_overrides[phase_to_run.value] = next_prompt
            else:
                state.phase_prompt_overrides.pop(phase_to_run.value, None)

        if pre_execution_context is not None:
            next_context = pre_execution_context.strip()
            if next_context:
                state.phase_pre_execution_context[phase_to_run.value] = next_context
            else:
                state.phase_pre_execution_context.pop(phase_to_run.value, None)

        phase_result = state.get_phase_result(phase_to_run)
        if phase_result is None:
            phase_result = PhaseResult(
                phase=phase_to_run,
                status=PhaseStatus.PROMPT_REVIEW,
                summary="Ready for prompt review and execution.",
            )
            state.set_phase_result(phase_result)

        phase_result.status = PhaseStatus.RUNNING
        phase_result.started_at = datetime.utcnow()
        state.set_phase_result(phase_result)
        self._save_state(state)

        agent = self._get_agent(phase_to_run)
        state = await agent.run(state)

        completed_result = state.get_phase_result(phase_to_run)
        if completed_result and completed_result.status == PhaseStatus.AWAITING_APPROVAL:
            completed_result.status = PhaseStatus.RESULTS_READY
            state.set_phase_result(completed_result)

        self._save_state(state)
        log.info("phase_execution_complete", run_id=state.run_id, phase=phase_to_run.value)
        return state

    def approve_phase(
        self,
        state: PipelineState,
        phase: Optional[PipelinePhase] = None,
        approved: bool = True,
        notes: str = "",
        advance: bool = True,
    ) -> PipelineState:
        """Approve/reject current phase and optionally advance to next phase prompt-review."""
        phase_to_update = phase or state.current_phase
        phase_result = state.get_phase_result(phase_to_update)
        if not phase_result:
            raise ValueError(f"No phase result found for {phase_to_update.value}")

        clean_notes = notes.strip()
        state.phase_post_execution_notes[phase_to_update.value] = clean_notes

        if approved:
            phase_result.status = PhaseStatus.COMPLETED
            phase_result.completed_at = datetime.utcnow()
            phase_result.approval_notes = clean_notes
            state.set_phase_result(phase_result)

            if advance:
                next_phase = self._next_phase(phase_to_update)
                if next_phase:
                    state.current_phase = next_phase
                    next_result = state.get_phase_result(next_phase)
                    if not next_result:
                        next_result = PhaseResult(
                            phase=next_phase,
                            status=PhaseStatus.PROMPT_REVIEW,
                            summary="Ready for prompt review and execution.",
                        )
                    else:
                        next_result.status = PhaseStatus.PROMPT_REVIEW
                    state.set_phase_result(next_result)
        else:
            phase_result.status = PhaseStatus.REJECTED
            phase_result.approval_notes = clean_notes
            state.set_phase_result(phase_result)

        self._save_state(state)
        return state

    def mark_phase_for_modify(
        self,
        state: PipelineState,
        phase: Optional[PipelinePhase] = None,
        notes: str = "",
    ) -> PipelineState:
        """Move a phase back to prompt review for edit/re-run."""
        phase_to_modify = phase or state.current_phase
        phase_result = state.get_phase_result(phase_to_modify)
        if not phase_result:
            phase_result = PhaseResult(
                phase=phase_to_modify,
                status=PhaseStatus.PROMPT_REVIEW,
                summary="Ready for prompt review and execution.",
            )
        else:
            phase_result.status = PhaseStatus.PROMPT_REVIEW
            phase_result.approval_notes = notes.strip()
        state.current_phase = phase_to_modify
        state.set_phase_result(phase_result)
        self._save_state(state)
        return state

    async def run(
        self,
        story_keys: Optional[list[str]] = None,
        sprint_name: Optional[str] = None,
        context_ids: Optional[list[str]] = None,
        selected_app_id: Optional[str] = None,
        selected_app_name: Optional[str] = None,
        selected_app_reference_key: Optional[str] = None,
        generated_summary: Optional[dict[str, Any]] = None,
        user_artifacts: Optional[dict[str, Any]] = None,
        start_phase: Optional[PipelinePhase] = None,
        stop_after: Optional[PipelinePhase] = None,
    ) -> PipelineState:
        """Run the full pipeline from the beginning.

        Args:
            story_keys: Jira story keys to analyze.
            sprint_name: Sprint name (alternative to story_keys).
            start_phase: Phase to start from (default: STORY_ANALYSIS).
            stop_after: Phase to stop after (default: run all).
        """
        state = await self.initialize_run(
            story_keys=story_keys,
            sprint_name=sprint_name,
            context_ids=context_ids,
            selected_app_id=selected_app_id,
            selected_app_name=selected_app_name,
            selected_app_reference_key=selected_app_reference_key,
            generated_summary=generated_summary,
            user_artifacts=user_artifacts,
            start_phase=start_phase,
        )

        log.info("pipeline_starting", run_id=state.run_id, stories=story_keys, sprint=sprint_name)

        state = await self._execute_phases(state, start_phase, stop_after)

        # Save final state
        self._save_state(state)

        log.info("pipeline_complete", run_id=state.run_id, current_phase=state.current_phase.value)
        return state

    async def resume(
        self,
        state: PipelineState,
        stop_after: Optional[PipelinePhase] = None,
    ) -> PipelineState:
        """Resume a pipeline from its current phase."""
        log.info("pipeline_resuming", run_id=state.run_id, from_phase=state.current_phase.value)
        return await self._execute_phases(state, state.current_phase, stop_after)

    async def _execute_phases(
        self,
        state: PipelineState,
        start_phase: Optional[PipelinePhase] = None,
        stop_after: Optional[PipelinePhase] = None,
    ) -> PipelineState:
        """Execute phases in sequence with HITL gates."""
        started = start_phase is None

        for phase in PHASE_ORDER:
            if not started:
                if phase == start_phase:
                    started = True
                else:
                    continue

            # Skip already-completed phases
            existing = state.get_phase_result(phase)
            if existing and existing.status == PhaseStatus.COMPLETED:
                log.info("phase_skipping_completed", phase=phase.value)
                continue

            state.current_phase = phase
            log.info("phase_entering", phase=phase.value)

            # Execute the phase agent
            agent = self._get_agent(phase)
            state = await agent.run(state)

            # Save intermediate state
            self._save_state(state)

            # Check for failure
            phase_result = state.get_phase_result(phase)
            if phase_result and phase_result.status == PhaseStatus.FAILED:
                log.error("phase_failed_stopping", phase=phase.value, errors=phase_result.errors)
                break

            # HITL Gate
            if self.settings.hitl_enabled and phase_result:
                approved, notes = await self._hitl_gate(phase, phase_result, state)

                if approved:
                    phase_result.status = PhaseStatus.COMPLETED
                    phase_result.approval_notes = notes
                    log.info("phase_approved", phase=phase.value)
                else:
                    phase_result.status = PhaseStatus.REJECTED
                    phase_result.approval_notes = notes
                    log.warning("phase_rejected", phase=phase.value, notes=notes)
                    break

                state.set_phase_result(phase_result)
                self._save_state(state)

            # Check stop condition
            if stop_after and phase == stop_after:
                log.info("stopping_after_phase", phase=phase.value)
                break

        return state

    async def _hitl_gate(
        self,
        phase: PipelinePhase,
        phase_result: PhaseResult,
        state: PipelineState,
    ) -> tuple[bool, str]:
        """Human-in-the-loop approval gate.

        If a callback is registered (Web UI), it's used.  Otherwise,
        auto-approve (for testing/batch mode).
        """
        if self._hitl_callback:
            return await self._hitl_callback(phase, phase_result, state)

        # No callback registered — auto-approve in non-interactive mode
        log.warning("hitl_auto_approved", phase=phase.value, reason="no_callback_registered")
        return True, "Auto-approved (no HITL callback)"

    # ── State Persistence ─────────────────────────────────────────────

    def _save_state(self, state: PipelineState) -> None:
        """Persist pipeline state to disk."""
        run_dir = Path(self.settings.pipeline_run_dir) / state.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        state_path = run_dir / "pipeline_state.json"
        state_path.write_text(state.model_dump_json(indent=2))

    @staticmethod
    def load_state(state_path: str) -> PipelineState:
        """Load pipeline state from a saved file."""
        return PipelineState.model_validate_json(Path(state_path).read_text())


# ── CLI Entry Point ───────────────────────────────────────────────────

async def main():
    """CLI entry point for running the pipeline."""
    import argparse

    parser = argparse.ArgumentParser(description="PERF-TEST-AGENT Pipeline")
    parser.add_argument("--story-key", nargs="+", help="Jira story key(s)")
    parser.add_argument("--sprint", help="Sprint name")
    parser.add_argument("--start-from", help="Phase to start from")
    parser.add_argument("--stop-after", help="Phase to stop after")
    parser.add_argument("--resume", help="Path to saved pipeline_state.json")
    parser.add_argument("--no-hitl", action="store_true", help="Disable HITL gates")

    args = parser.parse_args()

    settings = get_settings()
    if args.no_hitl:
        settings.hitl_enabled = False

    orchestrator = PipelineOrchestrator(settings)

    if args.resume:
        state = PipelineOrchestrator.load_state(args.resume)
        state = await orchestrator.resume(state)
    else:
        start = PipelinePhase(args.start_from) if args.start_from else None
        stop = PipelinePhase(args.stop_after) if args.stop_after else None
        state = await orchestrator.run(
            story_keys=args.story_key,
            sprint_name=args.sprint,
            start_phase=start,
            stop_after=stop,
        )

    print(f"\nPipeline complete. Run ID: {state.run_id}")
    print(f"Current phase: {state.current_phase.value}")
    for phase_name, result in state.phase_results.items():
        status_icon = "✓" if result.status == PhaseStatus.COMPLETED else "✗"
        print(f"  {status_icon} {phase_name}: {result.status.value}")


def cli() -> None:
    import asyncio

    asyncio.run(main())


if __name__ == "__main__":
    cli()
