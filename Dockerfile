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
CMD ["sh", "-c", "mkdir -p \"$LEDGERHAWK_DATA_DIR\" && exec uvicorn ledgerhawk.api.app:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*'"]
