FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY memory/feedback.example.md ./memory/feedback.example.md
COPY scripts ./scripts

RUN pip install --no-cache-dir . \
    && mkdir -p briefs runs memory

HEALTHCHECK --interval=5m --timeout=10s --start-period=30s \
    CMD dailyresearch doctor >/dev/null || exit 1

CMD ["dailyresearch", "schedule"]
