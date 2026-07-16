# FocusProof Operations Runbook

## Operating status

This runbook supports local and private staging operation. Public deployment is
blocked by the development identity. Production authentication is not
implemented or complete, and operations must not describe the shared
development identity as tenant security.

## Start checklist

1. Confirm the reviewed commit and expected Alembic head.
2. Confirm Python 3.12 and the approved OpenHands SDK build.
3. Confirm database, conversation, lock, and backup paths are owned by the
   service account and are not world-readable.
4. Confirm SQLite resides inside `FOCUSPROOF_DATA_DIR`.
5. Confirm provider credentials, if explicitly authorized for private staging,
   come from the secret manager and are absent from shell history and files.
6. Apply Alembic head.
7. Start the Agent Server, wait for readiness, then start the Next.js server.
8. Run the safe smoke and inspect redacted logs.

## Stop checklist

1. Disable ingress or place the private staging environment in maintenance.
2. Stop the Next.js server.
3. Send a normal termination signal to the Agent Server.
4. Wait for new reviews to be rejected, active reviews to be interrupted, and
   native conversations to close.
5. Confirm provider registry release and database engine disposal.
6. Take a post-stop backup when required.

Do not use a forced process kill unless the incident commander records why
orderly shutdown could not complete.

## Health interpretation

`GET /health` returns service process and readiness information:

- `status=ok`, `readiness=null`: startup checks succeeded and the service can
  receive private staging traffic.
- `status=degraded`, `readiness=schema_out_of_date`: stop traffic and reconcile
  the migration revision.
- `status=degraded`, `readiness=database_unavailable`: stop traffic and inspect
  database path, permissions, disk, and connection state.
- transport failure or malformed response: treat the instance as unavailable.

Health does not prove production authentication, model correctness, backup
freshness, or semantic quality.

## Structured and redacted logging

Logs should contain timestamps, severity, stable error code, bounded session or
event identifiers, duration, retryability, and component name. Logs must not
contain:

- raw goals, evidence, answers, findings, or fetched page excerpts;
- raw URLs, userinfo, query strings, redirect targets, or DNS exception text;
- authorization, cookies, provider headers, environment values, or secrets;
- SQL parameter values, database rows, conversation state dumps, or backups.

Restrict log access and retention. A debug request body is not an acceptable
incident diagnostic.

## SQLite backup

For the strongest consistency, stop writes and the Agent Server before backing
up the SQLite database and conversation data.

Example database backup:

```bash
sqlite3 "$DATABASE_FILE" ".backup '$BACKUP_DIR/focusproof.sqlite3'"
```

Copy the conversation/data directory with metadata preserved:

```bash
rsync -a --delete "$FOCUSPROOF_DATA_DIR/" "$BACKUP_DIR/data/"
```

Store database and conversation data under one backup identifier with commit,
Alembic revision, timestamp, checksum, and operator. Encrypt and restrict backup
storage. Never print a backup or learner record during verification.

## Restore

1. Disable traffic and stop both services.
2. Preserve the failed state for investigation.
3. Verify backup checksums, commit, revision, and permissions.
4. Restore the database and conversation directory as one consistency set.
5. Start the Agent Server only.
6. Confirm Alembic current revision and `/health`.
7. Verify a known non-secret staging session or run the safe smoke.
8. Start Next.js and reopen private traffic after operator approval.

Never restore only the SQLite file while retaining incompatible conversation
files, or vice versa.

## Rollback

A compatible code rollback uses the prior immutable image and the current
schema only when that combination was tested. Otherwise restore the verified
pre-deployment consistency set. Run Alembic downgrade only after testing it on a
copy and recording the data-loss implications.

After rollback, verify health, idempotent replay, event ordering, and a
non-secret smoke. Rotate provider credentials if the rollback follows suspected
exposure.

## Failure diagnosis

### Schema out of date

Compare `alembic current` and `alembic heads`. Do not serve traffic until they
match the reviewed release. Preserve migration output without database values.

### Database unavailable

Check path containment, directory ownership, disk capacity, filesystem errors,
SQLite locks, and whether another restore is running. Do not delete lock or
database files as a first response.

### Session busy or retryable 503

Inspect bounded duration and concurrency metrics. A concurrent identical Answer
may return a retryable busy response; retrying must not create another version
or native event. Do not treat permanent `session_finalized` as retryable.

### Review timeout or cancellation

Confirm the session is not reviewed and no `review.completed` event exists.
Inspect provider latency and native interrupt completion. Retry only after the
recoverable failure has released the session lock.

### URL verification failure

Use the stable observation category. Do not request or log the original URL to
diagnose a blocked, timeout, binary, or oversized response. Reproduce with a
non-secret controlled URL when necessary.

### Shutdown does not complete

Stop new ingress, retain process diagnostics, and allow the configured review
timeout. Escalate before forcing termination. After restart, run recovery tests
against the same database and conversation data.

## Monitoring and alerting

Monitor readiness, request error code counts, review duration/timeouts,
session-busy frequency, URL failure categories, SQLite disk usage, backup age,
restore rehearsal age, shutdown duration, and unexpected process restarts.

Alerts must identify the component and stable code without learner text or
secret values. Assign an owner and response objective before private staging.

## Incident response

For suspected secret or learner-data exposure: disable ingress, preserve
redacted evidence, rotate affected credentials, restrict backups/logs, identify
the disclosure path, and notify the AI0 release owner. Do not reopen staging
until the cause and containment are verified.

## Scheduled maintenance

Regularly verify backups, rehearse restore, monitor disk growth, review
dependencies, rerun non-real-LLM gates, review URL policy tests, and audit
service-account permissions. A successful maintenance run does not remove the
public-release identity blocker.
