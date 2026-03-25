API_PORT ?= 8002

.PHONY: run-api serve-dashboard

run-api:
	$(MAKE) -C perf-test-agent run PORT=$(API_PORT)

serve-dashboard:
	.venv/bin/python -m http.server 8081 --directory .
