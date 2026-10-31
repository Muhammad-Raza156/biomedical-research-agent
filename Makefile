ORCH = docker compose -f orchestration-service/docker-compose.yml
API  = docker compose -f api-service/docker-compose.yml

.PHONY: up down ps logs

up:
	-docker network create research-net
	$(ORCH) up -d --build
	$(API) up -d --build

down:
	$(API) down
	$(ORCH) down

ps:
	$(ORCH) ps
	$(API) ps

logs:
	$(ORCH) logs -f --tail=100 worker

PY ?= python
TIER ?=

.PHONY: test-unit test eval

# Unit + Temporal workflow + replay tests: no running stack needed.
test-unit:
	cd orchestration-service && $(PY) -m pytest -m "not integration" -q
	cd api-service && $(PY) -m pytest -m "not integration" -q

# Adds the integration tests (needs `make up` and GRAPH_PASSWORD / GRAPH_READER_PASSWORD in the environment).
test: test-unit
	cd orchestration-service && $(PY) -m pytest -m integration -q
	cd api-service && $(PY) -m pytest -m integration -q

# Every question through the public API (needs GRAPH_READER_PASSWORD); TIER=1|2|3|adversarial runs one tier.
eval:
	$(PY) eval/run_eval.py --api http://localhost:8000 $(if $(TIER),--tier $(TIER),)
