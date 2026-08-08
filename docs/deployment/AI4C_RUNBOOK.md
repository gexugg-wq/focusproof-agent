# AI4C staging operations runbook

This runbook operates the single-host AI4C staging proof. It is not a public
deployment guide. The stack has exactly one FastAPI worker, one Next production
server, a private PostgreSQL database, and official OpenHands native persistence.
PostgreSQL product facts and OpenHands persistence are one recovery unit: never
back up, restore, retain, or delete one without the other.

## Safety rules and prerequisites

- Work from the accepted application revision on a Linux host. Never use a
  branch, mutable SDK path, or locally copied OpenHands implementation.
- Keep the operator env file and all secret files outside the repository. The
  env file contains paths and public metadata only. Never put secret values in
  command arguments, shell history, manifests, tickets, or logs.
- Unset provider keys for every deterministic operation. Staging acceptance uses
  the official OpenHands SDK `TestLLM`; it must not call a real provider.
- Keep PostgreSQL private and application ports bound to `127.0.0.1`.
- Do not use `docker compose down --volumes` except in an explicitly disposable
  drill. A normal stop preserves both named volumes.

Run the capability gate before deploy, backup, restore, or a recovery drill:

```sh
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY \
  -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python scripts/check_ai4c_capabilities.py \
  --require container_cli compose postgres_client
```

All three capabilities must report `available`. A missing capability is a
blocker, never a skipped success.

## Deploy, migrate, and verify readiness

Use the accepted `deploy/compose.staging.yml` and an operator-owned paths file:

```sh
docker compose --env-file /secure/staging.paths \
  -f deploy/compose.staging.yml pull
docker compose --env-file /secure/staging.paths \
  -f deploy/compose.staging.yml up --build -d --wait
curl --fail --silent --show-error http://127.0.0.1:18080/ready
```

Compose waits for PostgreSQL, runs the one-shot `migrate` service, and starts
the backend only after `alembic upgrade head` succeeds. Application startup
checks the exact migration head and never migrates implicitly. `/ready` must
return success without making an LLM call; `/health` is the liveness check.

Record the application revision, Compose file digest, canonical release
digests, migration head, and readiness outcome. OCI image IDs are diagnostic,
not the release identity.

To stop services while retaining data:

```sh
docker compose --env-file /secure/staging.paths \
  -f deploy/compose.staging.yml down --timeout 30
```

## Paired backup

Schedule a quiet period. The backup helper creates
`.focusproof-maintenance.lock` in the stable sibling coordination directory
`.<OPENHANDS_DATA_DIR name>.focusproof-recovery/`. This directory is outside the
OpenHands tree that restore replaces. The single worker admits every `POST`,
`PUT`, `PATCH`, and `DELETE` through the same cross-process file barrier. Backup
creates the marker, waits for an already-entered writer to leave, and holds the
drain barrier through both `pg_dump` and the official OpenHands archive. New
writes receive HTTP 503. `/health` and `/ready` remain available during ordinary
maintenance. Do not stop PostgreSQL until `pg_dump` completes.

The operator process needs `pg_dump`, access to the private PostgreSQL endpoint,
and direct access to the mounted OpenHands data directory. On a native Linux
Docker host the named-volume mountpoint can be resolved without printing its
contents:

```sh
docker volume inspect --format '{{ .Mountpoint }}' \
  focusproof-staging_focusproof-openhands-data
```

Export `OPENHANDS_DATA_DIR` to that path and inject `DATABASE_URL` from the
secret manager into the operator process. Do not echo either value. Use a new,
nonexistent output directory for every recovery unit:

```sh
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY \
  -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  PYTHONPATH=agent-server:scripts .venv/bin/python -c \
  'import os; from pathlib import Path; from ai4c_backup import create_backup; create_backup(database_url=os.environ["DATABASE_URL"], openhands_data_dir=Path(os.environ["OPENHANDS_DATA_DIR"]), output_dir=Path(os.environ["RECOVERY_OUTPUT_DIR"]))'
```

The function uses bounded argument-array subprocesses, moves the password only
through `PGPASSWORD`, creates deterministic OpenHands archive metadata, and
publishes the completed directory with one atomic rename. A valid recovery unit
contains exactly:

- `database.dump`
- `openhands.tar.gz`
- `manifest.json`

The manifest becomes visible only after both artifacts and their SHA-256
digests are complete. Failure leaves no published bundle and removes partial
work. Record only revision, timestamps, counts, digests, paths, and the outcome;
never record evidence text, answers, tokens, URLs containing credentials, or
secret values.

The helper removes its maintenance marker on both success and failure. Never
create or remove that marker manually during a normal backup. After success,
confirm `/ready`. If backup fails, keep writers stopped until the failure is
understood; do not label a partial directory as recoverable.

## Paired restore and recovery drill

Restore only into the approved target. The helper uses the same maintenance and
drain barrier as backup, so all admitted writers finish before either store is
changed and no new writer enters during restore.
Check out the exact `application_revision` from the manifest first. The restore
helper rejects revision mismatch, a bundle containing anything other than the
three named files, missing or extra manifest fields, digest mismatch, symlinks,
hard links, absolute members, traversal, and unsupported archive members before
changing either store.

Prepare a fresh PostgreSQL database and an absent or disposable OpenHands target,
then inject `DATABASE_URL`, `OPENHANDS_DATA_DIR`, and `RECOVERY_MANIFEST` without
printing them:

```sh
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY \
  -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  PYTHONPATH=agent-server:scripts .venv/bin/python -c \
  'import os; from pathlib import Path; from ai4c_restore import restore_backup; restore_backup(manifest_path=Path(os.environ["RECOVERY_MANIFEST"]), database_url=os.environ["DATABASE_URL"], openhands_data_dir=Path(os.environ["OPENHANDS_DATA_DIR"]))'
```

