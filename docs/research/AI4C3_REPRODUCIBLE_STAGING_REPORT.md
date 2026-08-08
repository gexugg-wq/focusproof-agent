# AI4C.3 Reproducible Staging Phase Report

Date: 2026-07-29
Branch: `ai4c-production-readiness`
Task 6 baseline: `d17f82d8b85f465f5fd8bf04b1649955d5232c24`
Disposition: ready for AI0 Task 6 review

## Scope and release boundary

This report closes AI4C.3 Task 6 only. The product remains a general knowledge
learning-verification agent. No AI5/AI4C.4 work, real LLM, provider key, public
deployment, secret read, or Task 1-5 implementation change was performed.
FocusProof continues to use the official OpenHands SDK 1.31.0 directly for
`LocalConversation`, `EventLog`, native action/observation events, tools and
providers. Product PostgreSQL facts and official OpenHands native persistence
remain one paired recovery unit.

The starting checkout was clean at the required commit. All commands ran in WSL
Ubuntu from `/home/holy/web3/focusproof-agent`.

## Capability and official SDK gates

The credential-free capability preflight exited 0:

```sh
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY \
  -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python scripts/check_ai4c_capabilities.py \
  --require container_cli compose postgres_client
```

It reported container CLI, Compose and PostgreSQL client `available` on Linux
`x86_64`. Observed versions were Docker 29.1.3, Docker Compose 2.40.3,
PostgreSQL client 16.14, Python 3.12.3, Node 18.19.1 and npm 9.2.0.

The controlled official-release experiment also exited 0:

```sh
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY \
  -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python scripts/check_openhands_release_equivalence.py \
  --version 1.31.0 --timeout-seconds 300
```

Result: `PASS`, version `1.31.0`.

- signature digest: `f0dd4830554f256b605f565304d17221c7d2ad52fb33fa5afd6aa3823da48e3e`
- lifecycle digest: `ef16bc0b8164f579ae783b0d845c3947d539c285e426b532cf947b60993f5671`
- event digest: `2fa64b778094febdae107c90c68edd31b8f7c460d08b277418c8811848285c66`

The project environment independently reports `openhands-sdk 1.31.0`. No
custom wheel or local SDK path is present or conditionally retained.

## Locks, migrations and release identity

SHA-256 evidence:

- `requirements/production.lock`: `a23242e2c85d756a588324b909e3e52a388c60016ab51b8c40419e9120629376`
- `frontend/package-lock.json`: `7263329667e1be0623f94ec62cde9a38d82fc328fe167a70df53bdc3ae5ade0c`
- `deploy/agent-server.Dockerfile`: `a9008f2d83e635a56b9bd1ba6aa9add6e6b5c5769d62eec1bfb45c4dc882f562`
- `deploy/frontend.Dockerfile`: `eb4ca1f709d21ac4275118b19adbae37a8e5ecc2fbf32b9bdd9a920938822b63`
- `deploy/compose.staging.yml`: `52740faab53c686ad0e05e5a077586903ba784e10de032c137a440e688724d11`

There are three Alembic revisions
(`0001_initial_focusproof_schema`, `0002_verified_principals`,
`0003_security_audit_events`); the single head is
`0003_security_audit_events`.

The stack gate built twice from independent clean Git-index contexts. The
accepted canonical release identity, not random OCI image ID equality, matched:

| Image | Round 1 canonical | Round 2 canonical |
| --- | --- | --- |
| agent-server | `sha256:761d37cc8a5db7dc306675e478cb48cd641e94ec8913359606ef989efa5d2fe5` | same |
| frontend | `sha256:3f667ff29bff08bdc5ee16db045695ed853bbf4055be2e6ea1b6ab091caf5146` | same |

Backend OCI ID was
`sha256:4455fbdc6621fb3717a781ba8a4acb5e5f90967de8d460d1a15c7b3d20865746`
in both rounds. Frontend OCI IDs differed
(`sha256:7d81d970e4cab942e45c9849ba9ab2365338af71accda2ce6d817c4899a105d2`
and `sha256:ab152c624d99c5d776aeae4a0e5ef4665bcb9339d94c4555a516bf06b23c264b`);
they are diagnostic metadata and do not override the canonical decision.

## Gates and clean production flow

The complete default no-key gate passed:

- backend: 753 passed, 13 deselected, 16 warnings;
- Ruff: all checks passed;
- mypy: no issues in 158 source files;
- frontend ESLint and TypeScript: passed;
- frontend Vitest: 6 files and 76 tests passed;
- Next.js 15.5.18 production build: passed with four routes;
- `git diff --check`: passed.

After preflight, the explicit PostgreSQL suite passed 10 tests. Its accepted
fixture targets the dedicated local
`focusproof_ai4c_task3`/`focusproof_ai4c` database and role; a mode-0600
temporary pgpass was removed after the run.

The explicit stack gate passed once in 1582.14 seconds. Both clean rounds
completed the local OIDC Authorization Code + PKCE browser path through Next
BFF and FastAPI, then official OpenHands conversation restart/recovery:

- round 1 conversation `71f0afff-d5ba-5a8d-b127-26146dfba0b5`, review
  `rev_df13f5d4c52f49778bc835c724f2c166`;
- round 2 conversation `9d6afa6a-dbd5-5dd6-a654-78591d6c0748`, review
  `rev_c32da461a9c2491b88768bb25afc996e`;
- each round preserved product events `4 -> 7 -> 7`, native-source events
  `3 -> 4 -> 4`, and `reviewStatus=completed`.

The paired backup/restore external gate passed once in 14.43 seconds. It seeded
two owners and two sessions, evidence for each, an answer and one completed
review; destroyed the original disposable PostgreSQL volume and native data;
restored both stores; and repeated restore idempotently. The gate compares the
exact session, owner, conversation, evidence, question, answer/version, review,
native source event and native event IDs/types before restore, after the first
restore and after the second restore. Those random values are intentionally
captured by pytest and not printed; the successful equality assertions are the
secret-safe evidence, rather than invented IDs. Review and native-event counts
did not grow.

No FocusProof test containers or volumes remained after the external gates.
The pre-existing BuildKit builder and its state volume were retained.

## Monitoring, redaction, blockers and rollback

Operational tests in the default gate prove bounded request/review status and
latency, official conversation provider aggregate calls/tokens/cost, provider
admission rejection, authentication outcome, database/runtime health and
recovery outcome. Route templates are bounded; session IDs, credentials,
evidence text, answers and provider secrets are excluded. The paired external
gate additionally asserts its test database password and evidence sentinel are
absent from captured output, logs and manifest.

There is no release blocker. Residual risks are the documented single-host,
single-FastAPI-worker topology; local operator responsibility for external
secret files and retention; and deprecation warnings from Starlette/httpx,
SQLite adapters and Vite's CJS API. The Task 6 PostgreSQL setup rotated only the
dedicated local test role password because the fixture pgpass was absent; it did
not touch other roles or databases.

Rollback to the verified, accepted AI4C.2 release and use the paired recovery
unit produced by that same revision. Its code, images, Compose manifests and
migrations are one release unit and must move together; its PostgreSQL data and
OpenHands native persistence are one paired recovery unit and must be restored
together.

Before reopening writes, verify the restored canonical release digests and
migration state, then verify product and native event identities and counts,
review and ownership identities, and the established redaction guarantees. If
any release component, recovery member, digest, migration or restored identity
does not match, keep writes closed, stop the release and preserve the failed
deployment for investigation.
