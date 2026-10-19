# Build the web app, then serve it and the API from one Python image.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    LEDGERHAWK_DATA_DIR=/var/data/ledgerhawk LEDGERHAWK_WEB_DIST=/app/web/dist
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY ledgerhawk/ ledgerhawk/
RUN pip install --no-cache-dir .
COPY --from=web /web/dist web/dist
EXPOSE 10000
# Keep idle connections open longer than Render's proxy does, so it never reuses one uvicorn already closed (a fast 502).
# One worker: running jobs, rate-limit pauses and caches live in the process, and Render sets WEB_CONCURRENCY to the CPU
# count on larger plans, which uvicorn would otherwise follow (a second worker reports the first one's jobs as cut off).
CMD ["sh", "-c", "mkdir -p \"$LEDGERHAWK_DATA_DIR\" && exec uvicorn ledgerhawk.api.app:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]
