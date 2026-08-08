.PHONY: up

up:
	cp .env.example .env
	docker compose up -d db
	docker compose run --build --rm load
