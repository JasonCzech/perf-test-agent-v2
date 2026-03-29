"""Production entrypoint for the FastAPI service."""
from __future__ import annotations

import os

import uvicorn

from src.api.main import app
from src.config.settings import get_settings


def main() -> None:
    settings = get_settings()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", settings.web_ui_port))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
