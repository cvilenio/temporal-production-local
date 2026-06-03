.PHONY: lint format check fix

lint:
	uv run ruff check .

format:
	uv run ruff format .

fix:
	uv run ruff check --fix .

down:
	docker compose down -v

up:
	docker compose up --build

fresh: down	up