Before `pg_restore`, restore creates `.focusproof-recovery-incomplete` in the
stable coordination directory. `pg_restore` runs with
`--clean --if-exists --exit-on-error`; native persistence is extracted into a
sibling temporary directory and replaces the target only after archive
validation. The helper then verifies the restored native member set and file
digests. Only a successful paired restore and post-restore verification clears
the incomplete marker atomically while still holding the drain barrier. If PG
restore succeeds but filesystem replacement or verification fails, the marker
remains: `/health` stays 200, `/ready` returns 503 `recovery_incomplete`, and all
writes return 503. Rerun the complete paired restore; never clear the incomplete
marker to make readiness green.

The database URL accepts `postgres`/`postgresql` and SQLAlchemy driver suffixes.
Passwords are supplied only through `PGPASSWORD`. Supported query settings are
`host` for Unix sockets plus `sslmode`, `sslrootcert`, `sslcert`, `sslkey`,
`target_session_attrs`, `connect_timeout`, and `options`, which are mapped to a
minimal libpq environment. Unknown, duplicate, empty, or control-character
query values are rejected rather than ignored.

Start the application at the manifest revision, verify `/ready`, and validate:

- session IDs, owner IDs, conversation IDs, evidence IDs and hashes;
- answer question IDs, values and versions;
- review IDs, status, score and native source event IDs;
- official OpenHands native event IDs, event types, and counts.

Run the same restore once more while writers remain stopped. Reconciliation is
accepted only if the product snapshot is identical and neither review count nor
native event count grows. The automated authorization boundary is:

```sh
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY \
  -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python -m pytest \
  agent-server/tests/ai4c/test_backup_restore.py::test_staging_external_restores_paired_product_and_native_state_idempotently \
  -q -m staging_external
```

The drill uses disposable PostgreSQL data and must remove both its container and
named volume with checked commands, then prove both are absent with Docker
inspect before creating the fresh target volume. Never point it at retained
data.

## Recovery coordination state

The stable sibling coordination directory contains two marker files and two
advisory-lock files:

- `.focusproof-maintenance.lock` means a backup or restore is active or was
  interrupted before cleanup.
- `.focusproof-recovery-incomplete` means paired restore has not completed and
  verified; readiness and writes intentionally fail closed.
- `writer-admission.lock` and `writer-drain.lock` are reusable `filelock`
  backing files. Their mere existence is normal and does not mean a lock is
  held.

Treat a maintenance marker as active unless the `agent-server` is stopped, the
backup/restore operator process has exited, and host lock inspection shows no
process holding either advisory lock. If any check is uncertain, leave the
marker and investigate. Only after all three checks may an operator remove a
stale maintenance marker, record the incident, restart the app, and verify both
`/health` and `/ready`. Do not delete the advisory-lock files.

An incomplete marker is never a stale-lock cleanup item. Preserve it and the
failed stores, diagnose the paired failure, then rerun restore from the exact
three-file bundle at its manifest revision. The restore helper clears the marker
only after PostgreSQL, OpenHands replacement, and post-restore verification all
succeed. If no valid paired bundle exists, keep the service fail closed and
escalate; do not manually unlink the marker or reopen writes.

## Rollback

Rollback code, images, Compose, migrations, and the paired recovery unit
together. Stop writers, preserve the failed deployment for diagnosis, check out
the accepted revision, and restore a recovery unit created by that same
revision. Never start older code against a newer unsupported schema and never
restore only PostgreSQL or only OpenHands state.

If the new deployment has not accepted writes and its migration is explicitly
reversible, the one-shot migration service may run the approved downgrade.
Otherwise restore the pre-deploy paired unit. Validate the same IDs and native
event counts before reopening writes.

## Incident shutdown and outages

For suspected corruption, credential exposure, or identity bypass:

1. Enter maintenance mode and stop `frontend` and `agent-server`.
2. Preserve PostgreSQL and OpenHands volumes; do not run `down --volumes`.
3. Revoke exposed credentials in their owning systems without printing them.
4. Capture secret-free revision, digest, health, request status, latency, auth
   outcome, admission rejection, provider aggregate usage/cost, and timestamps.
5. Resume only after identity, database schema, runtime registry, and paired
   persistence checks pass.

During a provider outage, keep health/readiness and identity available, reject
or bound reviews through the existing provider-admission limits, and do not
switch staging to an unapproved provider or real-LLM fallback. Existing product
facts and native events remain authoritative.

During an identity-provider outage, fail closed: do not enable anonymous staging
access, mint local bearer tokens, disable TLS verification, or substitute a
JWKS-only probe. Keep writes stopped until issuer, audience, TLS chain, JWKS,
and principal resolution recover.

Operational JSON uses bounded route templates and aggregate numeric fields only.
It must never contain session IDs, owner IDs, user content, evidence, answers,
tokens, credentials, or provider responses.

## Retention and limitations

Retain each manifest, database dump, and OpenHands archive as one access-controlled
unit. Encrypt it at rest outside the repository, test restoration on a schedule,
and delete all three files together when the approved retention period expires.
Security audit retention defaults to 30 days
(`FOCUSPROOF_SECURITY_AUDIT_RETENTION_SECONDS=2592000`); backup retention is an
operator policy and must be documented separately. A legal hold applies to the
whole paired unit.

This topology is single-host and single-worker. It has no distributed lock,
scheduler, cross-host failover, online snapshot coordinator, or multi-worker
recovery protocol. Maintenance uses a cross-process filesystem admission/drain
barrier shared with that one worker. Scale-out or public deployment requires a
separate design and approval.
