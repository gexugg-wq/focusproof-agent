# Plan Review Log: FocusProof Voice-to-Text Evidence Input V1
Act 1 (grill) complete - plan locked with the user. MAX_ROUNDS=5.

## Review attempt 1 - infrastructure failure

- Reviewer: CLI default model (config unpinned), codex-cli 0.150.0-alpha.8
- Sandbox: read-only
- Thread: `01a04c6b-5c45-7a33-b55e-2e24fb04f626`
- Result: no verdict; the request timed out while reconnecting and the turn was interrupted.
- Resolution: stopped without retrying or treating this as a review round. No product code was modified.

## Round 1 - Codex

I could not perform a defensible review: the runtime denied read access to both the WSL workspace and `PLAN.md`. No files were modified. Restore workspace read access or paste `PLAN.md`, then rerun the review; approval is impossible without inspecting the plan and repository contracts.

VERDICT: REVISE

### Codex's response

The critique is valid as an infrastructure finding but contains no product-plan defect. `PLAN.md` remains unchanged. A read-only Windows review snapshot of tracked HEAD plus the locked plan will be supplied to the same reviewer session; implementation remains bound to the WSL repository.


## Round 2 - Codex

The Windows snapshot resolves the prior access blocker. The plan still has material defects:

1. **Cancellation is not real server-side cancellation.** `AbortController` only aborts the browser/BFF request; ASGI disconnect does not reliably cancel an already-running scanner/provider call, so billed work and temporary audio can survive “Cancel.”
   Fix: define an explicit cancellation protocol or a tracked server task with disconnect polling, provider cancellation behavior, terminal ledger transition, and deterministic cleanup ownership.

2. **Crash recovery is missing.** A worker crash can leave the unique in-flight row active forever and may leave named temporary files behind; the proposed state has no lease, expiry, owner token, or startup sweeper despite existing receipt code using that pattern.
   Fix: model in-flight work as an expiring lease with owner/version fencing, atomic reclaim rules, startup/periodic sweeping, and a dedicated ephemeral directory janitor.

3. **Provider idempotency is overstated.** A provider may accept and bill a request before the worker times out or crashes prior to recording completion; the automatic retry can then invoke it twice because the local ledger cannot determine the first call’s outcome.
   Fix: use a provider-supported idempotency/request key if documented, or prohibit automatic retry after ambiguous dispatch and explicitly document at-most-once local admission but potentially duplicate upstream billing.

4. **Rolling limits are not atomically specified.** A `COUNT` followed by `INSERT` allows concurrent workers to exceed both the 30-per-user window and 20-per-session limit; a unique in-flight index only solves the one-session slot.
   Fix: specify the PostgreSQL locking primitive—counter buckets, advisory locks, or serializable retry—and include a concurrent boundary test proving exactly N admissions across independent workers.

5. **The in-flight state machine is underspecified.** There are no exact states, legal transitions, retryability matrix, attempt-cap value, lease fields, check constraints, or behavior for a duplicate request while the first request is running.
   Fix: add a transition table and concrete schema constraints covering `admitted/running/succeeded/failed/cancelled/expired`, active-slot projection, attempt limits, replay/conflict/in-progress responses, and fencing.

6. **Quota charging occurs too late to prevent abuse.** Fingerprinting, upload, malware scanning, and format parsing can consume substantial resources before the durable provider-admission ledger is useful; authenticated users can repeatedly send malformed or malicious files without consuming the stated quota.
   Fix: add an atomic pre-scan request/byte rate limit and global scan/ASR concurrency admission, then separately define which terminal outcomes consume the provider quota.

7. **Global provider concurrency is absent.** Per-user and per-session limits still permit many users to exhaust sockets, memory, Clamd capacity, or DashScope quota simultaneously across workers.
   Fix: define bounded global scan and ASR admission with acquisition deadlines and multi-worker enforcement, plus overload metrics and a stable retryable response.

