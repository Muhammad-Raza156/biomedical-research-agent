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
