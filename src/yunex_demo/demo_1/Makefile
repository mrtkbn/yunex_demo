# Works from any directory: `make -C src/yunex_demo/demo_1 <target>` or `cd` here first.
DIR     := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ROOT    := $(abspath $(DIR)/../../..)
ENVFILE := $(ROOT)/.env
COMPOSE := docker compose --env-file $(ENVFILE) -f $(DIR)/docker-compose.yml
UV      := uv run --project $(ROOT)
DATA    := $(abspath $(DIR)/../data)

.DEFAULT_GOAL := help
.PHONY: help setup env network up down airflow-up airflow-down airflow-run airflow-app destroy rebuild ingest transform pipeline app run run-local offline test clean

help:  ## show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

setup:  ## install dependencies with uv
	uv sync --project $(ROOT)

env:  ## create .env from .env.example if missing (then set MINIO_USER / MINIO_PASSWORD)
	@test -f $(ENVFILE) || { cp $(ROOT)/.env.example $(ENVFILE); echo "Created $(ENVFILE): set MINIO_USER and MINIO_PASSWORD"; }

network:  ## create the Docker network shared by MinIO and Airflow
	@docker network inspect fx_net >/dev/null 2>&1 || docker network create fx_net

up: env network  ## start MinIO in Docker and wait until it is ready
	$(COMPOSE) up -d
	@until curl -sf http://localhost:9000/minio/health/live >/dev/null; do sleep 1; done
	@echo "MinIO ready: API :9000, console http://localhost:9001"

down:  ## stop MinIO (data is kept)
	$(COMPOSE) down

airflow-up: up  ## start Airflow (Astro) next to MinIO: http://localhost:8080
	@# astro can exit non-zero after the containers are up (airflow_settings.yaml import needs a TTY),
	@# so the real check is the Airflow health endpoint
	-cd $(ROOT)/airflow && astro dev start --no-browser
	@for i in $$(seq 1 90); do \
		curl -sf http://localhost:8080/api/v2/monitor/health >/dev/null && { echo "Airflow ready: http://localhost:8080"; exit 0; }; \
		sleep 2; \
	done; echo "Airflow did not become healthy"; exit 1

airflow-down:  ## stop Airflow (MinIO keeps running)
	cd $(ROOT)/airflow && astro dev stop

# Runs the DAG once and waits for it to finish; fails if any task fails
airflow-run:  ## run the fx_pipeline DAG once and wait (needs `make airflow-up` first)
	@C=$$(docker ps -q --filter "name=fx-pipeline.*scheduler"); \
	test -n "$$C" || { echo "Airflow is not running: run 'make airflow-up' first"; exit 1; }; \
	docker exec $$C airflow dags test fx_pipeline

airflow-app: airflow-run app  ## run the DAG, then start Streamlit once it succeeded

destroy:  ## DELETE Airflow (incl. run history), MinIO and its data, and the fx_net network
	-cd $(ROOT)/airflow && astro dev kill
	-$(COMPOSE) down -v
	-docker network rm fx_net

rebuild:  ## from scratch: destroy everything, start MinIO + Airflow, run the DAG, start Streamlit
	$(MAKE) destroy
	$(MAKE) airflow-up
	$(MAKE) airflow-app

ingest: setup  ## 1. ECB + CBAR rates -> MinIO raw/ (or local files when FX_LOCAL=1)
	$(UV) python $(DIR)/ingest.py

transform: setup  ## 2. raw -> silver -> gold star schema
	$(UV) python $(DIR)/transform.py

pipeline: ingest transform  ## ingest + transform

app: setup  ## 3. start the Streamlit demo (http://localhost:8501)
	$(UV) streamlit run $(DIR)/app.py

run: up pipeline app  ## everything with MinIO: start Docker, ingest, transform, app

# FX_LOCAL=1 makes ingest, transform and app use local files instead of MinIO
run-local: export FX_LOCAL=1
run-local: pipeline app  ## no Docker: live API -> local files, then the app

test: setup  ## run unit tests
	$(UV) pytest -q $(ROOT)/tests/demo_1

offline: export FX_LOCAL=1
offline: setup  ## no Docker, no internet: synthetic data -> local files, then the app
	$(UV) python $(DIR)/ingest.py --offline
	$(UV) python $(DIR)/transform.py
	$(UV) streamlit run $(DIR)/app.py

clean:  ## delete local data and caches (MinIO data is not touched)
	rm -rf $(DATA) $(ROOT)/.pytest_cache
	find $(DIR) -name __pycache__ -prune -exec rm -rf {} +
