# AI5.7 Task 2 Implementation Report

## Scope and baseline

- Authoritative repository: `/home/holy/web3/focusproof-agent`
- Branch: `agent/monad-evidence-plugin`
- Frozen baseline HEAD: `9a79998f6f62853d8dc000969ceb8a6f43040ba6`
- Implemented only Task 2, “Clamd Adapter and Resource Control Migration.”
- The pre-existing dirty tree, including accepted Task 1 work, was preserved. No reset,
  checkout, revert, stage, commit, push, merge, or amend was performed.

## Files changed for Task 2

- `agent-server/focusproof/media_adapters/clamd_malware_scanner.py`
- `agent-server/focusproof/media_adapters/clamd_limits.py`
- `agent-server/focusproof/media_core/ports.py`
- `agent-server/tests/media_adapters/test_clamd_malware_scanner.py`
- `docs/research/AI5_7_TASK2_IMPLEMENTATION_REPORT.md`

`media_core/limits.py` required no production change: the scanner-specific size,
concurrency, admission, deadline, socket, and definitions metadata are now owned by the
new adapter-local `ClamdLimits`; the existing product quota constants remain independent.

## RED / GREEN evidence

### RED

1. The first Task 2 specialty run failed during collection with
   `ModuleNotFoundError: focusproof.media_adapters.clamd_limits`. This proved the frozen
   limits/freshness contract was absent.
2. After the minimal contract was introduced, the specialty run failed because the legacy
   `MalwareScanStatus` rejected `oversize`, and old adapter assertions still expected
   migration-only `unknown` for protocol and source-integrity failures.
3. A subsequent run exposed that socket operations still referenced the removed legacy
   timeout attribute. This drove the independent `socket_timeout_ms` enforcement through
   connection, send, and receive boundaries.

### GREEN

- Final Task 2 specialty suite: `27 passed in 1.66s`.
- The tests exercise the actual adapter against controlled TCP and Unix socket daemons;
  they parse real `zINSTREAM` framing and daemon responses rather than mocking the scanner.
- Covered clean PNG-like bytes, EICAR signature response, oversize pre-admission rejection,
  total deadline timeout, refused/unavailable daemon, malformed/daemon error response,
  definitions freshness, resource-bound snapshots, concurrency limits, socket closure,
  capacity reuse, cancellation propagation, and cleanup after failures.

## Migration rather than a second scanner

- The existing `ClamdMalwareScanner`, endpoint validation, TCP/Unix socket connection,
  `zINSTREAM` chunking, SHA-256 streaming integrity check, bounded semaphore, total deadline,
  and strict NUL-framed response parser were retained and migrated in place.
- No CLI, managed, fake-clean, or fallback production path was added.
- `ClamdLimits.from_legacy_seconds` keeps current callers operational while translating their
  existing knobs into one immutable millisecond/resource snapshot. New callers can supply
  the frozen `ClamdLimits` directly.
- The adapter emits no live `unknown`: local oversize becomes `oversize`; deadline paths
  become `timeout`; connection refusal becomes `unavailable`; malformed/protocol/source
  failures become `error`; signature hits become `malicious`; only explicit OK is `clean`.

## Task 1 contract consumption

- `ClamdScanResult` directly uses Task 1 `ScanResultKind` and `ScanRejectionCode`.
- Every result carries the legal frozen result/code pair plus `definitions_version`,
  `definitions_fresh_at`, calculated non-negative `definitions_age_seconds`, `max_bytes`,
  `max_concurrent_scans`, `deadline_ms`, and `socket_timeout_ms`.
- The legacy base verdict remains available for existing non-production/migration callers,
  but the live clamd adapter returns the exact Task 1 result enum and never returns unknown.

## OpenHands SDK reuse

Task 2 does not create or imitate Runtime, Conversation, EventLog, Action, Observation, Tool,
or any conversation/event protocol. It makes no SDK source changes. Existing official
OpenHands SDK 1.31.0 types remain the only runtime/event/tool types; this adapter is strictly
the pre-existing malware scanner boundary.

## Verification

- Task 2 specialty: `27 passed in 1.66s`.
- Task 1 contract/persistence regression: `86 passed in 6.48s`.
- Related media adapter/core/persistence regression: `419 passed, 5 skipped in 56.48s`.
  The skips are the explicitly marked external PostgreSQL service cases.
- Full Ruff: passed.
- Strict mypy: `115 source files` passed.
- Final `git diff --check`: passed.
- Staged state: empty.

## Residual risks and boundaries not touched

- Definitions metadata is injected as an immutable trusted composition input. Live retrieval
  and certification against a deployed clamd database belongs to Task 6's real daemon gate;
  this report makes no real-clamd certification claim.
- Legacy constructor callers receive an explicit `legacy-unverified` definitions version;
  Task 3 composition must provide verified values before production persistence.
- No ingestion/profile composition, persistence implementation, quarantine/decoder, safe-fact
  projection, frontend, contracts, Monad, scoring, Manager, Factory, Synchronizer,
  ResultExtractor, agent loop, deployment wiring, or Task 3–6 behavior was modified.
- No secret or `.env` file was read or modified.

## Fix Round 1: cancellable connection lifetime

### Review finding and root cause

The original migration retained a per-scan daemon `connect_worker`. The main scan waited only
for its local timeout, set an `abandoned` event, and returned while `_connect_direct` could
remain blocked. The worker retained its stack and any socket/FD until the operating-system
connect returned. Repeated timeouts therefore accumulated workers and descriptors despite the
main scan meeting its latency deadline.

