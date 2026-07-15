FROM python:3.13.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.21 /uv /uvx /bin/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN groupadd --system agent \
    && useradd --system --gid agent --home-dir /app agent

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY docs/contracts/v1 ./docs/contracts/v1
RUN uv sync --frozen --no-dev \
    && chown -R agent:agent /app

USER agent

EXPOSE 8000

CMD ["uvicorn", "src.app:create_app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
