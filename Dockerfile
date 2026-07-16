FROM python:3.13.11-slim-bookworm@sha256:20080e807bfc404f8450b185cf0fc95d553462673598549613735f70a5b4d5d0

COPY --from=ghcr.io/astral-sh/uv:0.9.21@sha256:15f68a476b768083505fe1dbfcc998344d0135f0ca1b8465c4760b323904f05a /uv /uvx /bin/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN groupadd --system --gid 999 agent \
    && useradd --system --uid 999 --gid agent --home-dir /app agent

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY entrypoint.sh ./entrypoint.sh
COPY xray ./xray
COPY docs/contracts/v1 ./docs/contracts/v1
RUN uv sync --frozen --no-dev \
    && chmod 0555 /app/entrypoint.sh \
    && chown -R agent:agent /app \
    && mkdir /generated \
    && chown 65532:65532 /generated

USER agent

EXPOSE 8000

CMD ["uvicorn", "src.app:create_app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
