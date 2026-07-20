# SMR — operational shortcuts (PRD Sprint 10 §Deploy + Observabilidade).
#
# Common dev/ops workflows are wrapped as make targets so the operator
# doesn't need to remember long docker stack / celery incantations.

STACK_NAME ?= smr
WEB_SERVICE = $(STACK_NAME)_web
HEALTH_URL ?= https://smr.trade/health/

.DEFAULT_GOAL := help

.PHONY: help deploy build migrate migrate-local collectstatic superuser \
        logs logs-web logs-worker-discovery logs-worker-tracking \
        logs-worker-scoring logs-worker-alerts logs-beat logs-db logs-redis \
        health ps restart-web backup restore clean

help:
	@echo "SMR Makefile targets:"
	@echo "  make deploy                  Build image + deploy stack + migrate + superuser"
	@echo "  make build                   Build the smr/web:latest image"
	@echo "  make migrate                 Run Django migrations inside the web service container"
	@echo "  make migrate-local           Run migrations on local dev DB"
	@echo "  make collectstatic           Collect static files"
	@echo "  make superuser               Idempotently create the admin superuser"
	@echo "  make logs                    Tail logs of all services in the stack"
	@echo "  make logs-web                Tail web logs"
	@echo "  make logs-worker-discovery   Tail worker-discovery logs"
	@echo "  make logs-worker-tracking    Tail worker-tracking logs"
	@echo "  make logs-worker-scoring     Tail worker-scoring logs"
	@echo "  make logs-worker-alerts      Tail worker-alerts logs"
	@echo "  make logs-beat               Tail beat logs"
	@echo "  make logs-db                 Tail db logs"
	@echo "  make logs-redis              Tail redis logs"
	@echo "  make health                  Curl the public /health/ endpoint"
	@echo "  make ps                      List stack services"
	@echo "  make restart-web             Restart the web service"
	@echo "  make backup                  Run ./backup.sh (local pg_dump)"
	@echo "  make backup-remote           Run ./backup.sh --remote (in-container pg_dump)"
	@echo "  make clean                   Remove the stack and its volumes (DESTRUCTIVE)"

deploy:
	./deploy.sh

build:
	docker build -t smr/web:latest .

migrate:
	docker exec $$(docker service ps --filter "desired-state=running" --format "{{.Name}}.{{.ID}}" $(WEB_SERVICE) | head -1) \
		python3 manage.py migrate --noinput

migrate-local:
	python3 manage.py migrate --noinput

collectstatic:
	docker exec $$(docker service ps --filter "desired-state=running" --format "{{.Name}}.{{.ID}}" $(WEB_SERVICE) | head -1) \
		python3 manage.py collectstatic --noinput

superuser:
	docker exec $$(docker service ps --filter "desired-state=running" --format "{{.Name}}.{{.ID}}" $(WEB_SERVICE) | head -1) \
		python3 manage.py createsuperuser --noinput \
			--username $${DJANGO_SUPERUSER_USERNAME:-admin} \
			--email $${DJANGO_SUPERUSER_EMAIL:-admin@smr.trade} \
		|| echo "[superuser] already exists or skipped"

logs:
	docker service logs --tail 200 -f $(STACK_NAME)_web $(STACK_NAME)_worker-discovery \
		$(STACK_NAME)_worker-tracking $(STACK_NAME)_worker-scoring \
		$(STACK_NAME)_worker-alerts $(STACK_NAME)_beat

logs-web:
	docker service logs --tail 200 -f $(STACK_NAME)_web

logs-worker-discovery:
	docker service logs --tail 200 -f $(STACK_NAME)_worker-discovery

logs-worker-tracking:
	docker service logs --tail 200 -f $(STACK_NAME)_worker-tracking

logs-worker-scoring:
	docker service logs --tail 200 -f $(STACK_NAME)_worker-scoring

logs-worker-alerts:
	docker service logs --tail 200 -f $(STACK_NAME)_worker-alerts

logs-beat:
	docker service logs --tail 200 -f $(STACK_NAME)_beat

logs-db:
	docker service logs --tail 200 -f $(STACK_NAME)_db

logs-redis:
	docker service logs --tail 200 -f $(STACK_NAME)_redis

health:
	@command -v curl >/dev/null 2>&1 || { echo "curl not installed"; exit 1; }
	curl -fsS $(HEALTH_URL) | python3 -m json.tool || curl -s -o /dev/null -w "%{http_code}\n" $(HEALTH_URL)

ps:
	docker service ls --filter "label=com.docker.stack.namespace=$(STACK_NAME)"

restart-web:
	docker service update --force $(STACK_NAME)_web

backup:
	./backup.sh

backup-remote:
	./backup.sh --remote

clean:
	@read -p "This will remove stack $(STACK_NAME) AND its volumes. Type YES to continue: " ans; \
	if [ "$$ans" = "YES" ]; then \
		docker stack rm $(STACK_NAME); \
		echo "Volumes will be freed once services are down. Run 'docker volume ls | grep $(STACK_NAME)' to verify."; \
	else \
		echo "Aborted."; \
	fi
