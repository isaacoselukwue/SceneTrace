# SceneTrace: one application process serving the API, the worker and the built client.
FROM node:22.17.0-bookworm-slim AS client
WORKDIR /app/client
COPY client/package.json client/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY client/ ./
RUN npx vite build

FROM python:3.12.12-slim-bookworm AS app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 scenetrace
RUN pip install --no-cache-dir uv==0.12.9
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY scenetrace ./scenetrace
COPY reference_core ./reference_core
COPY config ./config
COPY contracts ./contracts
COPY prompts/runtime ./prompts/runtime
COPY samples/synthetic ./samples/synthetic
COPY demos ./demos
COPY reports/provider-capabilities.json ./reports/provider-capabilities.json
COPY --from=client /app/client/dist ./client/dist
RUN mkdir -p /data && chown scenetrace /data
USER scenetrace
ENV PYTHONPATH=/app SCENETRACE_DATA_DIR=/data SCENETRACE_PORT=8765 PYTHONUNBUFFERED=1
EXPOSE 8765
VOLUME ["/data"]
CMD ["/app/.venv/bin/python", "-m", "scenetrace", "serve", "--host", "0.0.0.0", "--import-demos", "demos"]
