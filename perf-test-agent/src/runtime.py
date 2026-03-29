"""Runtime path helpers for local and container deployments."""
from __future__ import annotations

import os
from pathlib import Path


def get_repo_root() -> Path:
    """Return the application root, allowing container overrides."""
    override = os.getenv("PERF_TEST_AGENT_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def get_workspace_root() -> Path:
    """Return the workspace root that may also hold dashboard assets."""
    override = os.getenv("PERF_TEST_AGENT_WORKSPACE")
    if override:
        return Path(override).expanduser().resolve()
    return get_repo_root().parent
