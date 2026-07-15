.PHONY: install run test compose-config

install:
	uv sync --frozen

run:
	uv run uvicorn src.app:create_app_from_env --factory --host 127.0.0.1 --port 8000

test:
	uv run pytest $(ARGS)

compose-config:
	docker compose -f docker-compose.yml config --quiet

