# PERF-TEST-AGENT v2.0

## Agentic Performance Testing Framework for AT&T CTx CQE

An end-to-end LLM-powered agentic pipeline that automates the performance testing lifecycle
using ReAct (Reasoning + Acting) agents with human-in-the-loop approval gates.

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                    FastAPI + React Web Dashboard                      │
│                   (HITL Gates / Status / Results)                     │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────────┐
│                   Pipeline Orchestrator (ReAct)                       │
│               LangChain Agent + Tool-Calling Loop                    │
│                                                                       │
│  Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5 ──►       │
│  Story       Test        Env         Script/     Execution           │
│  Analysis    Planning    Triage      Data        & Monitor           │
│                                                                       │
│  ──► Phase 6 ──► Phase 7                                             │
│      Results     Postmortem                                           │
│      & Report    & Feedback                                           │
│                                                                       │
│  [HITL Gate between each phase]                                       │
└──────────────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────────┐
│                      Integration Layer                                │
│                                                                       │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌───────────┐ ┌────────────┐ │
│  │  Jira   │ │ MS Graph│ │ Azure    │ │ Snowflake │ │  Jenkins   │ │
│  │  Client │ │ RAG     │ │ OpenAI   │ │  Client   │ │  Client    │ │
│  └─────────┘ └─────────┘ └──────────┘ └───────────┘ └────────────┘ │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌───────────┐ ┌────────────┐ │
│  │Dynatrace│ │Promethe-│ │   LRE    │ │  JMeter   │ │ SharePoint │ │
│  │ Client  │ │us Client│ │  Client  │ │  Client   │ │  Client    │ │
│  └─────────┘ └─────────┘ └──────────┘ └───────────┘ └────────────┘ │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐                              │
│  │ Solace  │ │ Azure   │ │  ELK     │                              │
│  │   MQ    │ │ AKS CLI │ │  Client  │                              │
│  └─────────┘ └─────────┘ └──────────┘                              │
└──────────────────────────────────────────────────────────────────────┘

## Systems Under Test

Frontend:         OPUS (HTTP/HTML) | Salesforce/Mulesoft (REST) | IDP (REST) | MBiz (REST)
Middleware:        CSI-Core (REST+SOAP) | CSI-CustomerCare (REST) | CSI-OAM (REST)
                   GDDN (Solace MQ) | CAS/iCAS (REST) | NEO (REST)
                   BOBPM (REST) | FOBPM (REST) | EDD (REST) | EGS (REST)
