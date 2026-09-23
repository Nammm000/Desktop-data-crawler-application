.PHONY: setup up down backend frontend logs secret help

BACKEND_VENV  := backend/.venv
FRONTEND_VENV := frontend/.venv
COMPOSE       := docker compose -f backend/docker-compose.yml

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Create both venvs, install pinned deps, write backend/.env with fresh secrets
	./scripts/init_env.sh

up: ## Start everything: MongoDB -> API (:8000) -> desktop app
	./run.sh

down: ## Stop the API and MongoDB (data volume is kept)
	@-if [ -f .backend.pid ]; then kill "$$(cat .backend.pid)" 2>/dev/null || true; rm -f .backend.pid; fi
	$(COMPOSE) stop

backend: ## Run only the API with auto-reload (dev)
	$(COMPOSE) up -d
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

frontend: ## Run only the desktop app (assumes the API is already up)
	cd frontend && .venv/bin/python main.py

logs: ## Tail the API log from the last `make up`
	@tail -f .backend.log

secret: ## Print a fresh JWT secret / Fernet key pair
	@python3 -c "import secrets,base64; print('JWT_SECRET=' + secrets.token_urlsafe(48)); print('CREDENTIALS_ENCRYPTION_KEY=' + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