8. **Reusing `build_malware_scanner()` can duplicate the configured concurrency budget.** A second scanner instance has its own process-local semaphore, allowing image and audio paths collectively to exceed `max_concurrent_scans`.
   Fix: compose and share one scanner/admission controller across media consumers, or define an aggregate concurrency budget explicitly.

9. **The timeout budget is internally inconsistent.** The 120-second deadline covers only provider admission/retry/parsing, while the BFF timeout is described as slightly above 120 seconds even though upload, scanning, and inspection occur first; the BFF can therefore abort before the backend classifies timeout.
   Fix: define one end-to-end monotonic deadline and allocate explicit upload, scan, inspection, queue, provider, retry, cleanup, and BFF margins within it.

10. **Multipart authentication ordering is assumed, not proven.** FastAPI/Starlette may parse and spool multipart fields before endpoint dependencies finish, contradicting “verify ownership before reading beyond the bounded upload.”
    Fix: prove ordering with an unauthorized oversized-upload test or perform authentication/ownership in middleware before multipart parsing while retaining the route-aware streaming body limiter.

11. **The BFF currently corrupts this route unless its streaming classification changes.** Only `/evidence/image` is treated as media; every other POST is read through `request.text()`, which is unsuitable for audio multipart and buffers it.
    Fix: explicitly extend the media-route predicate, timeout resolver, allowlist, content-length and streamed-byte enforcement, and tests for chunked bodies without `Content-Length`.

12. **The format inspectors are an unbounded parser attack surface.** Hand-rolled WebM/EBML, MP3 VBR, and RIFF/WAV duration parsing has integer, malformed-chunk, indefinite-size, and CPU-exhaustion edge cases; “narrowly scoped inspectors” is not a security design.
    Fix: name a maintained parser, impose byte/chunk/depth/time limits in an isolated worker, and add adversarial corpus/fuzz tests for truncated and pathological containers.

13. **Provider response bounding happens too late unless streaming is specified.** Calling `.json()` or `.text()` can allocate an arbitrary upstream body before the proposed UTF-8 bound is checked.
    Fix: stream the upstream response with a strict byte ceiling before decoding or JSON parsing and reject oversized/decompression-amplified responses.

