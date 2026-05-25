FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/scripts \
    KAITEN_RUNTIME_DIR=/data/runtime \
    KAITEN_ARTIFACTS_DIR=/data/artifacts \
    TZ=Europe/Moscow

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ ./scripts/
COPY config/ ./config/
COPY prompts/ ./prompts/

RUN mkdir -p /data/runtime /data/artifacts

CMD ["python", "scripts/bot.py"]
