FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# Dependencies first, so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --no-create-home --uid 10001 triage && mkdir /data && chown triage /data
USER triage

ENV PATH="/app/.venv/bin:$PATH" \
    TRIAGE_DATABASE_URL="sqlite+aiosqlite:////data/triage.db"
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"]
CMD ["uvicorn", "triage.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
