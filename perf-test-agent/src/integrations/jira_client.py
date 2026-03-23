"""Jira integration client.

Provides tools for:
- Ingesting user stories for Phase 1 analysis
- Creating performance test user stories (Phase 2)
- Logging performance defects (Phase 6)
- Updating story status throughout the pipeline
"""
from __future__ import annotations

from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config.settings import Settings, get_settings
from src.utils.logging import get_logger

log = get_logger(__name__)


class JiraClient:
    """Jira REST API client for the perf-test-agent pipeline."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.jira_url.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=f"{self.base_url}/rest/api/2",
                auth=(self.settings.jira_username, self.settings.jira_api_token),
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Story Ingestion (Phase 1) ─────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_story(self, story_key: str) -> dict[str, Any]:
        """Fetch a single Jira story with all fields."""
        resp = await self.client.get(
            f"/issue/{story_key}",
            params={"expand": "renderedFields"},
        )
        resp.raise_for_status()
        data = resp.json()
        log.info("story_fetched", key=story_key, summary=data["fields"].get("summary", ""))
        return data

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_sprint_stories(self, sprint_name: str) -> list[dict[str, Any]]:
        """Fetch all stories in a sprint via JQL."""
        jql = (
            f'project = {self.settings.jira_project_key} '
            f'AND sprint = "{sprint_name}" '
            f'AND issuetype in (Story, "User Story") '
            f'ORDER BY priority DESC'
        )
        return await self._search_jql(jql)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def get_stories_by_jql(self, jql: str, max_results: int = 100) -> list[dict[str, Any]]:
        """Fetch stories using a custom JQL query."""
        return await self._search_jql(jql, max_results=max_results)

    async def _search_jql(self, jql: str, max_results: int = 100) -> list[dict[str, Any]]:
        """Execute a JQL search and return all matching issues."""
        all_issues: list[dict[str, Any]] = []
        start_at = 0

        while True:
            resp = await self.client.get(
                "/search",
                params={
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": min(50, max_results - len(all_issues)),
                    "expand": "renderedFields",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            issues = data.get("issues", [])
            all_issues.extend(issues)

            if len(all_issues) >= data.get("total", 0) or len(all_issues) >= max_results:
                break
            start_at += len(issues)

        log.info("jql_search_complete", jql=jql[:80], results=len(all_issues))
        return all_issues

    # ── Story Creation (Phase 2) ──────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def create_story(
        self,
        summary: str,
        description: str,
        story_type: str = "Story",
        labels: Optional[list[str]] = None,
        components: Optional[list[str]] = None,
        parent_key: Optional[str] = None,
        custom_fields: Optional[dict[str, Any]] = None,
        project_key: Optional[str] = None,
    ) -> str:
        """Create a new Jira story/issue. Returns the issue key."""
        fields: dict[str, Any] = {
            "project": {"key": project_key or self.settings.jira_project_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": story_type},
        }
        if labels:
            fields["labels"] = labels
        if components:
            fields["components"] = [{"name": c} for c in components]
        if parent_key:
            fields["parent"] = {"key": parent_key}
        if custom_fields:
            fields.update(custom_fields)

        resp = await self.client.post("/issue", json={"fields": fields})
        resp.raise_for_status()
        key = resp.json()["key"]
        log.info("story_created", key=key, summary=summary[:60])
        return key

    # ── Defect Logging (Phase 6) ──────────────────────────────────────

    async def create_defect(
        self,
        summary: str,
        description: str,
        severity: str = "Major",
        affected_system: str = "",
        labels: Optional[list[str]] = None,
        linked_story_key: Optional[str] = None,
    ) -> str:
        """Create a performance defect in Jira."""
        defect_labels = labels or []
        defect_labels.extend(["perf-defect", "automated"])
        if affected_system:
            defect_labels.append(f"system:{affected_system}")

        key = await self.create_story(
            summary=f"[PERF] {summary}",
            description=description,
            story_type="Bug",
            labels=defect_labels,
        )

        # Link to source story if provided
        if linked_story_key:
            await self._create_link(key, linked_story_key, "is caused by")

        log.info("defect_created", key=key, severity=severity, system=affected_system)
        return key

    async def _create_link(self, from_key: str, to_key: str, link_type: str) -> None:
        """Create an issue link between two Jira issues."""
        await self.client.post(
            "/issueLink",
            json={
                "type": {"name": link_type},
                "inwardIssue": {"key": from_key},
                "outwardIssue": {"key": to_key},
            },
        )

    # ── Status Updates ────────────────────────────────────────────────

    async def add_comment(self, issue_key: str, comment: str) -> None:
        """Add a comment to a Jira issue."""
        await self.client.post(
            f"/issue/{issue_key}/comment",
            json={"body": comment},
        )

    async def transition_issue(self, issue_key: str, transition_name: str) -> None:
        """Transition an issue to a new status."""
        # First get available transitions
        resp = await self.client.get(f"/issue/{issue_key}/transitions")
        resp.raise_for_status()
        transitions = resp.json().get("transitions", [])

        target = next((t for t in transitions if t["name"].lower() == transition_name.lower()), None)
        if target:
            await self.client.post(
                f"/issue/{issue_key}/transitions",
                json={"transition": {"id": target["id"]}},
            )
            log.info("issue_transitioned", key=issue_key, to=transition_name)
        else:
            available = [t["name"] for t in transitions]
            log.warning("transition_not_found", key=issue_key, requested=transition_name, available=available)