Backend:           TLG/Amdocs (REST) | BSSe (REST) | OMS
Periphery:         Customer Graph (SOLr) | Order Graph (SOLr) | Identity Graph (SOLr) | SOLr DB Cluster
Data:              Oracle | Cassandra DB Cluster | LDAP | Snowflake (prod lookup)
Messaging:         Kafka MQ | IBM MQ | Solace MQ
```

## Phases

| # | Phase | Sub-Agent | Key Inputs | Key Outputs |
|---|-------|-----------|------------|-------------|
| 1 | Story Analysis | StoryAnalyzerAgent | Jira stories, MS Graph RAG, Solution Intent | Test cases, SLA targets, risk profiles |
| 2 | Test Planning | TestPlanGeneratorAgent | Test cases, app .md configs, Dynatrace/Snowflake baselines | Test plan (Word), Jira stories, env requirements |
| 3 | Env Deploy & Triage | EnvConfigAgent | Reference configs (YAML), AKS/AppGW/Amdocs endpoints | Golden config baseline, validation report |
| 4 | Script & Data Creation | ScriptGeneratorAgent | Test plan, app .md, protocol specs | VuGen .c / JMeter .jmx scripts, bulk data |
| 5 | Execution & Monitoring | ExecutionOrchestratorAgent | Scripts, configs, SLA targets | Peak/Break/Stability results, anomaly log |
| 6 | Results & Reporting | ResultsAnalyzerAgent | Execution data, Dynatrace/Prometheus metrics | Word report, SharePoint publish, Jira defects |
| 7 | Postmortem | PostmortemAgent | Phase logs, anomalies, pain points | Snowflake archive, RAG lessons learned |

## Tech Stack

- **Orchestration**: LangChain ReAct Agents (tool-calling loop, max 15 iterations)
- **LLM**: Azure OpenAI
  - `GPT-4o` — complex reasoning, code generation (phases 1, 2, 4, 5, 6, 7)
  - `GPT-4o-mini` — extraction, formatting, classification (phase 3, cost optimization)
- **RAG**: Microsoft Graph (pre-indexed enterprise docs) via Azure AI Search
- **Data**: Snowflake (configs, results, baselines, postmortem archive)
- **CI/CD**: Jenkins pipelines
- **Test Harness**: LoadRunner Enterprise 26.3 (REST API), JMeter
- **Monitoring**: Dynatrace, Prometheus/Grafana, ELK
- **Docs**: MS Word (docx), SharePoint
- **HITL**: FastAPI + React Web Dashboard (WebSocket for real-time updates)
- **Language**: Python 3.11+

## Directory Structure

```
perf-test-agent/
├── src/
│   ├── agents/                   # 7 ReAct phase agents
│   │   ├── base_agent.py         # Abstract base with retry, artifact persistence
│   │   ├── story_analyzer.py     # Phase 1
│   │   ├── test_plan_generator.py # Phase 2
│   │   ├── env_config_checker.py # Phase 3
│   │   ├── script_generator.py   # Phase 4
│   │   ├── execution_orchestrator.py # Phase 5
│   │   ├── results_analyzer.py   # Phase 6
│   │   └── postmortem_agent.py   # Phase 7
│   ├── api/
│   │   └── main.py               # FastAPI REST + WebSocket backend
│   ├── config/
│   │   └── settings.py           # Pydantic settings, LLM task routing
│   ├── integrations/             # External system clients
│   │   ├── azure_openai.py
│   │   ├── jira_client.py
│   │   ├── rag_retriever.py
│   │   ├── snowflake_client.py
│   │   ├── dynatrace_client.py
│   │   ├── lre_client.py
│   │   └── auxiliary_clients.py  # Jenkins, Prometheus, ELK, SharePoint, Solace, AKS
│   ├── models/                   # Pydantic data models
│   │   ├── pipeline_state.py     # Central state (PipelineState, PhaseResult)
│   │   ├── env_config.py         # EnvironmentReference, ConfigValidationReport
│   │   └── ...                   # Per-phase output models
│   ├── prompts/                  # LLM system prompts (per-phase .txt files)
│   │   └── global_system_context.txt
│   ├── tools/
│   │   └── langchain_tools.py    # LangChain-wrapped integration methods
│   ├── utils/
│   │   ├── logging.py            # structlog + Rich
│   │   └── env_reference_store.py # Manifest-driven YAML config management
│   ├── web/                      # React frontend assets
│   └── pipeline.py               # PipelineOrchestrator (main entry point)
├── config/
│   ├── perf_environment_reference.yaml    # Global reference config
│   └── environment_references/
│       ├── manifest.yaml                  # Central registry of all references
│       ├── csi-gateway/                   # Multi-lab: perf.yaml, t3a-n.yaml, t4a-n-1.yaml, t5a-n.yaml
│       ├── csi-customer-care/
│       ├── csi-oam/
│       ├── opus-web/
│       ├── salesforce-mulesoft-proxy/
│       ├── idp-mobile-api/
│       ├── mbiz/
│       ├── gddn-messaging/
│       ├── neo/
│       ├── bobpm/
│       ├── fobpm/
│       ├── edd/
│       ├── egs/
│       ├── tlg-backend/
│       ├── bsse/
│       ├── order-graph/
│       ├── customer-graph/
│       ├── identity-graph/
│       ├── solr-db/
│       ├── cassandra-db/
│       ├── kafka-mq/
│       ├── ibm-mq/
│       └── perf-app-gateway/
├── templates/
│   └── app_configs/_TEMPLATE.md  # App configuration template
├── snowflake/                    # Snowflake SQL scripts
├── tests/
├── pyproject.toml
└── .env.example
```

## Quick Start

```bash
# Install dependencies
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env with your Azure, Jira, Snowflake credentials

# Start the web dashboard
uvicorn src.api.main:app --reload --port 8000

# Start the web dashboard using the workspace venv helper script
bash scripts/run_local.sh

# Smoke-test API startup health endpoint
python -m pytest tests/test_health.py -q

# Run pipeline via CLI
python -m src.pipeline --story-key TELECOM-1234 --interactive

# Run multiple stories
python -m src.pipeline --story-key TELECOM-1234 TELECOM-5678 --interactive

# Run only through Phase 2
python -m src.pipeline --story-key TELECOM-1234 --stop-after test_planning

# Resume from a checkpoint
python -m src.pipeline --resume ./runs/run-20260322-153020-abc123/pipeline_state.json

# Resume starting from a specific phase
python -m src.pipeline --resume ./runs/.../pipeline_state.json --start-from script_data
```

## API Endpoints

### Pipeline

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/pipeline/start` | Start a new pipeline run |
| `GET`  | `/api/pipeline/runs` | List all runs with status/verdict |
| `GET`  | `/api/pipeline/{run_id}/status` | Get phase progress for a run |
| `GET`  | `/api/pipeline/{run_id}/phase/{phase_name}` | Get detailed output for a phase |
| `POST` | `/api/pipeline/{run_id}/approve` | Submit HITL approval/rejection |

