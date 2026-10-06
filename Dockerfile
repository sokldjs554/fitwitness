FROM node:22-bookworm-slim AS web
WORKDIR /build/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src PORT=8787
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxrender1 libxext6 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
COPY src/ src/
COPY --from=ghcr.io/astral-sh/uv:0.9.4 /uv /usr/local/bin/uv
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
COPY scripts/ scripts/
COPY docs/evaluation/ docs/evaluation/
RUN python -m fitwitness.data.generate
RUN python -m fitwitness.claims.synth var/claims 120
COPY --from=web /build/web/dist web/dist/
RUN useradd --uid 10001 --create-home app && chown -R app:app /app
USER app
EXPOSE 8787
CMD ["sh","-c","python scripts/setup.py && exec uvicorn fitwitness.api.app:create_app --factory --host 0.0.0.0 --port ${PORT}"]
