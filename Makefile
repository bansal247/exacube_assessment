.PHONY: up test lint typecheck eval down clean

up:
	cp -n .env.example .env
	docker compose up -d db
	docker compose run --build --rm load
	docker compose up -d --build api frontend

test:
	docker compose run --build --rm test

lint:
	docker compose run --build --rm lint ruff check .

typecheck:
	docker compose run --build --rm lint mypy api/app

# Assumes `make up` is already running with a real ANTHROPIC_API_KEY set --
# sends real chat turns, costs real tokens. Results land in eval/results/.
eval:
	docker compose run --build --rm eval

down:
	docker compose down

clean:
	docker compose down -v --rmi local --remove-orphans
