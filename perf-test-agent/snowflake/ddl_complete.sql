-- ═══════════════════════════════════════════════════════════════════════════
-- PERF_TESTING Database Schema — Snowflake DDL
-- ═══════════════════════════════════════════════════════════════════════════
-- Supports all 7 pipeline phases:
--   Phase 1: Test case extraction metadata
--   Phase 2: Test plan archival
--   Phase 3: Golden configs and config check history
--   Phase 4: Script metadata
--   Phase 5: Execution results (baseline source)
--   Phase 6: Analysis reports and defects
--   Phase 7: Postmortem entries and lessons learned
-- ═══════════════════════════════════════════════════════════════════════════

CREATE DATABASE IF NOT EXISTS PERF_TESTING;
USE DATABASE PERF_TESTING;
CREATE SCHEMA IF NOT EXISTS PUBLIC;
USE SCHEMA PUBLIC;

-- ── Pipeline Runs ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS PIPELINE_RUNS (
    RUN_ID              VARCHAR(64)     PRIMARY KEY,
    CREATED_AT          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    COMPLETED_AT        TIMESTAMP_NTZ,
    STATUS              VARCHAR(20)     NOT NULL DEFAULT 'running',  -- running | completed | failed | aborted
    CURRENT_PHASE       VARCHAR(30),
    JIRA_STORY_KEYS     ARRAY,
    SPRINT_NAME         VARCHAR(100),
    PIPELINE_STATE_JSON VARIANT,        -- Full PipelineState snapshot
    INITIATED_BY        VARCHAR(100)    DEFAULT 'perf-test-agent'
);

ALTER TABLE PIPELINE_RUNS CLUSTER BY (CREATED_AT);

