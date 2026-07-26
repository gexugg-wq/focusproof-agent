# AI4C staging stack

The staging stack runs one FastAPI worker, one Next production server, and a
private PostgreSQL database. It uses the official OpenHands SDK runtime already
owned by the application; PostgreSQL stores product projections while the
`focusproof-openhands-data` volume stores OpenHands native conversation state.

## Inputs

Copy `.env.example` to a temporary operator-owned location and set only file
paths. Generate the PostgreSQL password, OIDC fingerprint key, signing JWKS,
TLS certificate, TLS key, and CA locally. Never place secret values in an env
file or commit them. Provider API keys are neither required nor accepted by
this stack.

## Start and verify

Run from the repository root:

```sh
docker compose --env-file /path/to/staging.paths \
  -f deploy/compose.staging.yml up --build -d --wait
curl --fail http://127.0.0.1:18080/ready
```

Compose waits for PostgreSQL, runs the one-shot `alembic upgrade head`
migration, then starts the backend. Application startup verifies the database
revision and never runs migrations implicitly. Backend and frontend ports are
published only on loopback; PostgreSQL has no host port.

## Persistence and shutdown

Normal service restarts retain both named volumes. Stop without deleting data:

```sh
docker compose --env-file /path/to/staging.paths \
  -f deploy/compose.staging.yml down --timeout 30
```

Do not add `--volumes` outside a disposable verification run. Backup and restore
coordination is Task 5 and is intentionally not defined here.

## Limits

This topology is single-host and single-worker by design. It is a staging proof,
not a public deployment. Roll back images and Compose together; do not start an
older application against a schema it does not support.
