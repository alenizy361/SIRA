# Rabit AI Company OS - API control plane
FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git curl && rm -rf /var/lib/apt/lists/*

COPY apps/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY packages /app/packages
COPY services/orchestrator /app/services/orchestrator
COPY services/claude-worker /app/services/claude-worker
COPY apps/api /app/apps/api

ENV PYTHONPATH=/app/apps/api:/app/packages:/app/packages/permission-engine:/app/services:/app/services/claude-worker

# Run as a non-root user so an RCE/path-traversal in the FastAPI app does not
# execute as uid 0 in-container - this is the enforcement the systemd unit
# (infra/systemd/rabit-api.service) already documents as living "in the
# Dockerfile". The agent-specs bind mount is read-only, so read access under
# this uid is sufficient.
RUN useradd -r -u 10001 appuser && chown -R appuser /app
USER appuser

WORKDIR /app/apps/api

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
