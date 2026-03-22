"""LoadRunner Enterprise 26.3 REST API client.

Provides integration with on-prem LRE via the Performance Center REST API for:
- Uploading VuGen scripts
- Creating and configuring test sets
- Triggering test runs
- Polling run status and collecting results
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config.settings import Settings, get_settings
from src.utils.logging import get_logger

log = get_logger(__name__)


class LREClient:
    """LoadRunner Enterprise (Performance Center) REST API client.

    Authentication uses the LRE session-based auth flow:
    1. POST /authentication-point/authenticate (basic auth)
    2. Receive LWSSO_COOKIE_KEY
    3. Use cookie for subsequent API calls
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.lre_url.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None
        self._authenticated = False

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Content-Type": "application/json"},
                timeout=60.0,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Authentication ────────────────────────────────────────────────

    async def authenticate(self) -> None:
        """Authenticate with LRE and establish session."""
        resp = await self.client.post(
            "/authentication-point/authenticate",
            auth=(self.settings.lre_username, self.settings.lre_password),
        )
        resp.raise_for_status()

        # The LWSSO cookie is automatically captured by httpx cookie jar
        # Now sign in to the specific domain/project
        resp = await self.client.post(
            f"/rest/domains/{self.settings.lre_domain}/projects/{self.settings.lre_project}/session",
        )
        if resp.status_code in (200, 201):
            self._authenticated = True
            log.info("lre_authenticated", domain=self.settings.lre_domain, project=self.settings.lre_project)
        else:
            raise RuntimeError(f"LRE session creation failed: {resp.status_code}")

    async def _ensure_auth(self) -> None:
        if not self._authenticated:
            await self.authenticate()

    # ── Script Management ─────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def upload_script(
        self,
        script_name: str,
        script_path: str,
        folder_id: int,
    ) -> int:
        """Upload a VuGen script (.zip) to the LRE script repository.

        Returns the script ID.
        """
        await self._ensure_auth()

        # Create script entity
        resp = await self.client.post(
            f"/rest/domains/{self.settings.lre_domain}/projects/{self.settings.lre_project}/scripts",
            json={
                "Name": script_name,
                "FolderID": folder_id,
            },
        )
        resp.raise_for_status()
        script_id = resp.json().get("ID")

        # Upload script content
        with open(script_path, "rb") as f:
            resp = await self.client.post(
                f"/rest/domains/{self.settings.lre_domain}/projects/{self.settings.lre_project}"
                f"/scripts/{script_id}/content",
                content=f.read(),
                headers={"Content-Type": "application/octet-stream"},
            )
        resp.raise_for_status()

        log.info("script_uploaded", name=script_name, script_id=script_id)
        return script_id

    # ── Test Configuration ────────────────────────────────────────────

    async def create_test(
        self,
        test_name: str,
        test_set_id: int,
        scripts: list[dict[str, Any]],
        scheduler_config: dict[str, Any],
    ) -> int:
        """Create and configure a test in LRE.

        Args:
            test_name: Name of the test.
            test_set_id: ID of the test set folder.
            scripts: List of {script_id, vusers, group_name} dicts.
            scheduler_config: Ramp-up, duration, ramp-down settings.

        Returns the test ID.
        """
        await self._ensure_auth()

        # Create test
        resp = await self.client.post(
            f"/rest/domains/{self.settings.lre_domain}/projects/{self.settings.lre_project}/tests",
            json={
                "Name": test_name,
                "TestSetID": test_set_id,
            },
        )
        resp.raise_for_status()
        test_id = resp.json().get("ID")

        # Add script groups
        for script in scripts:
            await self.client.post(
                f"/rest/domains/{self.settings.lre_domain}/projects/{self.settings.lre_project}"
                f"/tests/{test_id}/groups",
                json={
                    "Name": script.get("group_name", "Group"),
                    "ScriptID": script["script_id"],
                    "VuserCount": script["vusers"],
                },
            )

        # Configure scheduler (ramp-up, steady state, ramp-down)
        await self.client.put(
            f"/rest/domains/{self.settings.lre_domain}/projects/{self.settings.lre_project}"
            f"/tests/{test_id}/scheduler",
            json=scheduler_config,
        )

        log.info("test_created", name=test_name, test_id=test_id, groups=len(scripts))
        return test_id

    # ── Test Execution ────────────────────────────────────────────────

    async def start_test_run(self, test_id: int, timeslot_id: Optional[int] = None) -> int:
        """Start a test run. Returns the run ID."""
        await self._ensure_auth()

        body: dict[str, Any] = {"TestID": test_id}
        if timeslot_id:
            body["TimeslotID"] = timeslot_id

        resp = await self.client.post(
            f"/rest/domains/{self.settings.lre_domain}/projects/{self.settings.lre_project}/runs",
            json=body,
        )
        resp.raise_for_status()
        run_id = resp.json().get("ID")
        log.info("test_run_started", test_id=test_id, run_id=run_id)
        return run_id

    async def get_run_status(self, run_id: int) -> dict[str, Any]:
        """Get the current status of a test run."""
        await self._ensure_auth()

        resp = await self.client.get(
            f"/rest/domains/{self.settings.lre_domain}/projects/{self.settings.lre_project}/runs/{run_id}",
        )
        resp.raise_for_status()
        return resp.json()

    async def wait_for_run_completion(
        self,
        run_id: int,
        poll_interval_seconds: int = 30,
        timeout_minutes: int = 300,
    ) -> dict[str, Any]:
        """Poll run status until completion or timeout."""
        start = time.time()
        timeout_s = timeout_minutes * 60

        while (time.time() - start) < timeout_s:
            status = await self.get_run_status(run_id)
            state = status.get("RunState", "")
            log.info("run_poll", run_id=run_id, state=state)

            if state in ("Finished", "Passed"):
                return status
            if state in ("Failed", "Error", "Stopped"):
                raise RuntimeError(f"LRE run {run_id} ended with state: {state}")

            time.sleep(poll_interval_seconds)

        raise TimeoutError(f"LRE run {run_id} timed out after {timeout_minutes} minutes")

    # ── Results Collection ────────────────────────────────────────────

    async def get_run_results(self, run_id: int) -> dict[str, Any]:
        """Get results summary for a completed run."""
        await self._ensure_auth()

        resp = await self.client.get(
            f"/rest/domains/{self.settings.lre_domain}/projects/{self.settings.lre_project}"
            f"/runs/{run_id}/results",
        )
        resp.raise_for_status()
        return resp.json()

    async def get_transaction_summary(self, run_id: int) -> list[dict[str, Any]]:
        """Get per-transaction metrics for a run."""
        await self._ensure_auth()

        resp = await self.client.get(
            f"/rest/domains/{self.settings.lre_domain}/projects/{self.settings.lre_project}"
            f"/runs/{run_id}/transactions",
        )
        resp.raise_for_status()
        transactions = resp.json().get("transactions", [])
        log.info("transaction_results_fetched", run_id=run_id, count=len(transactions))
        return transactions
