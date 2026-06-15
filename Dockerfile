FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY src ./src
COPY tests ./tests

RUN python -m pip install --upgrade pip \
    && python -m pip install '.[all,dev]'

RUN mkdir -p /home/token-saver/.codex /state \
    && chmod 0777 /home/token-saver /home/token-saver/.codex /state

ENV HOME=/home/token-saver \
    TOKEN_SAVER_CONFIG=/config/config.toml \
    TOKEN_SAVER_DB=/state/tasks.sqlite3 \
    TELEGRAM_BOT_TOKEN_FILE=/run/secrets/telegram_bot_token \
    TOKEN_SAVER_LOCAL_URL=http://host.docker.internal:8080/v1

ENTRYPOINT ["token-saver"]
CMD ["telegram"]
