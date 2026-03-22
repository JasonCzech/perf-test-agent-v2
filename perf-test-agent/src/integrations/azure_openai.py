"""Azure OpenAI client wrapper.

Provides a unified interface to Azure OpenAI with task-based model routing.
All LLM interactions in the pipeline go through this client.
"""
from __future__ import annotations

from typing import Optional

from langchain_openai import AzureChatOpenAI

from src.config.settings import LLMTask, Settings, get_settings
from src.utils.logging import get_logger

log = get_logger(__name__)


def get_llm(
    task: LLMTask,
    settings: Optional[Settings] = None,
    temperature: float = 0.1,
) -> AzureChatOpenAI:
    """Get an Azure OpenAI LLM instance configured for the given task.

    Args:
        task: The task type — determines which model deployment to use.
        settings: Optional settings override.
        temperature: LLM temperature (default 0.1 for consistency).

    Returns:
        Configured AzureChatOpenAI instance.
    """
    s = settings or get_settings()
    deployment = s.get_llm_deployment(task)
    max_tokens = s.get_llm_max_tokens(task)

    llm = AzureChatOpenAI(
        azure_endpoint=s.azure_openai_endpoint,
        api_key=s.azure_openai_api_key,
        api_version=s.azure_openai_api_version,
        azure_deployment=deployment,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    log.info("llm_created", task=task.value, deployment=deployment, max_tokens=max_tokens)
    return llm
