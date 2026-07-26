FROM python:3.12-slim-bookworm@sha256:72d3d75f2639ab82b34b29390ad3d6e0827c775befee94edda8e9976818f488d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/agent-server

WORKDIR /app

RUN groupadd --system --gid 10001 focusproof \
    && useradd --system --uid 10001 --gid focusproof --home-dir /app focusproof \
    && mkdir -p /app/var \
    && chown -R focusproof:focusproof /app

COPY requirements/production.lock /app/requirements/production.lock
RUN python -m pip install --no-cache-dir --require-hashes --no-deps \
    -r /app/requirements/production.lock

COPY alembic.ini pyproject.toml /app/
COPY agent-server/focusproof /app/agent-server/focusproof
COPY agent-server/migrations /app/agent-server/migrations

USER focusproof
EXPOSE 8000

CMD ["uvicorn", "focusproof.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
