FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config

RUN pip install --no-cache-dir . \
    && mkdir -p briefs runs state

HEALTHCHECK --interval=5m --timeout=10s --start-period=30s \
    CMD test -f state/service-health.json || exit 1

CMD ["marketanalyzeragents", "schedule"]
