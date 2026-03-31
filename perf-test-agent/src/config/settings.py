"""Centralized configuration for the perf-test-agent pipeline.

All settings are loaded from environment variables (or .env file).
LLM model selection is configured per-task to minimize token usage.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMTask(str, Enum):
    """Task categories for LLM model selection.  Maps to deployment names."""
    COMPLEX_REASONING = "complex"      # Test plan generation, results analysis, postmortem
    EXTRACTION = "extraction"          # Story parsing, config extraction, log parsing
    CODE_GENERATION = "codegen"        # VuGen/JMeter script generation
    FORMATTING = "formatting"          # Report formatting, Jira ticket writing
    CLASSIFICATION = "classification"  # Routing, triage, anomaly classification


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Azure OpenAI ──────────────────────────────────────────────────
    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_openai_deployment_gpt4o: str = "gpt-4o"
    azure_openai_deployment_gpt4o_mini: str = "gpt-4o-mini"

    # ── Azure AI Search (RAG) ─────────────────────────────────────────
    azure_search_endpoint: str = ""
    azure_search_key: str = ""
    azure_search_index: str = "enterprise-knowledge"

    # ── Azure AKS ─────────────────────────────────────────────────────
    azure_subscription_id: str = ""
    azure_resource_group: str = "rg-perf-testing"
    aks_cluster_name: str = "aks-perf-cluster"

    # ── Jira ──────────────────────────────────────────────────────────
    jira_url: str = ""
    jira_username: str = ""
    jira_api_token: str = ""
    jira_project_key: str = "TELECOM"

    # ── Snowflake ─────────────────────────────────────────────────────
    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_password: str = ""
    snowflake_database: str = "PERF_TESTING"
    snowflake_schema: str = "PUBLIC"
    snowflake_warehouse: str = "PERF_WH"
    snowflake_role: str = "PERF_ENGINEER"

    # ── Dynatrace ─────────────────────────────────────────────────────
    dynatrace_url: str = ""
    dynatrace_api_token: str = ""

    # ── Prometheus ────────────────────────────────────────────────────
    prometheus_url: str = ""

    # ── Jenkins ───────────────────────────────────────────────────────
    jenkins_url: str = ""
    jenkins_username: str = ""
    jenkins_api_token: str = ""

    # ── LoadRunner Enterprise ─────────────────────────────────────────
    lre_url: str = ""
    lre_username: str = ""
    lre_password: str = ""
    lre_domain: str = "DEFAULT"
    lre_project: str = "PerfTesting"

    # ── SharePoint / MS Graph ─────────────────────────────────────────
    ms_graph_tenant_id: str = ""
    ms_graph_client_id: str = ""
    ms_graph_client_secret: str = ""
    sharepoint_site_id: str = ""
    sharepoint_drive_id: str = ""

    # ── Solace MQ ─────────────────────────────────────────────────────
    solace_host: str = ""
    solace_vpn: str = ""
    solace_username: str = ""
    solace_password: str = ""

    # ── ELK ───────────────────────────────────────────────────────────
    elasticsearch_url: str = ""
    kibana_url: str = ""

    # ── App Settings ──────────────────────────────────────────────────
    log_level: str = "INFO"
    pipeline_run_dir: Path = Path("./runs")
    web_ui_port: int = 8000
    hitl_enabled: bool = True
    story_analysis_optimizer_enabled: bool = True
    story_analysis_optimizer_max_iterations: int = 5
    story_analysis_optimizer_score_threshold: float = 0.8
    story_analysis_optimizer_min_improvement_delta: float = 0.02
    results_analysis_optimizer_enabled: bool = True
    results_analysis_optimizer_max_iterations: int = 5
    results_analysis_optimizer_score_threshold: float = 0.85
    results_analysis_optimizer_min_improvement_delta: float = 0.02

    def get_llm_deployment(self, task: LLMTask) -> str:
        """Return the appropriate Azure OpenAI deployment for a given task.

        Strategy: Use GPT-4o for complex reasoning, code generation, and
        analysis.  Use GPT-4o-mini for extraction, formatting, and
        classification to minimize token cost.
        """
        if task in (LLMTask.COMPLEX_REASONING, LLMTask.CODE_GENERATION):
            return self.azure_openai_deployment_gpt4o
        return self.azure_openai_deployment_gpt4o_mini

    def get_llm_max_tokens(self, task: LLMTask) -> int:
        """Return appropriate max_tokens for each task type."""
        return {
            LLMTask.COMPLEX_REASONING: 8192,
            LLMTask.CODE_GENERATION: 8192,
            LLMTask.EXTRACTION: 4096,
            LLMTask.FORMATTING: 4096,
            LLMTask.CLASSIFICATION: 1024,
        }[task]


# Singleton
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
