# FocusProof AI4B Threat Model

## Scope and release position

This model covers the general knowledge-learning verification flow implemented by
the FastAPI Agent Server, SQLite persistence, the Next.js same-origin BFF, and the
official OpenHands SDK `Agent`, `LocalConversation`, native `EventLog`,
`ActionEvent`, and `ObservationEvent` path.

The current development identity (`dev-anonymous-user`) is not production
authentication. Production authentication is not implemented or complete.
Therefore public deployment is blocked until AI0 approves an identity design,
authorization lifecycle, and operational ownership model. The controls below
support private development and staging evaluation; they do not remove that
blocker.

No wallet, transaction, contract, chain, or domain-specific Web3 dependency is
part of this security boundary.

## Security objectives and assets

The protected assets are:

- learner goals, evidence text, source URLs, answers, and review findings;
- session ownership and the ability to append or review session facts;
- native OpenHands events and their persistent FocusProof projections;
- authoritative `ReviewResult` records and scores;
- SQLite data, Alembic revision state, conversation data, and lock files;
- provider credentials supplied to a future private staging environment;
- service availability, bounded compute, and bounded outbound URL fetching;
- redacted operational logs and backup material.

The main security objectives are confidentiality of learner material and
secrets, integrity of authoritative runtime facts and reviews, owner isolation,
availability under bounded input and runtime failures, and honest release
claims.

## Trust boundaries

1. **Browser to Next.js BFF.** Browser input is untrusted. The BFF accepts only
   approved FocusProof paths and constructs upstream headers from a literal
   allowlist.
2. **BFF or test client to FastAPI.** All request bodies, route parameters, and
   identity context are untrusted until validated.
3. **FastAPI to persistence.** Unit-of-work and session-lock boundaries protect
   ownership, finalization, idempotency, and event ordering.
4. **FocusProof adapter to OpenHands SDK.** Only native SDK actions,
   observations, event storage, interrupts, and closes are authoritative.
5. **Verification tools to external URLs.** DNS, redirects, address classes,
   response sizes, content types, and deadlines cross an SSRF boundary.
6. **Service process to filesystem and secret manager.** Database,
   conversation data, locks, backups, and provider credentials require
   deployment-level access control.
7. **Reverse proxy to private services.** TLS termination, forwarded headers,
   request limits, and origin policy are deployment responsibilities.

## Actors and entry points

Actors include an honest learner, a malicious or compromised browser, another
tenant attempting enumeration, an attacker controlling submitted text or a URL,
an attacker controlling DNS or redirects, a compromised upstream content
server, an unreliable model/provider, and an operator with filesystem access.

Entry points include session creation and derived session endpoints, evidence
and answer submissions, review execution, event and review reads, `/health`,
the restricted BFF, URL verification, SQLite and conversation files, process
environment, logs, backups, and deployment scripts.

## Threat analysis and controls

| STRIDE area | Representative threat | Existing control | Residual risk |
| --- | --- | --- | --- |
| Spoofing | A caller claims another session or identity | Every session-derived endpoint checks the verified identity and returns a non-enumerating denial | Development identity is shared and blocks public release |
| Tampering | Learner text embeds fake actions, observations, tool success, or a review | Authoritative facts come only from native SDK events, registered tools, repositories, and persisted review extraction | A compromised process or database administrator remains trusted |
| Repudiation | Facts are changed without an ordered record | Native event IDs and ordered persistent projections are retained across restart; completed review facts are frozen | Logs are not yet exported to an independently administered immutable store |
| Information disclosure | Errors, URLs, evidence, environment values, or provider keys leak | Safe API errors, URL redaction, BFF header allowlist, secret scans, and smoke/check output restrictions | Operators can still expose data through unsafe external log configuration |
| Denial of service | Oversized bodies, metadata, URL bodies, slow streams, stuck reviews, or lock contention | Pydantic bounds, ASGI body ceiling, URL deadlines/size limits, review timeout, per-session lock, interrupt, and orderly shutdown | SQLite remains a single-node staging topology with bounded concurrency |
| Elevation of privilege | Browser headers or prompt text invoke privileged tools | BFF drops authorization/cookie/provider headers; default programming tools are disabled; FocusProof tools are read-only and schema-bound | A future production identity and role model still requires separate design |

## Required abuse cases

### Prompt injection and event/result forgery

Goals, evidence, answers, fetched pages, and model prose are data, not control
messages. Text that resembles JSON, an `ActionEvent`, an `ObservationEvent`, a
successful verifier, or a `ReviewResult` cannot directly append an authoritative
runtime fact. Tool success requires a registered tool call followed by a native
SDK observation. Completed reviews require the existing review-draft and
deterministic scoring path.

### SSRF and hostile URLs

URL evidence is repository-backed and accepts only an evidence ID at tool
execution. URL policy denies credentials, unsafe schemes, localhost, metadata
services, private/reserved addresses, unsafe DNS answers, unsafe redirects, and
excessive redirect chains. Connections are pinned to policy-validated addresses
and revalidated for every redirect. Deadlines, content limits, supported media
types, and redacted failure observations bound exposure.

### XSS and BFF boundary

Learner and runtime strings are rendered as React text. The frontend does not
use untrusted HTML. The BFF forwards only the required content type and does not
forward browser authorization, cookies, or provider keys. Upstream failures are
mapped to safe structured errors; failed submissions retain learner input.

### Replay, finalization, and concurrency

Evidence has deterministic logical identity, answers are versioned
idempotently, and completed review results are replayed without new native
events. Once reviewed, identical facts may be replayed, but new or changed facts
receive the stable non-retryable `session_finalized` response. A concurrent
identical answer may receive a retryable busy response; safe retry must not
create another version or native event.

### Resource limits and recovery

Request sizes, field lengths, metadata depth/items/bytes, URL response size,
redirect count, fetch deadline, review duration, and per-session execution are
bounded. Failures must not mark a session reviewed or emit a completed review.
Shutdown rejects new reviews, interrupts admitted work, waits for it to leave
the native run, closes handles, releases the provider registry, and disposes the
database engine.

### Secrets

Provider credentials must come from a deployment secret manager, never from
tracked files, browser headers, screenshots, smoke output, or command output.
The deterministic test server uses SDK `TestLLM` and does not read a real LLM
key. Default checks remove known provider-key variables from test subprocesses.

## Residual risks and release blockers

- **Identity blocker:** the development anonymous identity is unsuitable for
  public deployment. No production authentication provider has been selected.
- **Semantic association:** lexical English word overlap and Chinese character
  overlap are low-confidence heuristics. Until real Agent/LLM semantic
  evaluation is integrated with deterministic score boundaries, the system
  must not claim it reliably detects every detailed but semantically unrelated
  false-learning submission.
- **Model and content risk:** deterministic tests prove orchestration and
  boundaries, not the safety or correctness of every future provider response.
- **Single-node persistence:** SQLite backup, file permissions, disk capacity,
  and exclusive restore procedures remain operator responsibilities.
- **Operational observability:** local structured logs are not a substitute for
  centrally administered retention, alerting, and incident response.
- **Availability:** native interrupt and timeout behavior depend on cooperative
  SDK/provider cancellation; operators must monitor shutdown and timeout rates.

## Public release gate

Public release requires AI0 approval of production identity, private staging
evidence, secret-manager integration, reverse-proxy policy, backup/restore
exercise, monitoring ownership, and a documented rollback decision. Until then,
the only approved server in this deliverable is the loopback deterministic test
server.
