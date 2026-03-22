"""Snowflake integration client.

Provides read/write access to the PERF_TESTING database for:
- Historical performance baselines (Phase 1, 2)
- Environment configuration archival (Phase 3)
- Test execution results storage (Phase 5, 6)
- Postmortem data archival (Phase 7)
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Generator, Optional

import snowflake.connector
from snowflake.connector import DictCursor

from src.config.settings import Settings, get_settings
from src.utils.logging import get_logger

log = get_logger(__name__)


class SnowflakeClient:
    """Snowflake client for the PERF_TESTING database."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._conn: Optional[snowflake.connector.SnowflakeConnection] = None

    @contextmanager
    def connection(self) -> Generator[snowflake.connector.SnowflakeConnection, None, None]:
        """Context manager for Snowflake connections."""
        conn = snowflake.connector.connect(
            account=self.settings.snowflake_account,
            user=self.settings.snowflake_user,
            password=self.settings.snowflake_password,
            database=self.settings.snowflake_database,
            schema=self.settings.snowflake_schema,
            warehouse=self.settings.snowflake_warehouse,
            role=self.settings.snowflake_role,
        )
        try:
            yield conn
        finally:
            conn.close()

    def _execute(self, sql: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """Execute a query and return results as dicts."""
        with self.connection() as conn:
            cur = conn.cursor(DictCursor)
            try:
                cur.execute(sql, params or {})
                return cur.fetchall()
            finally:
                cur.close()

    def _execute_write(self, sql: str, params: Optional[dict[str, Any]] = None) -> int:
        """Execute a write operation and return rows affected."""
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql, params or {})
                conn.commit()
                return cur.rowcount
            finally:
                cur.close()

    # ── Historical Baselines (Phase 1, 2) ─────────────────────────────

    def get_transaction_baselines(
        self,
        transaction_names: list[str],
        recent_runs: int = 10,
    ) -> dict[str, dict[str, float]]:
        """Get historical baselines for transactions.

        Returns: {transaction_name: {p90_ms, p95_ms, avg_tps, error_rate_pct, trend}}
        """
        if not transaction_names:
            return {}

        placeholders = ", ".join([f"%(tn{i})s" for i in range(len(transaction_names))])
        params = {f"tn{i}": tn for i, tn in enumerate(transaction_names)}

        sql = f"""
            WITH recent AS (
                SELECT
                    transaction_name,
                    p90_response_time_ms,
                    p95_response_time_ms,
                    avg_tps,
                    error_rate_pct,
                    run_date,
                    ROW_NUMBER() OVER (
                        PARTITION BY transaction_name
                        ORDER BY run_date DESC
                    ) AS rn
                FROM PERF_TESTING.PUBLIC.EXECUTION_RESULTS
                WHERE transaction_name IN ({placeholders})
            ),
            baselines AS (
                SELECT
                    transaction_name,
                    AVG(p90_response_time_ms) AS avg_p90,
                    AVG(p95_response_time_ms) AS avg_p95,
                    AVG(avg_tps) AS avg_tps,
                    AVG(error_rate_pct) AS avg_error_rate,
                    -- Trend: compare recent half vs older half
                    AVG(CASE WHEN rn <= %(half)s THEN p90_response_time_ms END) AS recent_p90,
                    AVG(CASE WHEN rn > %(half)s THEN p90_response_time_ms END) AS older_p90
                FROM recent
                WHERE rn <= %(limit)s
                GROUP BY transaction_name
            )
            SELECT
                transaction_name,
                ROUND(avg_p90, 1) AS p90_ms,
                ROUND(avg_p95, 1) AS p95_ms,
                ROUND(avg_tps, 2) AS avg_tps,
                ROUND(avg_error_rate, 3) AS error_rate_pct,
                CASE
                    WHEN older_p90 IS NULL OR older_p90 = 0 THEN 'insufficient_data'
                    WHEN (recent_p90 - older_p90) / older_p90 > 0.10 THEN 'degrading'
                    WHEN (recent_p90 - older_p90) / older_p90 < -0.10 THEN 'improving'
                    ELSE 'stable'
                END AS trend
            FROM baselines
        """
        params["half"] = recent_runs // 2
        params["limit"] = recent_runs

        try:
            rows = self._execute(sql, params)
            result = {}
            for row in rows:
                result[row["TRANSACTION_NAME"]] = {
                    "p90_ms": row["P90_MS"],
                    "p95_ms": row["P95_MS"],
                    "avg_tps": row["AVG_TPS"],
                    "error_rate_pct": row["ERROR_RATE_PCT"],
                    "trend": row["TREND"],
                }
            log.info("baselines_retrieved", count=len(result))
            return result
        except Exception as e:
            log.warning("baselines_unavailable", error=str(e))
            return {}

    # ── Environment Config Archival (Phase 3) ─────────────────────────

    def save_golden_config(self, config: dict[str, Any]) -> None:
        """Archive a golden config snapshot to Snowflake."""
        sql = """
            INSERT INTO PERF_TESTING.PUBLIC.GOLDEN_CONFIGS
            (CONFIG_ID, ENVIRONMENT, VALIDATION_RUN_ID, CONFIG_DATA, CREATED_AT)
            VALUES (%(config_id)s, %(environment)s, %(run_id)s, %(data)s, %(ts)s)
        """
        import json
        self._execute_write(sql, {
            "config_id": config.get("config_id", ""),
            "environment": config.get("environment", "PERF"),
            "run_id": config.get("validation_run_id", ""),
            "data": json.dumps(config.get("application_configs", {})),
            "ts": datetime.utcnow().isoformat(),
        })
        log.info("golden_config_saved", config_id=config.get("config_id"))

    # ── Execution Results (Phase 5, 6) ────────────────────────────────

    def save_execution_results(self, run_id: str, results: list[dict[str, Any]]) -> None:
        """Save test execution results for future baselining."""
        sql = """
            INSERT INTO PERF_TESTING.PUBLIC.EXECUTION_RESULTS
            (RUN_ID, TRANSACTION_NAME, P90_RESPONSE_TIME_MS, P95_RESPONSE_TIME_MS,
             AVG_TPS, ERROR_RATE_PCT, VUSERS, DURATION_MINUTES, RUN_DATE)
            VALUES (%(run_id)s, %(txn)s, %(p90)s, %(p95)s, %(tps)s, %(err)s,
                    %(vusers)s, %(duration)s, %(run_date)s)
        """
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                for r in results:
                    cur.execute(sql, {
                        "run_id": run_id,
                        "txn": r["transaction_name"],
                        "p90": r.get("p90_response_time_ms", 0),
                        "p95": r.get("p95_response_time_ms", 0),
                        "tps": r.get("tps_achieved", 0),
                        "err": r.get("error_rate_pct", 0),
                        "vusers": r.get("vusers", 0),
                        "duration": r.get("duration_minutes", 0),
                        "run_date": datetime.utcnow().isoformat(),
                    })
                conn.commit()
                log.info("execution_results_saved", run_id=run_id, rows=len(results))
            finally:
                cur.close()

    # ── Postmortem Archival (Phase 7) ─────────────────────────────────

    def save_postmortem(self, run_id: str, entries: list[dict[str, Any]]) -> None:
        """Archive postmortem entries to Snowflake."""
        sql = """
            INSERT INTO PERF_TESTING.PUBLIC.POSTMORTEM_ENTRIES
            (RUN_ID, ENTRY_ID, PHASE, CATEGORY, TITLE, DESCRIPTION,
             IMPACT, RESOLUTION, TIME_LOST_HOURS, LOGGED_AT)
            VALUES (%(run_id)s, %(entry_id)s, %(phase)s, %(category)s, %(title)s,
                    %(description)s, %(impact)s, %(resolution)s, %(hours)s, %(ts)s)
        """
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                for e in entries:
                    cur.execute(sql, {
                        "run_id": run_id,
                        "entry_id": e.get("entry_id", ""),
                        "phase": e.get("phase", ""),
                        "category": e.get("category", ""),
                        "title": e.get("title", ""),
                        "description": e.get("description", ""),
                        "impact": e.get("impact", ""),
                        "resolution": e.get("resolution", ""),
                        "hours": e.get("time_lost_hours", 0),
                        "ts": datetime.utcnow().isoformat(),
                    })
                conn.commit()
                log.info("postmortem_saved", run_id=run_id, entries=len(entries))
            finally:
                cur.close()
