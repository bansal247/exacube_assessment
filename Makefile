.PHONY: up test lint typecheck eval loadtest-chat loadtest-artifacts down clean

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

# Assumes `make up` is already running. Cost-bounded (~24 real /chat
# calls total, fixed regardless of how long it runs) -- see
# loadtest/chat.js. Results land in loadtest/results/.
loadtest-chat:
	docker compose run --rm loadtest-chat

# Assumes `make up` is already running. Two real /chat calls in setup(),
# then ramps concurrency against non-LLM endpoints only (list/refresh/
# download) -- see loadtest/artifacts.js. Results land in loadtest/results/.
loadtest-artifacts:
	docker compose run --rm loadtest-artifacts

down:
	docker compose down

clean:
	docker compose down -v --rmi local --remove-orphans
