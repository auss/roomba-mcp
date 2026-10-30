# syntax=docker/dockerfile:1
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock .python-version README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin roomba
USER roomba

EXPOSE 8785

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD roomba-mcp healthcheck --url "http://127.0.0.1:${ROOMBA_HTTP_PORT:-8785}/healthz" || exit 1

ENTRYPOINT ["roomba-mcp"]
CMD ["serve"]