-- ── Phase 1: Test Cases ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS TEST_CASES (
    TEST_CASE_ID        VARCHAR(64)     PRIMARY KEY,
    RUN_ID              VARCHAR(64)     NOT NULL REFERENCES PIPELINE_RUNS(RUN_ID),
    SOURCE_STORY_KEY    VARCHAR(30)     NOT NULL,
    TITLE               VARCHAR(500)    NOT NULL,
    DESCRIPTION         TEXT,
    RISK_LEVEL          VARCHAR(20),    -- low | medium | high | critical
    RECOMMENDED_HARNESS VARCHAR(30),    -- loadrunner_enterprise | jmeter | k6
    RECOMMENDED_PROTOCOL VARCHAR(30),   -- rest_json | soap_xml | web_http_html | solace_mq
    SLA_TARGETS_JSON    VARIANT,        -- Array of SLATarget objects
    TRANSACTION_FLOWS_JSON VARIANT,     -- Array of TransactionFlow objects
    DATA_REQUIREMENTS_JSON VARIANT,     -- Array of DataRequirement objects
    CONFIDENCE_SCORE    FLOAT,
    CREATED_AT          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

ALTER TABLE TEST_CASES CLUSTER BY (RUN_ID, SOURCE_STORY_KEY);

-- ── Phase 2: Test Plans ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS TEST_PLANS (
    PLAN_ID             VARCHAR(64)     PRIMARY KEY,
    RUN_ID              VARCHAR(64)     NOT NULL REFERENCES PIPELINE_RUNS(RUN_ID),
    TITLE               VARCHAR(500)    NOT NULL,
    VERSION             VARCHAR(20)     DEFAULT '1.0',
    PLAN_JSON           VARIANT         NOT NULL,   -- Full TestPlan object
    SCENARIO_COUNT      INTEGER,
    ESTIMATED_DURATION_DAYS INTEGER,
    CREATED_AT          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

-- ── Phase 3: Golden Configs & Config Check History ───────────────────────

CREATE TABLE IF NOT EXISTS GOLDEN_CONFIGS (
    CONFIG_ID           VARCHAR(64)     PRIMARY KEY,
    ENVIRONMENT         VARCHAR(20)     NOT NULL DEFAULT 'PERF',
    VALIDATION_RUN_ID   VARCHAR(64),
    CONFIG_DATA         VARIANT         NOT NULL,   -- {app_name: {field: value}}
    CREATED_AT          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    IS_ACTIVE           BOOLEAN         DEFAULT TRUE
);

ALTER TABLE GOLDEN_CONFIGS CLUSTER BY (ENVIRONMENT, CREATED_AT);

CREATE TABLE IF NOT EXISTS CONFIG_CHECK_HISTORY (
    CHECK_ID            VARCHAR(64)     PRIMARY KEY,
    RUN_ID              VARCHAR(64),    -- Pipeline run, or NULL for daily checks
    ENVIRONMENT         VARCHAR(20)     NOT NULL DEFAULT 'PERF',
    GOLDEN_CONFIG_ID    VARCHAR(64)     REFERENCES GOLDEN_CONFIGS(CONFIG_ID),
    TOTAL_CHECKS        INTEGER         NOT NULL,
    PASSED              INTEGER         NOT NULL,
    FAILED              INTEGER         NOT NULL,
    ERRORS              INTEGER         DEFAULT 0,
    IS_DRIFT_CHECK      BOOLEAN         DEFAULT FALSE,  -- TRUE for daily drift checks
    RESULTS_JSON        VARIANT,        -- Array of ConfigCheckResult objects
    MISMATCHES_SUMMARY  ARRAY,
    CHECKED_AT          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

ALTER TABLE CONFIG_CHECK_HISTORY CLUSTER BY (ENVIRONMENT, CHECKED_AT);

-- ── Phase 4: Generated Scripts ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS GENERATED_SCRIPTS (
    SCRIPT_ID           VARCHAR(64)     PRIMARY KEY,
    RUN_ID              VARCHAR(64)     NOT NULL REFERENCES PIPELINE_RUNS(RUN_ID),
    TEST_CASE_ID        VARCHAR(64)     REFERENCES TEST_CASES(TEST_CASE_ID),
    TRANSACTION_NAME    VARCHAR(200)    NOT NULL,
    HARNESS             VARCHAR(30)     NOT NULL,   -- loadrunner_enterprise | jmeter
    LANGUAGE            VARCHAR(30)     NOT NULL,   -- vugen_c | jmeter_jmx | jmeter_groovy
    PROTOCOL            VARCHAR(30)     NOT NULL,
    FILE_PATH           VARCHAR(500),
    SYNTAX_VALID        BOOLEAN         DEFAULT FALSE,
    DRY_RUN_PASSED      BOOLEAN         DEFAULT FALSE,
    VALIDATION_ERRORS   ARRAY,
    CREATED_AT          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

-- ── Phase 5: Execution Results (PRIMARY BASELINE SOURCE) ─────────────────

CREATE TABLE IF NOT EXISTS EXECUTION_RESULTS (
    RESULT_ID           VARCHAR(64)     PRIMARY KEY DEFAULT UUID_STRING(),
    RUN_ID              VARCHAR(64)     NOT NULL,
    TRANSACTION_NAME    VARCHAR(200)    NOT NULL,
    TEST_TYPE           VARCHAR(20),    -- load | stress | endurance | spike | breakpoint
    HARNESS             VARCHAR(30),
    P50_RESPONSE_TIME_MS FLOAT,
    P90_RESPONSE_TIME_MS FLOAT          NOT NULL,
    P95_RESPONSE_TIME_MS FLOAT,
    P99_RESPONSE_TIME_MS FLOAT,
    AVG_RESPONSE_TIME_MS FLOAT,
    MIN_RESPONSE_TIME_MS FLOAT,
    MAX_RESPONSE_TIME_MS FLOAT,
    AVG_TPS             FLOAT,
    TOTAL_REQUESTS      INTEGER,
    TOTAL_ERRORS        INTEGER,
    ERROR_RATE_PCT      FLOAT           NOT NULL,
    VUSERS              INTEGER,
    DURATION_MINUTES    FLOAT,
    SLA_P90_TARGET_MS   FLOAT,
    SLA_MET             BOOLEAN,
    RUN_DATE            TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

-- This is the most queried table — cluster for baseline lookups
ALTER TABLE EXECUTION_RESULTS CLUSTER BY (TRANSACTION_NAME, RUN_DATE);

-- ── Phase 5: Execution Runs ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS EXECUTION_RUNS (
    EXEC_RUN_ID         VARCHAR(64)     PRIMARY KEY,
    PIPELINE_RUN_ID     VARCHAR(64)     NOT NULL REFERENCES PIPELINE_RUNS(RUN_ID),
    SCENARIO_ID         VARCHAR(64),
    TEST_TYPE           VARCHAR(20)     NOT NULL,
    HARNESS             VARCHAR(30)     NOT NULL,
    HARNESS_RUN_ID      VARCHAR(64),    -- LRE run ID or JMeter test ID
    JENKINS_BUILD_ID    VARCHAR(64),
    STATUS              VARCHAR(20)     NOT NULL,  -- completed | aborted | failed
    STARTED_AT          TIMESTAMP_NTZ,
    COMPLETED_AT        TIMESTAMP_NTZ,
    DURATION_MINUTES    FLOAT,
    VUSERS_TARGET       INTEGER,
    VUSERS_PEAK         INTEGER,
    TOTAL_TRANSACTIONS  INTEGER,
    TOTAL_ERRORS        INTEGER,
    OVERALL_ERROR_RATE  FLOAT,
    CREATED_AT          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

-- ── Phase 5: Anomalies ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ANOMALIES (
    ANOMALY_ID          VARCHAR(64)     PRIMARY KEY,
    EXEC_RUN_ID         VARCHAR(64)     REFERENCES EXECUTION_RUNS(EXEC_RUN_ID),
    PIPELINE_RUN_ID     VARCHAR(64)     NOT NULL,
    SEVERITY            VARCHAR(20)     NOT NULL,  -- info | warning | error | critical
    CATEGORY            VARCHAR(50)     NOT NULL,  -- error_spike | response_degradation | resource_exhaustion
    DESCRIPTION         TEXT            NOT NULL,
    AFFECTED_TRANSACTION VARCHAR(200),
    AFFECTED_SYSTEM     VARCHAR(100),
    METRIC_NAME         VARCHAR(100),
    METRIC_VALUE        FLOAT,
    THRESHOLD           FLOAT,
    SOURCE              VARCHAR(30),    -- dynatrace | prometheus | elk | lre | jmeter
    ROUTED_TO_TEAM      VARCHAR(100),
    JIRA_DEFECT_KEY     VARCHAR(30),
    RESOLVED            BOOLEAN         DEFAULT FALSE,
    RESOLUTION_NOTES    TEXT,
    DETECTED_AT         TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

ALTER TABLE ANOMALIES CLUSTER BY (PIPELINE_RUN_ID, DETECTED_AT);

-- ── Phase 5: Peak/Break/Stability Results ────────────────────────────────

CREATE TABLE IF NOT EXISTS BENCHMARK_RESULTS (
    BENCHMARK_ID        VARCHAR(64)     PRIMARY KEY DEFAULT UUID_STRING(),
    PIPELINE_RUN_ID     VARCHAR(64)     NOT NULL REFERENCES PIPELINE_RUNS(RUN_ID),
    BENCHMARK_TYPE      VARCHAR(20)     NOT NULL,  -- peak_point | breakpoint | stability
    IDENTIFIED          BOOLEAN         NOT NULL DEFAULT FALSE,
    TPS_VALUE           FLOAT,
    VUSERS_VALUE        INTEGER,
    P90_AT_POINT_MS     FLOAT,
    ERROR_RATE_PCT      FLOAT,
    PRIMARY_FAILURE_MODE VARCHAR(50),   -- timeout | error_5xx | connection_refused (breakpoint)
    BOTTLENECK_SYSTEM   VARCHAR(100),
    DURATION_HOURS      FLOAT,          -- For stability tests
    PASSED              BOOLEAN,        -- For stability tests
    EXEC_RUN_ID         VARCHAR(64)     REFERENCES EXECUTION_RUNS(EXEC_RUN_ID),
    DETAILS_JSON        VARIANT,        -- Full result object
    CREATED_AT          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

-- ── Phase 6: Analysis Reports ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ANALYSIS_REPORTS (
    REPORT_ID           VARCHAR(64)     PRIMARY KEY,
    PIPELINE_RUN_ID     VARCHAR(64)     NOT NULL REFERENCES PIPELINE_RUNS(RUN_ID),
    TITLE               VARCHAR(500),
    EXECUTIVE_SUMMARY   TEXT,
    OVERALL_SLA_PASS    BOOLEAN,
    GO_NO_GO            VARCHAR(20),    -- go | no_go | conditional | pending
    TOTAL_DEFECTS       INTEGER         DEFAULT 0,
    BLOCKERS            INTEGER         DEFAULT 0,
    WORD_REPORT_PATH    VARCHAR(500),
    SHAREPOINT_URL      VARCHAR(500),
    SLA_COMPLIANCE_JSON VARIANT,        -- Array of SLAComplianceEntry
    RECOMMENDATIONS     ARRAY,
    GENERATED_AT        TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

-- ── Phase 6: Performance Defects ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS PERFORMANCE_DEFECTS (
    DEFECT_ID           VARCHAR(64)     PRIMARY KEY,
    PIPELINE_RUN_ID     VARCHAR(64)     NOT NULL,
    JIRA_KEY            VARCHAR(30),
    TITLE               VARCHAR(500)    NOT NULL,
    SEVERITY            VARCHAR(20)     NOT NULL,  -- blocker | critical | major | minor
    AFFECTED_SYSTEM     VARCHAR(100),
    AFFECTED_TRANSACTION VARCHAR(200),
    OBSERVED_VALUE      VARCHAR(200),
    EXPECTED_VALUE      VARCHAR(200),
    RECOMMENDED_ACTION  TEXT,
    ASSIGNED_TEAM       VARCHAR(100),
    CREATED_AT          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

-- ── Phase 7: Postmortem Entries ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS POSTMORTEM_ENTRIES (
    ENTRY_ID            VARCHAR(64)     PRIMARY KEY DEFAULT UUID_STRING(),
    RUN_ID              VARCHAR(64)     NOT NULL,
    PHASE               VARCHAR(30)     NOT NULL,
    CATEGORY            VARCHAR(30)     NOT NULL,  -- environment | data | tooling | process | communication | technical
    TITLE               VARCHAR(500)    NOT NULL,
    DESCRIPTION         TEXT,
    IMPACT              TEXT,
    RESOLUTION          TEXT,
    RESOLVED            BOOLEAN         DEFAULT FALSE,
    TIME_LOST_HOURS     FLOAT           DEFAULT 0,
    LOGGED_AT           TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

ALTER TABLE POSTMORTEM_ENTRIES CLUSTER BY (RUN_ID, PHASE);

-- ── Phase 7: Lessons Learned ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS LESSONS_LEARNED (
    LESSON_ID           VARCHAR(64)     PRIMARY KEY DEFAULT UUID_STRING(),
    PIPELINE_RUN_ID     VARCHAR(64)     NOT NULL,
    TITLE               VARCHAR(500)    NOT NULL,
    CONTEXT             TEXT,           -- When does this lesson apply?
    LESSON              TEXT            NOT NULL,  -- What was learned?
    RECOMMENDATION      TEXT,           -- What should be done differently?
    APPLICABLE_SYSTEMS  ARRAY,
    APPLICABLE_PHASES   ARRAY,
    TAGS                ARRAY,
    RAG_INDEXED         BOOLEAN         DEFAULT FALSE,
    CREATED_AT          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

-- ═══════════════════════════════════════════════════════════════════════════
-- Views for Common Queries
-- ═══════════════════════════════════════════════════════════════════════════

-- Transaction baseline view (used by Phase 1 & 2 agents)
CREATE OR REPLACE VIEW V_TRANSACTION_BASELINES AS
WITH ranked AS (
    SELECT
        TRANSACTION_NAME,
        P90_RESPONSE_TIME_MS,
        P95_RESPONSE_TIME_MS,
        AVG_TPS,
        ERROR_RATE_PCT,
        VUSERS,
        RUN_DATE,
        ROW_NUMBER() OVER (PARTITION BY TRANSACTION_NAME ORDER BY RUN_DATE DESC) AS rn
    FROM EXECUTION_RESULTS
    WHERE SLA_MET IS NOT NULL
),
baselines AS (
    SELECT
        TRANSACTION_NAME,
        ROUND(AVG(P90_RESPONSE_TIME_MS), 1)  AS AVG_P90_MS,
        ROUND(AVG(P95_RESPONSE_TIME_MS), 1)  AS AVG_P95_MS,
        ROUND(AVG(AVG_TPS), 2)               AS AVG_TPS,
        ROUND(AVG(ERROR_RATE_PCT), 3)         AS AVG_ERROR_RATE_PCT,
        COUNT(*)                               AS RUN_COUNT,
        MAX(RUN_DATE)                          AS LAST_RUN_DATE,
        -- Trend: compare recent 5 vs older 5
        ROUND(AVG(CASE WHEN rn <= 5 THEN P90_RESPONSE_TIME_MS END), 1) AS RECENT_P90,
        ROUND(AVG(CASE WHEN rn > 5 THEN P90_RESPONSE_TIME_MS END), 1)  AS OLDER_P90
    FROM ranked
    WHERE rn <= 10
    GROUP BY TRANSACTION_NAME
)
SELECT
    TRANSACTION_NAME,
    AVG_P90_MS,
    AVG_P95_MS,
    AVG_TPS,
    AVG_ERROR_RATE_PCT,
    RUN_COUNT,
    LAST_RUN_DATE,
    CASE
        WHEN OLDER_P90 IS NULL OR OLDER_P90 = 0 THEN 'insufficient_data'
        WHEN (RECENT_P90 - OLDER_P90) / OLDER_P90 > 0.10 THEN 'degrading'
        WHEN (RECENT_P90 - OLDER_P90) / OLDER_P90 < -0.10 THEN 'improving'
        ELSE 'stable'
    END AS TREND
FROM baselines;

-- Config drift summary (used by Phase 3 daily checks)
CREATE OR REPLACE VIEW V_CONFIG_DRIFT_HISTORY AS
SELECT
    c.CHECK_ID,
    c.ENVIRONMENT,
    c.TOTAL_CHECKS,
    c.PASSED,
    c.FAILED,
    c.ERRORS,
    c.IS_DRIFT_CHECK,
    c.CHECKED_AT,
    g.CONFIG_ID AS GOLDEN_CONFIG_ID,
    g.CREATED_AT AS GOLDEN_CREATED_AT
FROM CONFIG_CHECK_HISTORY c
LEFT JOIN GOLDEN_CONFIGS g ON c.GOLDEN_CONFIG_ID = g.CONFIG_ID
ORDER BY c.CHECKED_AT DESC;

-- Pipeline run summary
CREATE OR REPLACE VIEW V_PIPELINE_SUMMARY AS
SELECT
    r.RUN_ID,
    r.CREATED_AT,
    r.COMPLETED_AT,
    r.STATUS,
    r.CURRENT_PHASE,
    r.SPRINT_NAME,
    ARRAY_SIZE(r.JIRA_STORY_KEYS) AS STORY_COUNT,
    (SELECT COUNT(*) FROM TEST_CASES tc WHERE tc.RUN_ID = r.RUN_ID) AS TEST_CASE_COUNT,
    (SELECT COUNT(*) FROM EXECUTION_RUNS er WHERE er.PIPELINE_RUN_ID = r.RUN_ID) AS EXEC_RUN_COUNT,
    (SELECT COUNT(*) FROM ANOMALIES a WHERE a.PIPELINE_RUN_ID = r.RUN_ID AND a.SEVERITY IN ('error', 'critical')) AS CRITICAL_ANOMALIES,
    (SELECT COUNT(*) FROM PERFORMANCE_DEFECTS d WHERE d.PIPELINE_RUN_ID = r.RUN_ID) AS DEFECT_COUNT,
    (SELECT GO_NO_GO FROM ANALYSIS_REPORTS ar WHERE ar.PIPELINE_RUN_ID = r.RUN_ID LIMIT 1) AS GO_NO_GO
FROM PIPELINE_RUNS r
ORDER BY r.CREATED_AT DESC;

-- Postmortem insights (queried by Phase 7 for trending)
CREATE OR REPLACE VIEW V_POSTMORTEM_INSIGHTS AS
SELECT
    CATEGORY,
    COUNT(*) AS OCCURRENCE_COUNT,
    ROUND(SUM(TIME_LOST_HOURS), 1) AS TOTAL_HOURS_LOST,
    ROUND(AVG(TIME_LOST_HOURS), 1) AS AVG_HOURS_LOST,
    SUM(CASE WHEN RESOLVED THEN 1 ELSE 0 END) AS RESOLVED_COUNT
FROM POSTMORTEM_ENTRIES
GROUP BY CATEGORY
ORDER BY TOTAL_HOURS_LOST DESC;
