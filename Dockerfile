# Transcribly: one container with the API, the background worker and the static UI.
#
#   docker build -t transcribly .
#   docker run -p 8000:8000 -v transcribly-data:/data transcribly
#
# Build args:
#   PRELOAD_MODEL=small   download a Whisper model into the image (avoids a first-run download)

# ---- frontend -----------------------------------------------------------------
FROM node:24-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# Same-origin API in the single-container build
ENV NEXT_PUBLIC_API_BASE=""
RUN npm run build

# ---- backend ------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TRANSCRIBLY_DATA_DIR=/data \
    TRANSCRIBLY_MODEL_DIR=/models \
    TRANSCRIBLY_FRONTEND_DIST=/app/static \
    PORT=8000

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY backend/requirements.lock.txt ./
RUN pip install -r requirements.lock.txt

COPY backend/app ./app
COPY --from=frontend /src/frontend/out ./static

ARG PRELOAD_MODEL=""
RUN mkdir -p /data /models && chown -R app:app /data /models /app
USER app
RUN if [ -n "$PRELOAD_MODEL" ]; then \
      python -c "from faster_whisper import WhisperModel; WhisperModel('$PRELOAD_MODEL', device='cpu', compute_type='int8', download_root='/models')"; \
    fi

VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health', timeout=4)" || exit 1

ENTRYPOINT ["tini", "--"]
# One process: the API runs the worker threads itself. Use a single uvicorn worker -
# the SQLite job store expects one worker process per data directory.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
