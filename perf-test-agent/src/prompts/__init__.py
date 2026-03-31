"""Prompt loading utilities for pipeline agents.

Each phase agent has a dedicated prompt file in src/prompts/ containing
the full system instructions for that agent's LLM calls. The global
system context is prepended to every phase prompt automatically.

Prompt files:
    global_system_context.txt       - Shared AT&T topology and standards
    phase1_story_analyzer.txt       - Phase 1: Story Analysis
    phase2_test_plan_generator.txt  - Phase 2: Test Planning
    phase3_env_config_checker.txt   - Phase 3: Environment Config Checker
    phase4_script_generator.txt     - Phase 4: Script & Data Generation
    phase5_execution_orchestrator.txt - Phase 5: Execution & Monitoring
    phase6_results_analyzer.txt     - Phase 6: Results Analysis & Reporting
    phase7_postmortem.txt           - Phase 7: Postmortem & Feedback Loop
"""
from pathlib import Path
from functools import lru_cache

PROMPTS_DIR = Path(__file__).parent

PHASE_PROMPT_FILES = {
    "story_analysis": "phase1_story_analyzer.txt",
    "story_analysis_evaluator": "phase1_story_analyzer_evaluator.txt",
    "test_planning": "phase2_test_plan_generator.txt",
    "env_triage": "phase3_env_config_checker.txt",
    "script_data": "phase4_script_generator.txt",
    "execution": "phase5_execution_orchestrator.txt",
    "reporting": "phase6_results_analyzer.txt",
    "results_analysis_evaluator": "phase6_results_analyzer_evaluator.txt",
    "postmortem": "phase7_postmortem.txt",
}


@lru_cache(maxsize=None)
def load_global_context() -> str:
    """Load the shared global system context prepended to all prompts."""
    return (PROMPTS_DIR / "global_system_context.txt").read_text(encoding="utf-8").strip()


@lru_cache(maxsize=None)
def load_phase_prompt(phase_id: str) -> str:
    """Load a phase-specific prompt with global context prepended."""
    if phase_id not in PHASE_PROMPT_FILES:
        raise KeyError(f"Unknown phase_id '{phase_id}'. Valid: {list(PHASE_PROMPT_FILES.keys())}")
    path = PROMPTS_DIR / PHASE_PROMPT_FILES[phase_id]
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    global_ctx = load_global_context()
    phase_prompt = path.read_text(encoding="utf-8").strip()
    return f"{global_ctx}\n\n---\n\n{phase_prompt}"


def load_prompt(phase_id: str, hitl_context: str | None = None) -> str:
    """Load complete prompt for a phase, optionally with HITL context appended.

    Args:
        phase_id: Phase identifier (e.g., "story_analysis")
        hitl_context: Optional human-provided context from previous phase approval
    """
    prompt = load_phase_prompt(phase_id)
    if hitl_context and hitl_context.strip():
        prompt += (
            f"\n\n## HITL Context (from previous phase reviewer)\n"
            f"The human reviewer provided the following additional context. "
            f"Incorporate these instructions into your analysis:\n\n"
            f"{hitl_context.strip()}"
        )
    return prompt