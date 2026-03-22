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

Frontend:         OPUS (HTTP/HTML) | Salesforce/Mulesoft (REST) | IDP (REST)
Middleware:        CSI (REST+SOAP) | GDDN (Solace MQ) | CAS/iCAS (REST)
Backend:           TLG/Amdocs (REST) | BSSe | OMS
Periphery:         Customer Graph | Order Graph | Identity Graph (SOLr)
Data:              Oracle | Cassandra | LDAP | Snowflake (prod lookup)
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

- **Orchestration**: LangChain ReAct Agents (tool-calling loop)
- **LLM**: Azure OpenAI (GPT-4o for complex reasoning, GPT-4o-mini for extraction/formatting)
- **RAG**: Microsoft Graph (pre-indexed) via Azure AI Search
- **Data**: Snowflake (configs, results, baselines, postmortem)
- **CI/CD**: Jenkins pipelines
- **Test Harness**: LoadRunner Enterprise 26.3 (REST API), JMeter, K6/Grafana (future)
- **Monitoring**: Dynatrace, Prometheus/Grafana, ELK
- **Docs**: MS Word (docx), SharePoint
- **HITL**: FastAPI + React Web Dashboard
- **Language**: Python 3.11+
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

# Run pipeline via CLI (alternative)
python -m src.pipeline --story-key TELECOM-1234 --interactive

# Run a single phase
python -m src.agents.story_analyzer --story-key TELECOM-1234
```
