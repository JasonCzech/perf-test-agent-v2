"""LangChain tools for ReAct agents.

Each tool wraps an integration client method, making it available
to the ReAct agent's tool-calling loop.  Tools are organized by
domain and can be selectively registered per phase agent.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from langchain_core.tools import StructuredTool, tool

from src.integrations.jira_client import JiraClient
from src.integrations.rag_retriever import RAGRetriever
from src.integrations.snowflake_client import SnowflakeClient
from src.integrations.dynatrace_client import DynatraceClient
from src.integrations.lre_client import LREClient
from src.integrations.auxiliary_clients import (
    ELKClient,
    JenkinsClient,
    PrometheusClient,
    SharePointClient,
)
from src.utils.logging import get_logger

log = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Jira Tools
# ═══════════════════════════════════════════════════════════════════════

def make_jira_tools(client: Optional[JiraClient] = None) -> list[StructuredTool]:
    """Create Jira-related LangChain tools."""
    jira = client or JiraClient()

    @tool
    def fetch_jira_story(story_key: str) -> str:
        """Fetch a Jira user story by key. Returns the story summary, description, acceptance criteria, and metadata."""
        result = asyncio.get_event_loop().run_until_complete(jira.get_story(story_key))
        fields = result.get("fields", {})
        return json.dumps({
            "key": result.get("key"),
            "summary": fields.get("summary"),
            "description": fields.get("description", ""),
            "acceptance_criteria": fields.get("customfield_10001", ""),  # Adjust field ID
            "priority": fields.get("priority", {}).get("name", ""),
            "labels": fields.get("labels", []),
            "components": [c.get("name") for c in fields.get("components", [])],
            "story_points": fields.get("customfield_10002"),  # Adjust field ID
            "status": fields.get("status", {}).get("name", ""),
        }, indent=2)

    @tool
    def search_jira_stories(jql_query: str) -> str:
        """Search Jira using JQL. Returns matching stories with key, summary, and priority."""
        results = asyncio.get_event_loop().run_until_complete(jira.get_stories_by_jql(jql_query))
        stories = []
        for issue in results[:20]:  # Limit for context window
            f = issue.get("fields", {})
            stories.append({
                "key": issue.get("key"),
                "summary": f.get("summary"),
                "priority": f.get("priority", {}).get("name"),
                "status": f.get("status", {}).get("name"),
            })
        return json.dumps(stories, indent=2)

    @tool
    def create_jira_story(summary: str, description: str, labels: str = "") -> str:
        """Create a new Jira story. labels should be comma-separated. Returns the new issue key."""
        label_list = [l.strip() for l in labels.split(",") if l.strip()] if labels else []
        key = asyncio.get_event_loop().run_until_complete(
            jira.create_story(summary=summary, description=description, labels=label_list)
        )
        return f"Created: {key}"

    @tool
    def create_jira_defect(summary: str, description: str, severity: str = "Major", affected_system: str = "") -> str:
        """Create a performance defect in Jira. Returns the defect key."""
        key = asyncio.get_event_loop().run_until_complete(
            jira.create_defect(summary=summary, description=description, severity=severity, affected_system=affected_system)
        )
        return f"Defect created: {key}"

    return [fetch_jira_story, search_jira_stories, create_jira_story, create_jira_defect]


# ═══════════════════════════════════════════════════════════════════════
# RAG Tools
# ═══════════════════════════════════════════════════════════════════════

def make_rag_tools(retriever: Optional[RAGRetriever] = None) -> list[StructuredTool]:
    """Create RAG retrieval tools."""
    rag = retriever or RAGRetriever()

    @tool
    def search_enterprise_knowledge(query: str, max_results: int = 5) -> str:
        """Search the enterprise knowledge base (SharePoint, PowerBI, ServiceNow) for relevant documentation."""
        docs = asyncio.get_event_loop().run_until_complete(rag.search(query, top=max_results))
        results = []
        for d in docs:
            results.append({
                "title": d.title,
                "source": d.source,
                "url": d.url,
                "content_preview": d.content[:500],
                "score": d.score,
            })
        return json.dumps(results, indent=2)

    @tool
    def search_past_incidents(system_name: str) -> str:
        """Search for past performance incidents related to a specific system."""
        docs = asyncio.get_event_loop().run_until_complete(rag.search_past_incidents(system_name))
        return json.dumps([{"title": d.title, "content": d.content[:300], "url": d.url} for d in docs], indent=2)

    @tool
    def search_architecture_docs(system_name: str) -> str:
        """Search for architecture and design documentation for a system."""
        docs = asyncio.get_event_loop().run_until_complete(rag.search_architecture_docs(system_name))
        return json.dumps([{"title": d.title, "content": d.content[:500], "url": d.url} for d in docs], indent=2)

    return [search_enterprise_knowledge, search_past_incidents, search_architecture_docs]


# ═══════════════════════════════════════════════════════════════════════
# Snowflake Tools
# ═══════════════════════════════════════════════════════════════════════

def make_snowflake_tools(client: Optional[SnowflakeClient] = None) -> list[StructuredTool]:
    """Create Snowflake data tools."""
    sf = client or SnowflakeClient()

    @tool
    def get_performance_baselines(transaction_names: str) -> str:
        """Get historical performance baselines for transactions. transaction_names is comma-separated."""
        names = [n.strip() for n in transaction_names.split(",")]
        baselines = sf.get_transaction_baselines(names)
        return json.dumps(baselines, indent=2)

    return [get_performance_baselines]


# ═══════════════════════════════════════════════════════════════════════
# Dynatrace Tools
# ═══════════════════════════════════════════════════════════════════════

def make_dynatrace_tools(client: Optional[DynatraceClient] = None) -> list[StructuredTool]:
    """Create Dynatrace APM tools."""
    dt = client or DynatraceClient()

    @tool
    def discover_services(tags: str = "") -> str:
        """Discover monitored services in Dynatrace. tags is comma-separated (optional)."""
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        services = asyncio.get_event_loop().run_until_complete(dt.get_services(tag_list))
        return json.dumps([{
            "entity_id": s.get("entityId"),
            "name": s.get("displayName"),
            "tags": [t.get("key") for t in s.get("tags", [])],
        } for s in services[:20]], indent=2)

    @tool
    def get_service_metrics(entity_id: str, hours_back: int = 24) -> str:
        """Get response time, throughput, and error rate metrics for a Dynatrace service entity."""
        metrics = asyncio.get_event_loop().run_until_complete(dt.get_service_metrics(entity_id, hours_back))
        return json.dumps(metrics, indent=2)

    @tool
    def check_active_problems(minutes_back: int = 30) -> str:
        """Check for active problems in Dynatrace within the last N minutes."""
        problems = asyncio.get_event_loop().run_until_complete(dt.get_problems(minutes_back))
        return json.dumps([{
            "id": p.get("problemId"),
            "title": p.get("title"),
            "severity": p.get("severityLevel"),
            "impact": p.get("impactLevel"),
            "affected": [e.get("name") for e in p.get("affectedEntities", [])],
        } for p in problems], indent=2)

    return [discover_services, get_service_metrics, check_active_problems]


# ═══════════════════════════════════════════════════════════════════════
# Monitoring Tools (Prometheus + ELK)
# ═══════════════════════════════════════════════════════════════════════

def make_monitoring_tools(
    prom: Optional[PrometheusClient] = None,
    elk: Optional[ELKClient] = None,
) -> list[StructuredTool]:
    """Create Prometheus and ELK monitoring tools."""
    prometheus = prom or PrometheusClient()
    elk_client = elk or ELKClient()

    @tool
    def query_prometheus(promql: str) -> str:
        """Execute a PromQL query against Prometheus. Returns metric results."""
        results = asyncio.get_event_loop().run_until_complete(prometheus.query(promql))
        return json.dumps(results[:10], indent=2)

    @tool
    def get_cpu_memory(namespace: str) -> str:
        """Get CPU and memory usage for all pods in a Kubernetes namespace."""
        cpu = asyncio.get_event_loop().run_until_complete(prometheus.get_cpu_usage(namespace))
        mem = asyncio.get_event_loop().run_until_complete(prometheus.get_memory_usage(namespace))
        return json.dumps({"cpu": cpu[:10], "memory": mem[:10]}, indent=2)

    @tool
    def search_error_logs(service_name: str, minutes_back: int = 30) -> str:
        """Search for error/fatal logs for a service in Elasticsearch."""
        hits = asyncio.get_event_loop().run_until_complete(
            elk_client.search_error_logs(service_name, minutes_back)
        )
        return json.dumps([{
            "timestamp": h.get("_source", {}).get("@timestamp"),
            "level": h.get("_source", {}).get("level"),
            "message": h.get("_source", {}).get("message", "")[:200],
        } for h in hits[:20]], indent=2)

    return [query_prometheus, get_cpu_memory, search_error_logs]


# ═══════════════════════════════════════════════════════════════════════
# LRE + Jenkins Tools
# ═══════════════════════════════════════════════════════════════════════

def make_execution_tools(
    lre: Optional[LREClient] = None,
    jenkins: Optional[JenkinsClient] = None,
) -> list[StructuredTool]:
    """Create test execution tools (LRE + Jenkins)."""
    lre_client = lre or LREClient()
    jenkins_client = jenkins or JenkinsClient()

    @tool
    def start_lre_test(test_id: int) -> str:
        """Start a LoadRunner Enterprise test run. Returns the run ID."""
        run_id = asyncio.get_event_loop().run_until_complete(lre_client.start_test_run(test_id))
        return f"LRE run started: {run_id}"

    @tool
    def check_lre_run_status(run_id: int) -> str:
        """Check the status of a LoadRunner Enterprise test run."""
        status = asyncio.get_event_loop().run_until_complete(lre_client.get_run_status(run_id))
        return json.dumps(status, indent=2)

    @tool
    def get_lre_results(run_id: int) -> str:
        """Get transaction results for a completed LRE run."""
        txns = asyncio.get_event_loop().run_until_complete(lre_client.get_transaction_summary(run_id))
        return json.dumps(txns, indent=2)

    @tool
    def trigger_jenkins_job(job_name: str, parameters: str = "") -> str:
        """Trigger a Jenkins build. parameters is key=value pairs, comma-separated."""
        params = {}
        if parameters:
            for p in parameters.split(","):
                k, v = p.strip().split("=", 1)
                params[k.strip()] = v.strip()
        queue_id = asyncio.get_event_loop().run_until_complete(
            jenkins_client.trigger_build(job_name, params or None)
        )
        return f"Jenkins build queued: {queue_id}"

    return [start_lre_test, check_lre_run_status, get_lre_results, trigger_jenkins_job]


# ═══════════════════════════════════════════════════════════════════════
# SharePoint Tools
# ═══════════════════════════════════════════════════════════════════════

def make_sharepoint_tools(client: Optional[SharePointClient] = None) -> list[StructuredTool]:
    """Create SharePoint publishing tools."""
    sp = client or SharePointClient()

    @tool
    def upload_to_sharepoint(file_path: str, dest_folder: str, file_name: str) -> str:
        """Upload a file to SharePoint. Returns the web URL."""
        url = asyncio.get_event_loop().run_until_complete(sp.upload_file(file_path, dest_folder, file_name))
        return f"Uploaded to SharePoint: {url}"

    return [upload_to_sharepoint]
