"""Dynatrace integration client.

Provides access to Dynatrace APM for:
- Service topology and dependency mapping
- Response time and throughput metrics
- Error rate monitoring
- Real-time monitoring during test execution
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config.settings import Settings, get_settings
from src.utils.logging import get_logger

log = get_logger(__name__)


class DynatraceClient:
    """Dynatrace REST API client."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.dynatrace_url.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=f"{self.base_url}/api/v2",
                headers={
                    "Authorization": f"Api-Token {self.settings.dynatrace_api_token}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Service Discovery ─────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_services(self, tags: Optional[list[str]] = None) -> list[dict[str, Any]]:
        """Get monitored services, optionally filtered by tags."""
        selector = 'type("SERVICE")'
        if tags:
            tag_filter = ",".join(f'tag("{t}")' for t in tags)
            selector += f",{tag_filter}"

        resp = await self.client.get(
            "/entities",
            params={
                "entitySelector": selector,
                "fields": "+tags,+managementZones,+properties",
                "pageSize": 500,
            },
        )
        resp.raise_for_status()
        entities = resp.json().get("entities", [])
        log.info("services_discovered", count=len(entities), tags=tags)
        return entities

    # ── Metrics ───────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_service_metrics(
        self,
        entity_id: str,
        hours_back: int = 24,
    ) -> dict[str, Any]:
        """Get response time, throughput, and error metrics for a service."""
        from_ts = datetime.utcnow() - timedelta(hours=hours_back)
        to_ts = datetime.utcnow()

        metrics = {}
        metric_selectors = {
            "response_time": f"builtin:service.response.time:filter(eq(\"dt.entity.service\",\"{entity_id}\")):avg",
            "throughput": f"builtin:service.requestCount.total:filter(eq(\"dt.entity.service\",\"{entity_id}\")):value",
            "error_rate": f"builtin:service.errors.total.rate:filter(eq(\"dt.entity.service\",\"{entity_id}\")):avg",
        }

        for name, selector in metric_selectors.items():
            try:
                resp = await self.client.get(
                    "/metrics/query",
                    params={
                        "metricSelector": selector,
                        "from": from_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "to": to_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "resolution": "1h",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                result = data.get("result", [])
                if result and result[0].get("data"):
                    values = [
                        dp[1] for dp in result[0]["data"][0].get("values", [])
                        if dp[1] is not None
                    ]
                    if values:
                        metrics[name] = {
                            "avg": sum(values) / len(values),
                            "max": max(values),
                            "min": min(values),
                            "data_points": len(values),
                        }
            except Exception as e:
                log.warning("metric_fetch_failed", metric=name, entity=entity_id, error=str(e))

        return metrics

    # ── Topology / Dependencies ───────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_service_dependencies(self, entity_id: str) -> list[dict[str, Any]]:
        """Get downstream dependencies for a service via Smartscape."""
        resp = await self.client.get(
            f"/entities/{entity_id}",
            params={"fields": "+toRelationships,+fromRelationships"},
        )
        resp.raise_for_status()
        entity = resp.json()

        deps = []
        for rel in entity.get("toRelationships", {}).get("calls", []):
            deps.append({"direction": "downstream", "entity_id": rel, "type": "calls"})
        for rel in entity.get("fromRelationships", {}).get("isCalledBy", []):
            deps.append({"direction": "upstream", "entity_id": rel, "type": "called_by"})

        return deps

    # ── Real-time Monitoring (Phase 5) ────────────────────────────────

    async def get_problems(
        self,
        from_minutes_ago: int = 30,
        management_zone: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Get active problems from Dynatrace."""
        params: dict[str, Any] = {
            "from": f"now-{from_minutes_ago}m",
            "problemSelector": "status(\"OPEN\")",
        }
        if management_zone:
            params["problemSelector"] += f",managementZones(\"{management_zone}\")"

        resp = await self.client.get("/problems", params=params)
        resp.raise_for_status()
        problems = resp.json().get("problems", [])
        log.info("problems_fetched", count=len(problems))
        return problems
