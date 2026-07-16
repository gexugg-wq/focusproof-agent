# Vendor-Neutral Staging Deployment

## Release status

This topology is for private staging only. The development identity is a public
deployment blocker. Production authentication is not implemented or complete,
no production identity provider has been selected, and no public ingress may be
enabled until AI0 approves that design.

No real provider smoke runs by default. The loopback deterministic server is
the required pre-staging gate.

## Topology

A vendor-neutral staging deployment has:

1. a TLS reverse proxy or load balancer;
2. a Next.js server hosting the UI and restricted same-origin BFF;
3. a private FastAPI Agent Server;
4. a service-account-owned persistence volume for SQLite, conversation data,
   and locks;
5. an operator-controlled secret manager for any approved provider credential;
6. centralized structured logs, metrics, backup storage, and alerting.

The browser must reach only the Next.js origin. The BFF reaches the Agent
Server over a private address. The Agent Server is not a browser CORS endpoint,
and the reverse proxy must not turn it into one.

## Preconditions

- Task 5 artifact, security, backend, Ruff, Mypy, and hygiene gates pass.
- AI0 records the staging owner, incident contact, maintenance window, and
  rollback authority.
- The approved Python 3.12 image contains the exact OpenHands SDK build.
- Database and conversation volumes have restrictive service-account
  permissions.
- A backup and restore rehearsal succeeds on staging data.
- Proxy request-size and timeout limits are reconciled with application limits.
- Any provider credential is injected at process start from a secret manager,
  never baked into an image or frontend environment.

These preconditions do not authorize public release.

## Environment names

Agent Server:

- `DATABASE_URL`
- `FOCUSPROOF_DATA_DIR`
- `FOCUSPROOF_LOCK_TIMEOUT_SECONDS`
- approved provider-specific key names, supplied only by the secret manager

Next.js server:

- `FOCUSPROOF_API_BASE_URL`
- `APP_BASE_URL`, when required by the hosting platform

Do not expose provider names or values through `NEXT_PUBLIC_*`. Do not forward
browser `authorization`, `cookie`, or provider-key headers to the Agent Server.

## Build and migration sequence

1. Build immutable Agent Server and frontend artifacts from the reviewed
   commit.
2. Scan the artifacts and dependency manifests.
3. Stop staging writes and take a verified backup.
4. Run Alembic upgrade with the same image that will serve traffic:

   ```bash
   python3.12 -c '
   import os
   from alembic import command
   from alembic.config import Config
   config = Config("alembic.ini")
   config.set_main_option("script_location", "agent-server/migrations")
   config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
   command.upgrade(config, "head")
   '
   ```

   The service environment must supply `DATABASE_URL`; this command explicitly
   copies it into Alembic `Config` before migration.

5. Start the private Agent Server.
6. Wait for `/health` to report `status=ok` and `readiness=null`.
7. Start Next.js with the private Agent Server BFF target.
8. Enable only private staging ingress.
9. Run the non-secret smoke and manually inspect logs.

Alembic downgrade is not an automatic rollback strategy. A code rollback that
cannot read the upgraded schema requires a tested restore or a reviewed
downgrade on a maintenance copy first.

## Reverse-proxy boundary

The proxy must:

- terminate TLS with approved certificates and protocols;
- preserve the original request method and bounded body;
- set, rather than trust, forwarding headers;
- reject direct access to internal Agent Server routes;
- apply a request body ceiling no larger than the application contract;
- use timeouts compatible with the bounded review timeout;
- avoid logging request bodies, query strings, authorization, cookies, or
  provider headers;
- return generic gateway errors without upstream exception details.

The BFF allowlist remains authoritative for browser-accessible API paths.

## Staging smoke

First verify health and a non-review session/evidence flow:

```bash
python3.12 scripts/ai4b_smoke.py \
  --base-url https://private-staging.example.invalid
```

The command prints generated IDs and statuses only. It must not print evidence,
answers, environment values, provider responses, or secrets.

Do not pass `--scripted-review` to a real provider deployment. That option is
reserved for the deterministic loopback test server.

## Rollout and rollback decision

Admit private staging traffic only after health, migration, storage, BFF, log
redaction, and smoke checks pass. Roll back when readiness degrades, migration
state is unexpected, review failures exceed the agreed threshold, storage is
unavailable, or secret exposure is suspected.

Rollback order:

1. disable ingress and stop new review admission;
2. allow orderly shutdown and confirm handles close;
3. preserve logs and a forensic copy without exposing learner content;
4. restore the prior application image if schema-compatible;
5. otherwise restore the verified pre-deployment database and conversation
   data as one consistency set;
6. rotate credentials when exposure is possible;
7. rerun health and non-secret smoke before reopening private staging.

## Explicit exclusions

This staging guide does not add another runtime, scheduler, event loop, tool
protocol, wallet, transaction, contract, chain, or domain-specific dependency.
It does not authorize production authentication, public deployment, or a real
LLM smoke.
