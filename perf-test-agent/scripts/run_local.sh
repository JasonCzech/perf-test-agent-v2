#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_DIR="$(cd "${PROJECT_DIR}/.." && pwd)"
PYTHON_BIN="${WORKSPACE_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Missing Python executable: ${PYTHON_BIN}" >&2
  echo "Create the venv first (example): python3 -m venv ${WORKSPACE_DIR}/.venv" >&2
  exit 1
fi

exec "${PYTHON_BIN}" -m uvicorn src.api.main:app \
  --app-dir "${PROJECT_DIR}" \
  --host "${HOST:-127.0.0.1}" \
  --port "${PORT:-8000}" \
  --reload
