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

AI5 backup and restore require three explicit directory arguments. Resolve each
path and use only this layout:

- coordination: `FOCUSPROOF_DATA_DIR`, containing recovery markers and locks;
- OpenHands payload: `FOCUSPROOF_DATA_DIR/conversations`;
- media payload: `FOCUSPROOF_DATA_DIR/media/objects`.

The two payload roots must not be equal or nested. The coordination root is
their intentional common parent, but is never archived, extracted, renamed, or
swapped as a whole. Backup and restore reject the legacy single-directory
contract and any symlink, escaped path, or alternate relative layout.

AI5 recovery uses one manifest-v2 unit containing `database.dump`,
`openhands.tar.gz`, and `media.tar.gz`. The manifest records the canonical
payload-relative paths, independent SHA-256 values, and tree versions. Restore
also requires an explicit recovery-admin PostgreSQL URL; it is never inferred
from `.env`. The business URL names only the target database. The admin URL must
address the same host, effective port, and TLS settings, but a different cluster
maintenance database. It is used only to create, rename, terminate connections
to, and remove the randomized recovery databases.

Restore validates the exact bundle, safe archive members and resource limits,
all digests, the shadow database media object-key/hash rows, media bytes, and
official OpenHands persisted image references before changing live state. It
then switches the database and the two payload roots independently under the
coordination maintenance marker, post-verifies all three sources, and rolls
back media, OpenHands, then database on failure. Never restore only one member.
Manifest v1 has no media snapshot and is rejected; migrate it by restoring with
the pre-AI5 release and taking a new v2 backup.

The `staging_external` recovery marker test creates a temporary PostgreSQL
cluster, backs up an image session across database/EventLog/media, destroys its
temporary sources, restores them, reruns image review, and compares media bytes
and hashes. It is honestly deselected when container/PostgreSQL capabilities are
unavailable; a deselection is not recovery evidence and must not be reported as
a successful drill.

Historical note: the earlier `BLOCKED_BY_OFFICIAL_SDK_GATE` visual conclusion
is superseded by the real `openai/qwen3.7-plus` acceptance recorded in
[AI5_IMAGE_GATE_REPORT](../research/AI5_IMAGE_GATE_REPORT.md). The historical
guarded skip remains context only and must not be treated as current status.

## AI5.3 malware admission

Set FOCUSPROOF_MEDIA_SCANNER_MODE=clamd and an explicit Unix or TCP
FOCUSPROOF_CLAMD_ENDPOINT. The configured scan capacity must be at least
10485760 bytes. Staging and production fail composition when clamd settings
are missing, disabled, fake, or invalid.

Staging compose requires the external endpoint through
FOCUSPROOF_STAGING_CLAMD_ENDPOINT and passes it as FOCUSPROOF_CLAMD_ENDPOINT.
The endpoint must be reachable only on the private deployment network. Before
starting agent-server, operators must independently verify daemon health.

Before enabling upload, run the guarded test with
FOCUSPROOF_REAL_CLAMD_TEST_ENABLED=true and the same explicit endpoint. The gate
creates temporary clean and standard EICAR probes, requires clean and malicious
verdicts respectively, and deletes both probes in finally cleanup. A skip is
BLOCKED_REAL_CLAMD, not successful staging evidence. Deterministic code-gate
success and real clamd/EICAR gate success are separate release decisions.

Public scan failures are limited to media_malicious (422, not retryable) and
media_scan_unavailable (503, retryable). Intentional local/test disabling
returns media_disabled without entering quarantine, decode, stage, or finalize.
Roll back by disabling public media upload; never replace clamd with a fake or
an unscanned path in staging/production.

## Tiered Quality Gate

Run `scripts/run_quality_gate.py` from WSL before promoting staging evidence.
Use `--list` to inspect resolved steps and `--dry-run` to print commands without
executing them.

```bash
cd /home/holy/web3/focusproof-agent
.venv/bin/python scripts/run_quality_gate.py --tier fast
.venv/bin/python scripts/run_quality_gate.py --tier integration
.venv/bin/python scripts/run_quality_gate.py --tier release --allow-real-provider
```

`fast` is deterministic and must not require Docker, network, PostgreSQL, or
real LLM/provider credentials. `integration` inherits fast and adds PostgreSQL,
backup/restore, deterministic frontend/backend E2E, and Clamd integration while
still avoiding real LLM calls. `release` inherits integration and is blocked
unless `--allow-real-provider` is present; it requires the configured live Clamd
endpoint and real Qwen/OpenHands provider environment before producing the final
dual-mode manifest. Cost profile: fast has no external cost, integration spends
local infrastructure time, and release can spend provider quota plus live daemon
capacity.
