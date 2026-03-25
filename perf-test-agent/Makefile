API_PORT ?= 8002
HOST ?= 127.0.0.1
PORT ?= $(API_PORT)

.PHONY: run serve-dashboard

run:
	HOST="$(HOST)" PORT="$(PORT)" bash scripts/run_local.sh

serve-dashboard:
	../.venv/bin/python -m http.server 8081 --directory ..
