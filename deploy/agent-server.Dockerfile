FROM python:3.12-slim-bookworm@sha256:72d3d75f2639ab82b34b29390ad3d6e0827c775befee94edda8e9976818f488d AS runtime

ARG SOURCE_DATE_EPOCH=1735689600
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/agent-server \
    PATH=/app/.venv/bin:${PATH} \
    SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}

WORKDIR /app

RUN groupadd --system --gid 10001 focusproof \
    && useradd --system --uid 10001 --gid focusproof --home-dir /app focusproof \
    && mkdir -p /app/var \
    && chown -R focusproof:focusproof /app \
    && touch -h -d "@${SOURCE_DATE_EPOCH}" \
        /etc /etc/group /etc/gshadow /etc/passwd /etc/shadow /app /app/var

RUN --mount=type=bind,source=requirements/production.lock,target=/mnt/input/production.lock \
    install -D -m 0644 -o root -g root /mnt/input/production.lock /app/requirements/production.lock \
    && python -m venv /app/.venv \
    && /app/.venv/bin/python -m pip install --no-cache-dir --require-hashes --no-deps \
        -r /app/requirements/production.lock \
    && find /app/.venv -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} + \
    && touch -h -d "@${SOURCE_DATE_EPOCH}" /app/requirements/production.lock /app/requirements /app

RUN --mount=type=bind,source=alembic.ini,target=/mnt/input/alembic.ini \
    --mount=type=bind,source=pyproject.toml,target=/mnt/input/pyproject.toml \
    install -m 0644 -o root -g root /mnt/input/alembic.ini /app/alembic.ini \
    && install -m 0644 -o root -g root /mnt/input/pyproject.toml /app/pyproject.toml \
    && touch -h -d "@${SOURCE_DATE_EPOCH}" /app/alembic.ini /app/pyproject.toml /app
RUN --mount=type=bind,source=agent-server/focusproof,target=/mnt/input/focusproof \
    mkdir -p /app/agent-server/focusproof \
    && cp -a --no-preserve=ownership /mnt/input/focusproof/. /app/agent-server/focusproof/ \
    && chown -R root:root /app/agent-server/focusproof \
    && find /app/agent-server/focusproof -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} + \
    && touch -h -d "@${SOURCE_DATE_EPOCH}" /app/agent-server /app
RUN --mount=type=bind,source=agent-server/migrations,target=/mnt/input/migrations \
    mkdir -p /app/agent-server/migrations \
    && cp -a --no-preserve=ownership /mnt/input/migrations/. /app/agent-server/migrations/ \
    && chown -R root:root /app/agent-server/migrations \
    && find /app/agent-server/migrations -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} + \
    && touch -h -d "@${SOURCE_DATE_EPOCH}" /app/agent-server /app

USER focusproof
EXPOSE 8000

CMD ["uvicorn", "focusproof.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

FROM runtime AS core
ENV FOCUSPROOF_MEDIA_ENABLED=false

FROM runtime AS media
ENV FOCUSPROOF_MEDIA_ENABLED=true

FROM core AS final
