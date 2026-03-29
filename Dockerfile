FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PERF_TEST_AGENT_ROOT=/app/perf-test-agent \
    PERF_TEST_AGENT_WORKSPACE=/app \
    PERF_TEST_AGENT_DASHBOARD_HTML=/app/perf_test_dashboard.html \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app/perf-test-agent

COPY perf-test-agent /app/perf-test-agent
COPY perf_test_dashboard.html /app/perf_test_dashboard.html

RUN python -m pip install --upgrade pip && \
    pip install .

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --no-create-home appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["perf-test-agent-api"]