### Environment References

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/api/env/configs` | List configs (filterable by app, env, lab, release, variant) |
| `GET`  | `/api/env/configs/{app_key}/{environment}` | Get a specific config with YAML content |
| `PUT`  | `/api/env/configs/{app_key}/{environment}` | Create or update a config |

### Jira

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/api/jira/tickets` | List tickets by application key or team scope |
| `POST` | `/api/jira/tickets` | Create a support/defect ticket |

### Context & Prompts

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/context/summary` | LLM-generated context summary from Jira + RAG |
| `GET`  | `/api/prompts/{phase_id}` | Retrieve the system prompt for a phase |
| `GET`  | `/health` | Health check (active runs, HITL pending, WS connections) |
| `WS`   | `/ws` | WebSocket for real-time pipeline events |

### Pipeline Start Payload

```json
{
  "story_keys": ["TELECOM-1234"],
  "sprint_name": "Sprint 42",
  "selected_app_id": "csi-gateway",
  "selected_app_name": "CSI Common Services Gateway",
  "selected_app_reference_key": "csi",
  "context_ids": ["PID12345", "E1-67890"],
  "hitl_enabled": true,
  "start_phase": "story_analysis",
  "stop_after": "reporting"
}
```

## Environment Reference Store

Environment references are per-application YAML configuration files managed via a central manifest (`config/environment_references/manifest.yaml`). They define endpoints, SLAs, protocols, and monitoring config per app and lab environment.

### Multi-Lab Support

CSI Gateway (and other apps with multi-lab environments) can have separate configs per lab and release code:

```
config/environment_references/csi-gateway/
├── perf.yaml          # PERF / current
├── t3a-n.yaml         # T3A lab / N release
├── t4a-n-1.yaml       # T4A lab / N-1 release
└── t5a-n.yaml         # T5A lab / N release
```

Each entry in `manifest.yaml` carries: `application_key`, `application_name`, `api_variant`, `environment`, `lab_environment`, `release_code`, `path`, `tags`, `last_updated`, `updated_by`.

The `EnvConfigAgent` (Phase 3) reads these references via `env_reference_store` and validates live endpoints against them to produce a golden config baseline.

## Configuration

All settings are loaded from environment variables (via `.env`). The settings module routes LLM calls based on task complexity:

| Task Type | Model | Max Tokens |
|-----------|-------|-----------|
| `COMPLEX_REASONING`, `CODE_GENERATION` | GPT-4o | 8192 |
| `EXTRACTION`, `CLASSIFICATION` | GPT-4o-mini | 1024 |
| `FORMATTING` | GPT-4o-mini | 4096 |

Temperature is fixed at `0.1` across all tasks for consistency.

### Key Environment Variables

```bash
# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://your-instance.openai.azure.com/
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_API_VERSION=2024-08-01-preview
AZURE_OPENAI_DEPLOYMENT_GPT4O=gpt-4o
AZURE_OPENAI_DEPLOYMENT_GPT4O_MINI=gpt-4o-mini

# Azure AI Search (RAG)
AZURE_SEARCH_ENDPOINT=https://your-search.search.windows.net
AZURE_SEARCH_KEY=...
AZURE_SEARCH_INDEX=enterprise-knowledge

# Jira
JIRA_URL=https://jira.corp.att.com
JIRA_USERNAME=...
JIRA_API_TOKEN=...
JIRA_PROJECT_KEY=TELECOM

# Snowflake
SNOWFLAKE_ACCOUNT=...
SNOWFLAKE_USER=...
SNOWFLAKE_PASSWORD=...
SNOWFLAKE_DATABASE=PERF_TESTING
SNOWFLAKE_WAREHOUSE=PERF_WH

# Monitoring
DYNATRACE_URL=https://your-env.live.dynatrace.com
DYNATRACE_API_TOKEN=...
PROMETHEUS_URL=http://prometheus.internal.att.com:9090

# Test Harness
LRE_URL=https://lre.internal.att.com
LRE_USERNAME=...
LRE_PASSWORD=...

# CI/CD
JENKINS_URL=https://jenkins.internal.att.com
JENKINS_USERNAME=...
JENKINS_API_TOKEN=...

# SharePoint / MS Graph
MS_GRAPH_TENANT_ID=...
MS_GRAPH_CLIENT_ID=...
MS_GRAPH_CLIENT_SECRET=...
SHAREPOINT_SITE_ID=...

# Application
LOG_LEVEL=INFO
PIPELINE_RUN_DIR=./runs
WEB_UI_PORT=8000
HITL_ENABLED=true
```