14. **The provider contract is not frozen precisely enough.** The plan does not identify the exact endpoint, message/audio representation, response text path, language-hint field, or regional base URL contract. Official documentation says `qwen3-asr-flash` accepts URL/Base64/local-path-style input and returns emotion annotations by design, which cannot simply be assumed absent. [Alibaba Cloud ASR documentation](https://help.aliyun.com/zh/model-studio/asr-model/)
    Fix: pin the documented Beijing endpoint and exact request/response fixtures, verify whether `zh/en` hints are supported, and explicitly discard—not log or persist—all annotations and non-transcript fields.

15. **“Transcription only” conflicts with unavoidable provider metadata.** Official documentation indicates Qwen3-ASR-Flash has emotion recognition enabled and returns annotations; the privacy boundary does not say how those sensitive derived attributes are eliminated.
    Fix: parse only the bounded transcript field into a minimal DTO, discard the complete provider object immediately, disable HTTP body logging, and test that emotion/language annotations never reach logs, metrics, exceptions, or persistence.

16. **The privacy UX is inadequate.** A browser microphone permission prompt authorizes capture, not disclosure that audio is uploaded to a third-party cloud service in a configured region; “no explanatory copy” is not informed consent.
    Fix: add concise just-in-time disclosure covering cloud transcription, region/provider, ephemeral local handling, and cancellation limitations, with an explicit user action before upload.

17. **The persistence schema is too vague for a migration review.** The plan omits column types/lengths, foreign keys, cascade behavior, indices supporting window queries, check constraints, timestamp source, retention/deletion policy, and whether session deletion destroys audit metadata.
    Fix: include the concrete table DDL/model specification and migration data/rollback semantics, using database timestamps and indexed `(owner, admitted_at)` and `(session, admitted_at)` access paths.

18. **Hashed idempotency keys need a defined threat model.** Plain hashing does not protect low-entropy client keys from dictionary recovery and prevents neither cross-environment correlation nor accidental reuse.
    Fix: validate high-entropy UUID keys and store a server-keyed HMAC with key-version rotation, or state why the identifier is non-sensitive and store it consistently with existing conventions.

19. **The no-transcript-persistence contract leaves success commit ordering unresolved.** Returning text before durably marking success permits a later retry; marking success before sending text can permanently produce `result_unavailable` even when serialization fails locally.
    Fix: specify the exact provider-result → durable-success → bounded-response ordering and accept/document the unavoidable failure window with tests at every injected crash point.

20. **Shutdown behavior is aspirational.** FastAPI lifespan cannot await or cancel admitted requests unless the service maintains a task registry and stops admission before teardown.
    Fix: define a lifecycle-owned task registry, shutdown admission gate, bounded drain deadline, cancellation/fencing behavior, provider-client close order, and cleanup assertions.

21. **Frontend state conflicts with the existing composer are unresolved.** The current controlled textarea has no selection ref, and image/text submission has its own `busy` state; microphone clicks can lose cursor position while concurrent submission, session navigation, or late completion mutates the same text.
    Fix: define one reducer/state machine for composer submission and recording, capture/restore selection explicitly, fence results by operation/session ID, and specify which controls are disabled in every state.

22. **“Keep the Blob after retryable failure” is incompatible with unmount/page close.** Component memory cannot survive refresh, tab crash, route remount, or browser eviction, so the recovery promise is narrower than the wording suggests.
    Fix: state that retry is same-mount only and test remount loss, or obtain separate approval for encrypted browser persistence.

23. **Capability typing and disabled-state behavior are incomplete.** The frontend currently types `productCapabilities` as `unknown[]`, while image capability assumes `enabled: true`; the plan does not define unavailable versus disabled metadata or version negotiation.
    Fix: add a discriminated, versioned speech capability contract with enabled and disabled variants, reason codes safe for clients, and backward-compatible parsing tests.

24. **Observability cannot diagnose abandoned work safely.** Metadata fields alone do not define counters for admission denial, lease expiry, ambiguous dispatch, cleanup failure, cancellation outcome, provider latency, or queue saturation.
    Fix: specify low-cardinality metrics and structured redacted events for every transition, including orphan recovery and cleanup failure, without transcript/audio/provider-body labels.

25. **The test plan misses the hardest failure injection.** Ordinary cancellation and cleanup tests will not prove correctness across crashes between provider dispatch, response receipt, ledger commit, and HTTP response delivery.
    Fix: add deterministic kill/crash-point tests for every boundary, multi-process PostgreSQL admission tests, chunked-upload disconnect tests, and restart recovery assertions.

26. **A simpler V1 is available.** Durable replay semantics, retries, leases, rolling windows, and cancellation form a small job system despite the feature being synchronous and non-replayable.
    Fix: simplify V1 to one atomic active lease, no automatic provider retry after dispatch, strict global admission, and explicit new-attempt recovery; add richer retry accounting only after provider idempotency is proven.

VERDICT: REVISE

### AI0's response

Accepted and incorporated: explicit lease/fencing state machine; crash recovery and temp janitor; local at-most-once semantics; atomic PostgreSQL admission and resource slots; pre-scan quota charging; shared lifespan-owned MalwareScanner; a single 120-second end-to-end deadline; auth before multipart consumption; BFF streaming; isolated maintained mediainfo inspection; bounded provider response parsing; minimal transcript-only DTO; exact schema/indexes; HMAC idempotency; durable-success-before-response ordering; shutdown registry/drain; reducer operation fencing; versioned capability variants; bounded observability; crash/multi-process/chunked-disconnect tests; and the simpler no-retry-after-dispatch V1.

Partially accepted: the previously agreed one automatic retry remains only for failures proven before provider dispatch and before any request bytes are accepted. It is forbidden for ambiguous dispatch. The optional language hint remains in FocusProof's stable API but is forwarded only if the pinned official provider contract supports it.

Rejected as a component requirement: an additional privacy modal/copy. The user explicitly rejected it during Act 1. Native permission remains the UI behavior; cloud-processing disclosure is made an external deployment policy/terms prerequisite. This does not weaken the prohibition on persisting audio/transcripts.

The revised plan also names the maintained inspector (mediainfo/libmediainfo), fixes the BFF timeout at 130 seconds over a 120-second backend monotonic budget, and makes PostgreSQL mandatory for production multi-worker admission.


## Round 3 - Codex

The revision addresses most prior findings, but these material defects remain:

1. **Ownership still cannot run before multipart parsing as designed.** FastAPI parses `UploadFile` form bodies before ordinary dependencies execute, so a “pre-multipart admission dependency” cannot guarantee that unauthorized session uploads consume no multipart bytes.
   Fix: perform session ownership and ledger admission in ASGI middleware or a custom `APIRoute` before invoking FastAPI’s request handler, then prove zero downstream `receive()` calls for rejected requests.

2. **The deadline excludes atomic admission.** It begins “after durable admission,” so PostgreSQL advisory-lock waits and SQLite `BEGIN IMMEDIATE` contention are unbounded and can make the 130-second BFF timeout win.
   Fix: start the monotonic deadline at request entry and apply its remaining budget to ownership lookup, advisory-lock acquisition, quota counting, admission, and every later phase.

3. **Advisory-lock ordering is unspecified.** Transactions locking both owner and session can deadlock unless every code path acquires them in one canonical order.
   Fix: mandate owner lock first and session lock second, use bounded lock/statement timeouts, and test cross-session requests for the same owner under contention.

4. **Shared scanner concurrency is only process-local and excludes image admission.** One lifespan scanner is shared only within one worker; speech claims database scan slots, but the existing image path does not, so image plus speech across workers can exceed the advertised global scan limit.
   Fix: make both image and speech acquire the same generation-fenced database scan slots, or stop claiming the limit is global across media paths.

5. **Slot reconciliation can silently increase concurrency after configuration shrink.** Existing higher-numbered slot rows remain claimable unless reconciliation disables or deletes them safely.
   Fix: add an enabled/config-generation field and atomically retire surplus free slots while allowing occupied retired slots only to drain.

6. **Provider “zero request bytes” classification lacks an implementable mechanism.** Persisting `provider_dispatched_at` before writing the request makes DNS/connect/TLS failures look dispatched, while normal HTTP clients do not expose a trustworthy request-body-byte boundary for retry classification.
   Fix: either eliminate automatic provider retries entirely for V1 or specify an instrumented transport with separate `dispatch_intent` and `request_bytes_started` durable states and tests at each boundary.

7. **Idempotency breaks on HMAC-key rotation or accidental key replacement.** The schema has no HMAC key version, so the same client key hashes differently after rotation and can create another billable request.
   Fix: persist `hmac_key_version`, compare hashes under active and retained previous keys, and fail readiness when required historical keys are unavailable.

8. **The database state invariants remain mostly application-only.** The plan lists fields and CAS transitions but does not require check constraints for state, lease nullability, dispatch timestamps, outcome payloads, slot occupancy, or a foreign key from slots to requests.
   Fix: add an explicit state/payload constraint matrix, slot occupancy constraints, request foreign key behavior, and migration tests that invalid combinations fail at the database layer.

9. **The claimed `mediainfo` isolation does not enforce “no network” or filesystem confinement.** A subprocess timeout and output ceiling bound hangs/output but do not contain a parser compromise running as the application user.
   Fix: run inspection under a least-privilege sandbox/container profile with filesystem and network restrictions plus memory/CPU/file-descriptor limits, or narrow the security claim accordingly.

10. **Independent composer submission races with transcription completion.** Existing text/image submission clears the same controlled textarea; a transcript arriving during or after submission can be inserted into, or erased from, the wrong composer revision despite matching operation/session generation.
    Fix: include a composer revision in the result fence and either block submission while speech is active or define an atomic merge/preservation rule tested for concurrent text and image submission.

11. **The language-hint capability is misleading when the adapter may ignore it.** Advertising `zh|en` support while intentionally sending no unsupported provider parameter gives clients no observable semantic guarantee.
    Fix: expose only `auto` unless the pinned provider contract supports language hints, or explicitly define hints as accepted-but-ignored metadata rather than a capability.

12. **Tests do not cover the remaining cross-boundary guarantees.** The matrix omits mixed image/speech scan-slot contention, configuration shrink, HMAC rotation, canonical advisory-lock ordering, and concurrent composer submission.
    Fix: add those cases, including independent PostgreSQL processes and crash injection during slot retirement and key rotation.

VERDICT: REVISE

### AI0's response

Accepted all twelve material findings. The plan now mandates an ASGI/custom-route admission gate before multipart parsing; starts the 120-second deadline at request entry; fixes advisory-lock order to owner then session; places image and speech behind the same database scan slots; defines safe slot retirement; removes every automatic provider retry; versions HMAC keys and fails readiness when historical key material is missing; adds database state/slot constraint matrices; requires production bubblewrap confinement for mediainfo; fences transcript insertion with composer revision and blocks submit while speech is active; labels zh/en hints as accepted metadata with no V1 semantic effect; and adds every named cross-boundary test.

The removal of the previously discussed automatic retry is a deliberate safety simplification. The user still gets explicit same-mount recovery, but every new provider attempt requires a new user action and key.


## Round 4 - Codex

Four material blockers remain:

1. **The shared scan-slot schema cannot represent image owners.** `speech_resource_slots.request_id` is a foreign key to `speech_transcription_requests`, but the existing image path must claim the same slots and has only media reservation IDs.
   Fix: make slots resource-neutral with an opaque `lease_owner_token` plus `work_kind/work_id`, or introduce a shared work-admission table referenced by both image and speech.

2. **`ON DELETE SET NULL` conflicts with the slot check constraints.** Deleting a referenced request nulls `request_id` but leaves `lease_expires_at` and generation populated, violating the stated empty-slot invariant.
   Fix: use `ON DELETE RESTRICT` and release slots before request deletion, or implement an atomic database mechanism that clears all occupancy fields together.

3. **The state machine cannot represent definitive failures after dispatch.** Blank transcripts, bounded-response violations, HTTP authorization/rate-limit responses, and other known provider failures occur after `provider_dispatched_at`, but only `succeeded` and `ambiguous` are defined from `dispatching`; `failed_terminal` is modeled as pre-dispatch.
   Fix: add an explicit `dispatching -> failed_terminal` transition for definitive provider responses and update the database payload matrix to distinguish known post-dispatch failure from transport ambiguity.

4. **Cleanup cannot consume an already-expired request deadline.** If provider work exhausts the full 120 seconds, placing deletion and process termination under the same deadline leaves no budget for privacy-critical cleanup.
   Fix: reserve cleanup time within the 120-second budget or run shielded cleanup under a separate short hard deadline before releasing the task, while keeping the client-visible operation deadline unchanged.

VERDICT: REVISE

### AI0's response

Accepted all four blockers. Resource slots are now modality-neutral opaque leases rather than speech foreign keys; release atomically clears every occupancy field; the request state machine distinguishes definitive post-dispatch failure from transport ambiguity; and the 120-second budget reserves seconds 115-120 for shielded privacy cleanup. Related constraint and observability language was updated.


## Round 5 - Codex

No material implementation blockers remain. The four latest issues are resolved consistently: resource-neutral shared slots, valid slot cleanup semantics, definitive post-dispatch failure states, and a reserved shielded cleanup window. The plan is sound enough to implement against the repository snapshot.

VERDICT: APPROVED

### Resolution

APPROVED after 5 counted review rounds. The final specification is implementation-ready.
