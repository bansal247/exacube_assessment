.PHONY: up test down clean

up:
	cp .env.example .env
	docker compose up -d db
	docker compose run --build --rm load
	docker compose up -d --build api

test:
	docker compose run --build --rm test

down:
	docker compose down

clean:
	docker compose down -v --rmi local --remove-orphans
