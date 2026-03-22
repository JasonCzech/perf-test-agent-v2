"""Additional integration clients: Jenkins, Prometheus, SharePoint, ELK."""
from __future__ import annotations

from typing import Any, Optional

import httpx
import msal
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config.settings import Settings, get_settings
from src.utils.logging import get_logger

log = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Jenkins Client
# ═══════════════════════════════════════════════════════════════════════

class JenkinsClient:
    """Jenkins CI/CD client for triggering and monitoring pipeline jobs."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.jenkins_url.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                auth=(self.settings.jenkins_username, self.settings.jenkins_api_token),
                timeout=30.0,
            )
        return self._client

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def trigger_build(self, job_name: str, params: Optional[dict[str, str]] = None) -> int:
        """Trigger a Jenkins build. Returns the queue item ID."""
        url = f"/job/{job_name}/buildWithParameters" if params else f"/job/{job_name}/build"
        resp = await self.client.post(url, params=params)
        resp.raise_for_status()
        queue_url = resp.headers.get("Location", "")
        queue_id = int(queue_url.rstrip("/").split("/")[-1]) if queue_url else 0
        log.info("jenkins_build_triggered", job=job_name, queue_id=queue_id)
        return queue_id

    async def get_build_status(self, job_name: str, build_number: int) -> dict[str, Any]:
        """Get build status and result."""
        resp = await self.client.get(f"/job/{job_name}/{build_number}/api/json")
        resp.raise_for_status()
        return resp.json()

    async def get_build_console(self, job_name: str, build_number: int) -> str:
        """Get build console output."""
        resp = await self.client.get(f"/job/{job_name}/{build_number}/consoleText")
        resp.raise_for_status()
        return resp.text


# ═══════════════════════════════════════════════════════════════════════
# Prometheus Client
# ═══════════════════════════════════════════════════════════════════════

class PrometheusClient:
    """Prometheus client for infrastructure metrics."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.prometheus_url.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        return self._client

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def query(self, promql: str) -> list[dict[str, Any]]:
        """Execute an instant PromQL query."""
        resp = await self.client.get("/api/v1/query", params={"query": promql})
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {data}")
        return data.get("data", {}).get("result", [])

    async def query_range(
        self, promql: str, start: str, end: str, step: str = "60s"
    ) -> list[dict[str, Any]]:
        """Execute a range PromQL query."""
        resp = await self.client.get(
            "/api/v1/query_range",
            params={"query": promql, "start": start, "end": end, "step": step},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("result", [])

    async def get_cpu_usage(self, namespace: str) -> list[dict[str, Any]]:
        """Get CPU usage for pods in a namespace."""
        return await self.query(
            f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[5m])) by (pod)'
        )

    async def get_memory_usage(self, namespace: str) -> list[dict[str, Any]]:
        """Get memory usage for pods in a namespace."""
        return await self.query(
            f'sum(container_memory_working_set_bytes{{namespace="{namespace}"}}) by (pod)'
        )

    async def get_jvm_heap(self, namespace: str) -> list[dict[str, Any]]:
        """Get JVM heap usage for Java services."""
        return await self.query(
            f'jvm_memory_used_bytes{{namespace="{namespace}",area="heap"}} '
            f'/ jvm_memory_max_bytes{{namespace="{namespace}",area="heap"}}'
        )


# ═══════════════════════════════════════════════════════════════════════
# SharePoint / MS Graph Client
# ═══════════════════════════════════════════════════════════════════════

class SharePointClient:
    """Microsoft Graph client for SharePoint document publishing."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._token: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_token(self) -> str:
        """Get an access token via MSAL client credentials."""
        if self._token:
            return self._token

        app = msal.ConfidentialClientApplication(
            self.settings.ms_graph_client_id,
            authority=f"https://login.microsoftonline.com/{self.settings.ms_graph_tenant_id}",
            client_credential=self.settings.ms_graph_client_secret,
        )
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in result:
            raise RuntimeError(f"MSAL token acquisition failed: {result.get('error_description')}")
        self._token = result["access_token"]
        return self._token

    @property
    async def client(self) -> httpx.AsyncClient:
        token = await self._get_token()
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url="https://graph.microsoft.com/v1.0",
                headers={"Authorization": f"Bearer {token}"},
                timeout=60.0,
            )
        return self._client

    async def upload_file(self, file_path: str, dest_folder: str, file_name: str) -> str:
        """Upload a file to SharePoint. Returns the web URL."""
        c = await self.client
        site_id = self.settings.sharepoint_site_id
        drive_id = self.settings.sharepoint_drive_id

        with open(file_path, "rb") as f:
            content = f.read()

        upload_path = f"{dest_folder}/{file_name}".lstrip("/")
        resp = await c.put(
            f"/sites/{site_id}/drives/{drive_id}/root:/{upload_path}:/content",
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        resp.raise_for_status()
        web_url = resp.json().get("webUrl", "")
        log.info("sharepoint_upload_complete", file=file_name, url=web_url)
        return web_url


# ═══════════════════════════════════════════════════════════════════════
# ELK Client
# ═══════════════════════════════════════════════════════════════════════

class ELKClient:
    """Elasticsearch client for log analysis."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.settings.elasticsearch_url,
                timeout=30.0,
            )
        return self._client

    async def search_logs(
        self,
        index_pattern: str,
        query: str,
        time_range_minutes: int = 30,
        size: int = 100,
    ) -> list[dict[str, Any]]:
        """Search application logs in Elasticsearch."""
        body = {
            "size": size,
            "query": {
                "bool": {
                    "must": [
                        {"query_string": {"query": query}},
                        {"range": {"@timestamp": {"gte": f"now-{time_range_minutes}m"}}},
                    ]
                }
            },
            "sort": [{"@timestamp": {"order": "desc"}}],
        }

        resp = await self.client.post(f"/{index_pattern}/_search", json=body)
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        log.info("elk_search_complete", index=index_pattern, query=query[:40], hits=len(hits))
        return hits

    async def search_error_logs(
        self,
        service_name: str,
        time_range_minutes: int = 30,
        patterns: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Search for error patterns in service logs."""
        default_patterns = [
            "OutOfMemoryError", "StackOverflowError", "Connection refused",
            "timeout", "Circuit breaker", "503", "500", "FATAL",
        ]
        search_patterns = patterns or default_patterns
        query = f'service:"{service_name}" AND level:(ERROR OR FATAL) AND ({" OR ".join(search_patterns)})'
        return await self.search_logs(f"{service_name}-*", query, time_range_minutes)
