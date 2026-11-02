# Saarthi 2.0 - production image
#
# Layout inside the container mirrors the repository, because config.py resolves
# PROJECT_ROOT as three parents above backend/app/core/config.py:
#   /app/backend  /app/frontend  /app/data/knowledge  /app/config  /app/data/storage
#
# What is deliberately NOT baked in:
#   config/api_keys.env  -> mounted read-only at runtime (keys never live in an image)
#   data/storage         -> named volume (sqlite + vector index survive a rebuild)
# What IS baked in: data/knowledge (the RAG corpus) and data/models (the ONNX leaf
# classifier), because both are versioned with the code that depends on them.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
# The public site, served at / with the dashboard at /app.
#
# Its two background videos (84 MB) are NOT in the repository: they sit in the build
# context on the VPS only, copied there once from the old saarthi stack, and the deploy
# tarball never carries them. They survive a redeploy because tar only overwrites paths
# it contains. Without them the pages fall back to the poster image, which is what
# happens in local development.
COPY landing ./landing
# The VisionCoders agency site, served at the root of team.visioncoders.in.
COPY agency ./agency
# The knowledge console: /kb on every hostname, and the root of SAARTHI_CONSOLE_HOST.
# Three static files; its API is admin-only and lives in backend/app/routers/kb.py.
COPY console ./console
COPY data/knowledge ./data/knowledge
# The exported leaf-disease model (ONNX + model card), ~17 MB.
COPY data/models ./data/models

# Non-root. Note the host's mounted api_keys.env must be chown 10001:10001, or
# every key silently reads as "not configured" inside the container.
RUN useradd --uid 10001 --create-home saarthi \
 && mkdir -p /app/data/storage /app/config \
 && chown -R 10001:10001 /app

USER 10001
WORKDIR /app/backend
EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=6s --start-period=45s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8010/api/health', timeout=5).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
