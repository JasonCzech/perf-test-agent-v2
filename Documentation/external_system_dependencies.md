# External System Dependencies and Configured Endpoints

This document captures external systems referenced by the codebase and the endpoints currently configured in repository defaults.

Scope and source of truth:
- Environment variable defaults from `perf-test-agent/.env.example`
- Runtime endpoint composition from `perf-test-agent/src/config/settings.py` and `perf-test-agent/src/integrations/*`

## Endpoint Inventory

| External system | Config key(s) | Repo default endpoint value | Effective endpoint/path used by code | Notes |
|---|---|---|---|---|
| Azure OpenAI | `AZURE_OPENAI_ENDPOINT` | `https://your-instance.openai.azure.com/` | Passed directly to `AzureChatOpenAI(azure_endpoint=...)` | Required for agent LLM execution. |
| Azure AI Search (RAG) | `AZURE_SEARCH_ENDPOINT` | `https://your-search.search.windows.net` | `POST {AZURE_SEARCH_ENDPOINT}/indexes/{AZURE_SEARCH_INDEX}/docs/search?api-version=2024-07-01` | Used by `RAGRetriever`; context summary path skips if not configured. |
| Jira | `JIRA_URL` | `https://jira.corp.att.com` | Client base URL: `{JIRA_URL}/rest/api/2` | Used by `JiraClient`; context summary path skips if creds not configured. |
| Dynatrace | `DYNATRACE_URL` | `https://your-env.live.dynatrace.com` | Client base URL: `{DYNATRACE_URL}/api/v2` | Used for service discovery, metrics, and open problems. |
| Microsoft Graph auth (AAD) | `MS_GRAPH_TENANT_ID`, `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET` | `MS_GRAPH_TENANT_ID` empty by default | `https://login.microsoftonline.com/{MS_GRAPH_TENANT_ID}` | Used by MSAL client-credentials flow. |
| Microsoft Graph API (SharePoint publish) | `SHAREPOINT_SITE_ID`, `SHAREPOINT_DRIVE_ID` | IDs empty by default | Base: `https://graph.microsoft.com/v1.0` then `PUT /sites/{site_id}/drives/{drive_id}/root:/{dest_folder}/{file_name}:/content` | Used by `SharePointClient.upload_file`. |
| Prometheus | `PROMETHEUS_URL` | `http://prometheus.internal.att.com:9090` | `{PROMETHEUS_URL}/api/v1/query` and `{PROMETHEUS_URL}/api/v1/query_range` | Used by monitoring tools. |
| Jenkins | `JENKINS_URL` | `https://jenkins.internal.att.com` | Base URL with calls such as `/job/{job_name}/build` and `/job/{job_name}/buildWithParameters` | Used for execution orchestration helper tools. |
| LoadRunner Enterprise (LRE) | `LRE_URL` | `https://lre.internal.att.com` | Base URL with auth `POST /authentication-point/authenticate` and API under `/rest/domains/{domain}/projects/{project}/...` | Used for script upload, test run start, and result retrieval. |
| Elasticsearch (ELK) | `ELASTICSEARCH_URL` | `https://elk.internal.att.com:9200` | Base URL with `POST /{index_pattern}/_search` | Used for log error analysis. |
| Kibana | `KIBANA_URL` | `https://kibana.internal.att.com:5601` | No direct client calls in current code | Config present for operator access; not consumed by integration client yet. |
| Snowflake | `SNOWFLAKE_ACCOUNT` (+ user/pass/role/db/schema/warehouse) | Account empty by default | No explicit URL string in code; Python connector resolves account host from `SNOWFLAKE_ACCOUNT` | Used for baselines, archival, results, and postmortem persistence. |
| Azure AKS metadata | `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AKS_CLUSTER_NAME` | No endpoint value; RG/cluster have defaults | No direct HTTP endpoint usage in current integration clients | Config modeled for environment triage/orchestration context. |
| Solace MQ metadata | `SOLACE_HOST`, `SOLACE_VPN` | `SOLACE_HOST=solace-perf.internal.att.com` | No direct HTTP endpoint usage in current integration clients | Host is configured but no active Solace client implementation in current `src/integrations`. |

## Code References

- `perf-test-agent/src/config/settings.py`
- `perf-test-agent/.env.example`
- `perf-test-agent/src/integrations/azure_openai.py`
- `perf-test-agent/src/integrations/rag_retriever.py`
- `perf-test-agent/src/integrations/jira_client.py`
- `perf-test-agent/src/integrations/dynatrace_client.py`
- `perf-test-agent/src/integrations/auxiliary_clients.py`
- `perf-test-agent/src/integrations/lre_client.py`
- `perf-test-agent/src/integrations/snowflake_client.py`
- `perf-test-agent/src/api/main.py`