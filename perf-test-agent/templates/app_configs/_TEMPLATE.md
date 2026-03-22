# Application Performance Testing Configuration Template

> **Instructions**: Copy this template for each application under test.
> Save as `{app_name_lowercase}.md` in `templates/app_configs/`.
> This file is read by the Phase 2 (Test Planning) and Phase 4 (Script Generation) agents
> to produce application-specific test artifacts.

---

## Application Identity

| Field | Value |
|-------|-------|
| **App Name** | `{APP_NAME}` |
| **App Code** | `{APP_CODE}` (e.g., OPUS, SF, IDP, CSI, TLG) |
| **System Tier** | `frontend` \| `middleware` \| `backend` \| `periphery` |
| **Owner Team** | `{TEAM_NAME}` |
| **Jira Component** | `{JIRA_COMPONENT}` |

---

## Protocols & Endpoints

### Primary Protocol
- **Protocol**: `rest_json` | `soap_xml` | `web_http_html` | `solace_mq` | `ibm_mq`
- **Base URL (PERF)**: `https://{app}-perf.internal.att.com:{port}`
- **Health Check**: `GET /health` → expected `200`

### API Endpoints Under Test
| Endpoint | Method | Description | SLA p90 (ms) | Expected TPS |
|----------|--------|-------------|--------------|--------------|
| `/api/v1/orders` | POST | Create new order | 2000 | 50 |
| `/api/v1/orders/{id}` | GET | Retrieve order | 500 | 200 |
| `/api/v1/customers/{id}` | GET | Customer lookup | 800 | 300 |

---

## SLA Targets

| Transaction | p90 (ms) | p95 (ms) | p99 (ms) | Error Rate (%) | Notes |
|-------------|----------|----------|----------|----------------|-------|
| `{Transaction_Name}` | 1000 | 2000 | 5000 | 0.5 | |

> **Override Note**: If SLA targets are specified in the Jira story, they take
> precedence over the values in this file.

---

## Test Harness Selection

| Condition | Harness | Rationale |
|-----------|---------|-----------|
| Web HTTP/HTML (OPUS) | LoadRunner Enterprise (VuGen) | Browser-level protocol emulation |
| REST/JSON APIs | JMeter | Native HTTP sampler, JSON assertion |
| SOAP/XML APIs | JMeter | SOAP/XML sampler with XPath extraction |
| Solace MQ | JMeter (Solace plugin) | JMS publisher/subscriber |
| Mixed protocol | Both | Separate script groups per protocol |

---

## Script Generation Instructions

### Correlation Rules
Dynamic values that must be extracted and correlated between requests:
- `sessionId` — extracted from login response header
- `csrfToken` — extracted from HTML meta tag (OPUS) or response header
- `orderId` — extracted from order creation response body

### Parameterization
Data-driven values that change per virtual user:
- `username` — from CSV data file
- `customerId` — from CSV data file
- `productId` — from CSV data file

### Think Time
- **Default**: 3 seconds between steps
- **Login flow**: 5 seconds (realistic user behavior)
- **Search/browse**: 2 seconds

### Known Constraints
- Max concurrent sessions per user: 1 (enforce in pacing)
- Rate limiting: {N} requests/second per client IP (use IP spoofing if needed)
- Authentication: OAuth2 / SAML / Basic (specify mechanism)
- Cookie handling: Required for session management

---

## Environment Dependencies

### Upstream Dependencies (systems that call this app)
| System | Protocol | Expected Call Pattern |
|--------|----------|---------------------|
| `{UPSTREAM_APP}` | REST | Sync request-response |

### Downstream Dependencies (systems this app calls)
| System | Protocol | Expected Call Pattern | Timeout (ms) |
|--------|----------|-----------------------|---------------|
| `TLG` | REST | Sync | 10000 |
| `BSSe` | REST | Sync | 15000 |
| `Cassandra` | JDBC | Async batch write | 5000 |
| `Solace` | MQ | Async publish | 2000 |

### Database Configuration
| DB Type | Connection | Schema | Notes |
|---------|-----------|--------|-------|
| Oracle | `oracle-perf-scan.att.com:1521/PERFDB` | `{SCHEMA}` | Connection pool: min 10, max 50 |
| Cassandra | `cassandra-perf-01,02.att.com` | `{KEYSPACE}` | Consistency: LOCAL_QUORUM |

### Message Queuing
| Broker | Host | VPN/Topic | Direction |
|--------|------|-----------|-----------|
| Solace | `solace-perf.internal.att.com` | `perf-vpn/{topic}` | Publish |

---

## Bulk Data Requirements

| Entity | Quantity | Method | Source System | Notes |
|--------|----------|--------|---------------|-------|
| Customer accounts | 50,000 | API provisioning | TLG | Active status, varied plan types |
| Orders | 100,000 | SQL insert | Oracle | Mix of order types |
| Billing records | 200,000 | SQL insert | BSSe | Current billing cycle |

### Data Refresh Cadence
- **Before each test cycle**: Full refresh of customer/order data
- **Daily**: Verify data integrity (counts, status checks)

---

## Monitoring Focus

### Key Metrics to Watch
- JVM heap usage (alert at 80%)
- GC pause duration (alert at 500ms)
- Database connection pool utilization
- MQ queue depth (alert at 1000 messages)
- Thread pool saturation
- HTTP 5xx error rate

### Dynatrace
- **Management Zone**: `{MZ_NAME}`
- **Tags**: `perf-testing`, `{app_name}`

### Prometheus Queries
```promql
# CPU usage
sum(rate(container_cpu_usage_seconds_total{namespace="{namespace}"}[5m])) by (pod)

# Memory usage
container_memory_working_set_bytes{namespace="{namespace}"} / container_memory_limit_bytes{namespace="{namespace}"}

# JVM heap
jvm_memory_used_bytes{namespace="{namespace}",area="heap"} / jvm_memory_max_bytes{namespace="{namespace}",area="heap"}
```

### ELK Log Patterns
```
service:"{app_name}" AND (level:ERROR OR level:FATAL)
```

---

## Known Issues & Historical Context

| Date | Issue | Resolution | Impact |
|------|-------|------------|--------|
| 2025-Q4 | TLG timeout under load | Increased connection pool to 50 | p90 improved 40% |
| 2025-Q3 | Cassandra read timeout | Added read retry policy | Error rate dropped to <0.1% |

---

## Contacts

| Role | Name | Team |
|------|------|------|
| App Owner | | |
| Performance Lead | | CTx CQE Performance Engineering |
| DBA | | Database Administration |
| Infrastructure | | CSTEM |