### RED evidence

A controlled connector created a real socketpair and then blocked before returning it:

- One connect timeout returned with one `connect_worker` still alive.
- Twelve repeated timeouts returned with twelve `connect_worker` threads alive.
- On Linux, `/proc/self/fd` showed the corresponding live socketpair descriptors at return.
- The cleanup fixture proved the old worker closed a late socket only after it was released,
  not before `scan()` returned.
- The cancellation check retained daemon socket drain and immediate semaphore reuse coverage;
  a fixture-owned `_handle` thread was excluded from scanner-worker assertions.

### GREEN implementation

- Removed the daemon worker and `socket.create_connection` path entirely.
- TCP and Unix endpoints now create their socket in the scan's calling thread, set it
  non-blocking, and use `connect_ex`.
- `EINPROGRESS`, `EWOULDBLOCK`, `EALREADY`, and `EINTR` wait through
  `selectors.DefaultSelector` for write readiness, then require zero `SO_ERROR`.
- Connect, send, and receive all use the same monotonic total deadline. Per-operation socket
  timeout is always the minimum of `socket_timeout_ms` and the remaining total budget.
- Every unsuccessful candidate socket closes immediately in its timeout/error path. No join,
  replacement daemon thread, or thread pool exists; a late connection after return is no
  longer possible because connection progress is owned by the calling thread.

### Cleanup and regression evidence

- Controlled selector timeout: two consecutive scans returned timeout, each created socket was
  closed, and the one-slot semaphore was immediately reusable.
- Legacy blocking connector injection was never invoked after migration.
- Repeated timeout test: no new scanner worker threads and no Linux FD growth at return.
- Cancellation: daemon connection drained, no connect worker remained, and a following clean
  scan reused capacity.
- Task 2 specialty: `31 passed in 1.87s`.
- Task 1 contract/persistence regression: `86 passed in 6.49s`.
- Related media adapter/core/persistence regression: `423 passed, 5 skipped in 53.37s`.
- Full Ruff: passed.
- Strict mypy: `115 source files` passed.

No Task 3 ingestion/composition, persistence, safe-fact, frontend, contracts, Monad, agent
loop, or OpenHands SDK source was changed in this fix round.

## Fix Round 2: startup DNS resolution and non-empty socket lifecycle tests

### RED evidence

The non-blocking connect migration still called `socket.getaddrinfo` inside `_connect`, after
the scan deadline was created. A controlled resolver slept for 200 ms and then failed:

- A scanner with a 50 ms total deadline spent about 200 ms in scan and returned `error`.
- The resolver call counter proved scanner initialization performed zero resolutions and the
  scan path performed the call.
- An initialization-resolution-failure test expected one startup call, but observed zero;
  resolution was deferred until scan.

Independent review also showed two worker/FD tests patched the removed
`socket.create_connection` API. Their socket lists stayed empty, so cleanup loops passed
without exercising the production `socket.socket/connect_ex/selector/SO_ERROR` path.

### GREEN implementation

- Every TCP endpoint, including IP literals and compose service hostnames, is resolved once in
  `ClamdMalwareScanner.__init__`; the resulting sockaddr candidates are cached as an immutable
  tuple. Unix endpoints cache their filesystem sockaddr without DNS.
- Startup `getaddrinfo` failure or an empty candidate list is cached as a sanitized unavailable
  configuration state. Scan then returns `unavailable + daemon_unavailable` immediately and
  never attempts fake-clean or a fallback resolver.
- `_connect` consumes only cached candidates. No scan-path DNS, resolver thread, thread pool,
  daemon thread, retry resolver, or CLI/managed fallback exists.
- Endpoint refresh is intentionally performed by explicitly rebuilding the scanner at the
  controlled composition/startup boundary; scans never mutate or refresh endpoint state.

### Socket, selector, SO_ERROR, and cleanup proof

The two empty tests were replaced with a socket factory that creates a real Linux socket FD
for every production `socket.socket` call and wraps only the boundary methods needed to drive
the actual adapter path:

- Single pending connect: exactly one socket created through `connect_ex`, selector timeout,
  socket closed, no new thread.
- Repeated pending connect: exactly 13 sockets created (12 repetitions plus immediate 13th
  semaphore reuse), all 13 closed, thread count unchanged, and `/proc/self/fd` returned to the
  baseline when available.
- Ready selector plus nonzero `SO_ERROR`: returns unavailable and closes exactly one socket.
- Initialization-complete DNS guard: replacing `getaddrinfo` with a slow failing function did
  not call it during scan; the live controlled daemon scan stayed clean and below 100 ms.
- Startup resolution failure: exactly one initialization call; subsequent scan performed no
  DNS and returned unavailable below 50 ms.
- Existing cancellation coverage still proves daemon socket drain, no scanner worker, and
  immediate capacity reuse through a following clean scan.

### Fresh verification

- Task 2 specialty: `34 passed in 1.75s`.
- Task 1 contract/persistence regression: `86 passed in 6.47s`.
- Related media adapter/core/persistence regression: `426 passed, 5 skipped in 53.91s`.
- Full Ruff: passed.
- Strict mypy: `115 source files` passed.

No Task 3 ingestion/composition, persistence, safe-fact, frontend, contracts, Monad, agent
loop, or OpenHands SDK source was changed in this fix round.
