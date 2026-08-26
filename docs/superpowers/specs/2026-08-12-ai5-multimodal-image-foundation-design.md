# AI5 Multimodal Image Foundation Design

Status: AI5.0 architecture revision v7. Production implementation remains frozen.

## Scope, quotas, and immutable history

AI5.1 delivers PNG, JPEG, and single-frame WebP learning evidence with a mandatory non-blank learner explanation. Each product Session accepts at most four image Evidence rows; each original upload is at most 10 MiB; committed distinct normalized image bytes total at most 20 MiB. The EventLog retains and restores those four official `ImageContent` entries. A fifth image is rejected by the first authoritative database quota transaction before quarantine creation. Supporting larger histories requires a separately approved history-media selection/cropping protocol; AI5.1 does not provide or assume it.

Any `ArtifactResolvingLLM` behavior is conditional on the Task5 official-public-extension-point gate. Audio/ASR/PDF retain extension boundaries only. SVG, GIF, HEIC, animated/multi-frame WebP, video, file paths, and remote media URLs reject. Text/URL behavior, native OpenHands Conversation/Agent.step/EventLog/Message/ImageContent/Tool/Action/Observation, six scoring dimensions and caps, Build Log, Domain Plugin, and Monad-disabled default remain unchanged.

## Architecture, ownership, and composition root

```text
SessionWorkspace <- AgentView.productCapabilities <- api/app.py::_view
browser multipart -> Next BFF stream -> ASGI route policy -> media route
media route -> MediaIngestionCommand -> synchronous MediaIngestionService
  -> quota lease -> QuarantineStore -> ImageValidator -> ImageNormalizer
  -> MediaObjectStore.stage -> final quota/UoW -> mark_referenced
api/app.py create_app/lifespan -> bootstrap/media_composition.py
  -> RuntimeContribution(capabilities, tool_definitions) -> Manager constructor
StoredEvidence -> Synchronizer -> RuntimeEvidenceMessageFactory -> stable ImageContent
factory.py -> DB-verified RuntimeLLMContext -> [conditional after Task5 public extension proof: ArtifactResolvingLLM -> official inner LLM]
Media Tool -> ScopedMediaEvidenceRepository -> MediaEvidenceFacts -> VerificationObservation
ResultExtractor -> LearningNarrativeProjector providers -> VerifiedLearningNarrative -> scoring
```

`api/app.py:create_app` and lifespan are the sole application composition root. `bootstrap/media_composition.py` is a conditional construction helper called only there; it is not a service locator. Media core/application imports no OpenHands, FastAPI, SQLAlchemy, Pillow, multipart, frontend, scoring, plugins, or Monad. Synchronous infrastructure implements stable ports. Delivery consumes commands. The OpenHands adapter owns request-copy conversion only. Exact image logic is confined to `media_adapters/**`, `api/media_routes.py`, `runtime_evidence_message_factory.py`, `media_projection/image_narrative_provider.py`, `runtime_contributions.py`, `tools/media_evidence.py`, and `bootstrap/media_composition.py`. Manager/Agent loop, domain scoring, text/URL tools, Domain Plugin, and Monad contain no image condition.

Quarantine owns original bytes temporarily. Normalized object storage owns canonical bytes. Product SQL owns leases, reservations, artifact metadata, Evidence links, idempotency results, quotas, and authorization. OpenHands EventLog owns stable runtime messages/actions/observations. Build Log/API are safe projections. No projection substitutes for another layer's transaction or recovery truth.

## Complete synchronous lifecycle ports

```python
@dataclass(frozen=True)
class ReadOnlyMediaSource:
    stream: SeekableBinaryIO
    byte_size: int
    streaming_sha256: str

class QuarantineWriter(Protocol):
    def write(self, chunk: bytes) -> None: ...
    def finalize(self) -> ReadOnlyQuarantineObject: ...
    def abort(self) -> None: ...
    def close(self) -> None: ...

class ReadOnlyQuarantineObject(Protocol):
    quarantine_id: str
    byte_size: int
    streaming_sha256: str
    def open(self) -> ContextManager[BinaryIO]: ...
    def delete(self) -> None: ...
    def close(self) -> None: ...

class QuarantineStore(Protocol):
    def create(self, reservation_id: str) -> QuarantineWriter: ...

@dataclass(frozen=True)
class ValidatedImageMetadata:
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    byte_size: int
    source_sha256: str
    width: int
    height: int
    has_alpha: bool

class ImageValidator(Protocol):
    def validate(self, source: ReadOnlyMediaSource, declared_media_type: str | None) -> ValidatedImageMetadata: ...

class NormalizedMediaSource(Protocol):
    stream: SeekableBinaryIO
    media_type: str
    byte_size: int
    normalized_sha256: str
    width: int
    height: int
    def rewind(self) -> None: ...
    def close(self) -> None: ...

class ImageNormalizer(Protocol):
    def normalize(self, source: ReadOnlyMediaSource, metadata: ValidatedImageMetadata) -> NormalizedMediaSource: ...

@dataclass(frozen=True)
class StagedMediaObject:
    artifact_id: str
    reservation_id: str
    opaque_object_key: str
    manifest_id: str

class MediaObjectStore(Protocol):
    def stage(self, normalized: NormalizedMediaSource, artifact_id: str, reservation_id: str) -> StagedMediaObject: ...
    def mark_referenced(self, staged: StagedMediaObject) -> None: ...
    def abort_staged(self, staged: StagedMediaObject) -> None: ...
    def open(self, opaque_object_key: str) -> ContextManager[BinaryIO]: ...
    def delete(self, opaque_object_key: str) -> None: ...
```

`MediaIngestionService` owns every handle. It acquires a count lease, creates a system-named writer, streams once with authoritative source SHA-256 and 10 MiB gate, finalizes/closes writer, validates the read-only source, normalizes through a separate port, consumes normalized bytes to verify authoritative normalized hash/size, calls `normalized.rewind()` (equivalent to checked `seek(0)`) immediately before `stage`, stages, executes the final quota/UoW, marks referenced, and closes/deletes temporary sources in `finally`. Before finalize failure it aborts writer; after finalize it deletes quarantine; before SQL success it aborts staged object on failure. Validator owns no storage identity; Normalizer returns closeable canonical bytes. `PillowImageCodecAdapter` may implement both ports while core remains codec-free. Async route crosses once through `asyncio.to_thread`.

Validation gates header dimensions before decode: 40,000,000 pixels, 12,000 per axis, 160 MiB decoded RGBA. Pillow DecompressionBomb warning/error rejects. It executes `verify()`, reopens, then `load()`. PNG requires one terminal IEND and no trailing bytes. WebP RIFF length matches, only defined alignment padding is allowed, and frame count is one/non-animated. JPEG requires one EOI and no following bytes; legal EXIF/ICC is pre-EOI. Normalization applies orientation, deterministic sRGB RGB/RGBA conversion, strips EXIF/ICC/comments/thumbnails/ancillary metadata, and uses versioned fixed encoders. Alpha becomes PNG; opaque PNG stays PNG; JPEG stays JPEG; opaque WebP stays WebP. Only normalized bytes persist and reach models.

Quarantine and normalized objects live under separate least-privilege roots in `FOCUSPROOF_DATA_DIR/media`, with system-generated paths and no public list/read. SQL reservation is authoritative; durable store manifest only makes staged pre-commit objects discoverable. Janitor checks SQL artifact and Evidence references before deletion and never trusts a manifest alone.

## Two-stage linearizable quotas and synchronous UoW

The first short transaction locks `learning_sessions` with PostgreSQL `SELECT FOR UPDATE`, reclaims expired leases, computes committed image count plus active `reserved_image_count`, and succeeds only when adding one remains `<= 4`. Each active lease reserves exactly `count=1` and one owner slot 0..3. It does not predict normalized bytes. The transaction persists owner, Session, fingerprint, slot, active status, and expiry before quarantine begins.

After normalization/staging, the final transaction locks the same Session row, verifies lease active/owner/Session/fingerprint, recomputes committed image count, and calculates committed distinct normalized bytes plus this upload's actual normalized size. Reusing an existing same-owner normalized hash adds zero bytes. Only if count remains `<=4` and bytes remain `<=20 MiB` does the same existing SQL UoW write/reuse artifact, write Evidence, store idempotency result, and consume lease. Multiple uploads may process concurrently, but only transactions satisfying both final conditions commit. A losing upload aborts staged/quarantine objects when unreferenced and terminates its lease with a quota-rejected state.

PostgreSQL two-connection gates prove: with three committed images, four concurrent initial attempts yield exactly one completion; and two uploads whose individual normalized sizes fit but combined size exceeds 20 MiB yield exactly one final commit. SQLite tests provide serial semantics. This quota path uses DB row locks/leases, not `FileSessionRunLock`.

Core returns `IngestedEvidenceResult`, not `StoredEvidence`, and consumes a synchronous transaction port. `persistence/unit_of_work.py` extends existing `UnitOfWork`, `SqlAlchemyUnitOfWork`, `UnitOfWorkFactory`, and `UnitOfWorkFactoryLike`; the same SQLAlchemy Session exposes existing Evidence plus media/idempotency repositories. No parallel async UoW exists.

Migration `agent-server/migrations/versions/0005_media_artifacts.py` sets `revision="0005_media_artifacts"`, `down_revision="0004_monad_evidence_claims"`; creates `media_ingestion_reservations`, `media_artifacts`, then nullable `evidence.artifact_id`. Constraints/indexes are `pk_media_ingestion_reservations`, `fk_media_ingestion_reservations_session`, `uq_media_ingestion_owner_session_key`, `ix_media_ingestion_owner_status_expires`, PostgreSQL partial `uq_media_ingestion_active_owner_slot`, `pk_media_artifacts`, `fk_media_artifacts_reservation`, `uq_media_artifacts_owner_normalized_hash`, `uq_media_artifacts_object_key`, `ix_media_artifacts_owner_state`, `fk_evidence_artifact`, and `ix_evidence_artifact_id`.

`agent-server/migrations/env.py` supports one explicit test/operations override through Alembic `-x database_url=<SQLAlchemy URL>`. It reads `context.get_x_argument(as_dictionary=True)` and accepts only the single key `database_url`; without it, `alembic.ini` remains authoritative. Empty values, unknown keys, repeated keys detected from the raw x-argument list, and SQLAlchemy URLs that cannot be parsed fail closed. Diagnostics identify only the error category and never log the complete URL, username, password, query secret, or credential-bearing exception text.

V1 has no cross-owner physical deduplication. Same-owner normalized hash may serve multiple Evidence rows. Artifact deletion requires authoritative zero-Evidence-reference proof. AI5.1 handles only orphan cleanup, not user deletion/refcount decrement.

| State/window | Owner and durable facts | Recovery |
|---|---|---|
| `LEASED/RECEIVING` | service owns writer; active SQL count lease | abort/close; expiry reclaims lease |
| `QUARANTINED/VALIDATED/NORMALIZED` | service owns quarantine/normalized sources | close/delete in `finally`; 1h temp janitor |
| `STAGED` before SQL success | service owns staged object; manifest ties random key to reservation | abort on failure; 24h janitor confirms no DB reference |
| race/final quota loser | lease terminated; no successful Evidence/result | bind winner only for same-owner/hash valid request; otherwise abort staged |
| SQL success before mark | artifact/Evidence/result and consumed lease exist | startup/janitor sees DB references and marks referenced, never deletes |
| response lost | committed idempotency result | replay returns stored result |

Lifecycle is `LEASED -> RECEIVING -> QUARANTINED -> VALIDATED -> NORMALIZED -> STAGED -> REFERENCED`, with terminal `REJECTED`, `ABORTED`, `EXPIRED`; transitions are idempotent and cannot skip quota/authorization. SQL is atomic only within its transaction; store is compensated.

## Request policy, BFF, and backend capability

`api/request_limits.py` resolves policy before routing by iterating application `APIRoute` objects and calling public `route.matches(scope)`. Only `Match.FULL` plus explicit media-upload route name/metadata gets 11 MiB. `PARTIAL`, 404, 405, Mount/unknown, duplicate-template ambiguity, and method mismatch remain 256 KiB; normal routing emits response. Starlette handles `root_path`. No substring/header raises limits. Authentication precedes significant body reads. Receive wrapper prechecks Content-Length, counts/forwards chunks without caching, and terminates overflow.

Next BFF allowlists one media contract and transparently forwards `request.body`; platform fallback uses 11 MiB-bounded `arrayBuffer`. It preserves boundary/bytes and never calls `request.text()` for multipart. `client.ts:requestMultipart` omits Content-Type; `requestJson` stays unchanged.

`runtime/view.py::AgentView.productCapabilities` is populated by `api/app.py::_view` from conditional composition. `image_evidence` publishes enabled, formats, four, 10 MiB original, 20 MiB normalized, and explanation-required. Disabled is `[]`; SessionWorkspace does not load Image UI. This is not Domain Plugin capability; Monad `pluginCapabilities` is unchanged.

## Exact runtime contribution and safe repository facts

```python
@dataclass(frozen=True)
class RuntimeContribution:
    capabilities: tuple[VerificationCapability, ...]
    tool_definitions: Mapping[str, openhands.sdk.tool.ToolDefinition]

@dataclass(frozen=True)
class MediaEvidenceFacts:
    evidence_id: str
    artifact_ref: str
    media_type: str
    normalized_sha256: str
    byte_size: int
    width: int
    height: int
    learner_explanation: str

class ScopedMediaEvidenceRepository(Protocol):
    def get_media_evidence_facts(self, session_id: str, evidence_id: str) -> MediaEvidenceFacts: ...
```

Task 1 verifies public `Message`/`TextContent`/`ImageContent` model_dump/model_validate roundtrip, `TestLLM` completion/acompletion, public metrics mutation/read, `LocalConversation`, Agent identity on create/restore, required public `ToolDefinition` model fields and concrete registration behavior, process-isolated tool/model registration, dependency versions, and the network-blocking import order before implementation. It does not prove public inner-LLM composition, wrapper identity across recovery, stats/budget/call accounting, or the `LocalConversation`-typed replacement-Agent negative case. A child process may inherit FocusProof registrations already present in the parent; isolation proves only that registrations created inside the child do not leak back to the parent. `ConversationManager(runtime_contributions: Iterable[RuntimeContribution])` builds text/url built-ins first, merges contributions in caller order, and fails closed on duplicate capability ID or tool name. The single Registry/Assembler receives the merged result. Media composition lazily calls official `register_tool` before injection. `tool_registry.py` has no top-level media import. Manager constructor/composition changes only; main loop does not.

The network-isolation contract is explicit: the subprocess imports Python standard-library `ssl`, `http`, and `socket` first, installs a socket-subclass blocker, then imports OpenHands, LiteLLM, and FocusProof. The contract uses no API credentials or proxy credentials.

`FocusProofMediaEvidenceVerificationTool` accepts only evidence ID and calls the scoped repository. Provider implementation in `persistence/providers.py` verifies principal owner, Session, Evidence/artifact link, type/hash/dimensions, and bounded non-blank explanation. DTO and Observation never include object key, path, provider URL, owner, or Session ID. Tool does not open pixels, infer visuals, or score.

Disabled mode is a clean new process: no route/product capability/media tool map, no imports of FocusProof codec/store/route/media-tool modules, and no provider instantiation. It does not require Pillow or python-multipart distributions to be absent because OpenHands 1.31.0 already installs compatible transitive media packages. Startup configuration is immutable because official global tool registry has no unregister.

## Modality-neutral scoring inputs

```python
@dataclass(frozen=True)
class VerifiedLearningNarrative:
    evidence_id: str
    text: str
    verification_status: Literal["success", "failed", "inconclusive"]

class LearningNarrativeProjectionProvider(Protocol):
    def project(self, evidence: Evidence, observations: Sequence[Observation]) -> VerifiedLearningNarrative | None: ...
```

The modality-neutral `LearningNarrative` DTO and provider protocol may live in
`domain/scoring_inputs.py`; they contain no modality fields, image conditions,
or type/allowlist branches. Existing text provider projects `textContent` under
current verification behavior. Each runtime contribution may provide a
projection provider through the same generic protocol. The image-specific
provider lives only in `media_projection/image_narrative_provider.py` and emits
an explanation only when a successful `VerificationObservation` matches the
same evidence ID and safe bounded learner explanation is non-blank.
`LearningNarrativeProjector` combines provider outputs with conflict rejection.
`result_extractor.py` invokes it before scoring and passes narratives, answers,
and generic observations.

`scoring.py` consumes narratives without inspecting Evidence type, modality metadata, image/audio/PDF, hash, dimensions, or artifact. It applies existing near-copy goal, generic/short phrase, goal association, answer independence, six keys, and caps. Image bytes, hash, dimensions, or success alone never raise score. Tests cover absent explanation, failed Observation, copied goal, specific irrelevant explanation, and specific aligned explanation plus independent answer. A fake audio provider proves extension without editing `scoring.py`.

Qwen views pixels through official ImageContent. Visual claims flow through existing ReviewDraftTool/learner answers. Native Action/Observation comes from media repository verification and unchanged Review tools, not direct perception.

## Stable messages and model wrapper

`RuntimeEvidenceMessageFactory.build(StoredEvidence | SafeEvidenceDTO) -> str | Message`; text/URL preserve string envelope, image emits bounded TextContent envelope plus official `ImageContent("focusproof-artifact://<opaque-id>")`. Synchronizer calls `send_message(str | Message)`. `message_envelope_from_event` reads legacy strings and TextContent, preserving key/version/dedup on recovery. EventLog contains four stable URIs, no Base64/path/key/owner/Session/secret/provider URL.

`factory.py` changes `LLMFactory` to accept immutable `RuntimeLLMContext`. Factory uses scoped repositories/UoW to verify authenticated principal, DB owner, and conversation mapping before Agent construction/reconstruction; existing public passed-Agent identity check fails closed. Scope is not global or serialized authorization.

`ArtifactResolvingLLM` is a conditional design only. It may be implemented only after Task5 proves, through an official public OpenHands extension point, public inner-LLM composition, wrapper identity across recovery, stats/budget/call accounting, and the `LocalConversation`-typed replacement-Agent negative case. If the public extension point is absent or any case cannot be proven, Task5 is a hard STOP GATE: record the SDK gap and do not implement an OpenHands-style wrapper/facade or touch private state. Task2-4 remain independently actionable. No real visual LLM acceptance is claimed.

`RealLlmPolicy.quotaFallbackModel`, alias `FOCUSPROOF_LLM_QUOTA_FALLBACK_MODEL`, defaults empty. `.env.example` lists name only. `openhands_adapter/llm_config.py::build_openhands_llm` registers plus and configured flash through public `register_model`, then wraps both with identical resolver/scope. Fallback allowlist is LiteLLM `RateLimitError`, HTTP 429, and provider codes `insufficient_quota`, `QuotaExhausted`, `Arrearage`, `Throttling.RateQuota`; format/400/auth/network/timeout/other codes/5xx/generic errors never fallback. If public composition/accounting cannot be proven, implementation stops with SDK gap and no private patch. VisionInspectTool remains unregistered pending a public profile-wrapper/restore hook.

## Real optional dependency and build contract

Task 1 confirms installed OpenHands 1.31.0 requires Pillow `>=12.1.1` and current `requirements/production.lock` already contains python-multipart before choosing compatible declarations. Root `pyproject.toml` adds `media = ["Pillow>=12.1.1,<13", "python-multipart>=0.0.20,<0.1"]`. The existing `requirements/production.lock` is regenerated with hashes; no parallel media lock is created. `deploy/agent-server.Dockerfile` provides `core` and `media` targets using the same locked environment; their difference is media feature/startup/provider configuration, not package absence. `deploy/compose.staging.yml` explicitly chooses `media` when enabled.

Exact builds from repository root are `docker build --target core -f deploy/agent-server.Dockerfile -t focusproof-agent:core .` and `docker build --target media -f deploy/agent-server.Dockerfile -t focusproof-agent:media .`. Disabled subprocess blocks FocusProof media imports/provider construction; enabled subprocess proves conditional composition.

## Backup, restore, and change radius

`FOCUSPROOF_DATA_DIR/media` joins DB and OpenHands persistence in `scripts/ai4c_backup.py`/`ai4c_restore.py`. Manifest v2 records database, OpenHands archive, media archive SHA-256 and tree version. Restore extracts to isolation, verifies all digests and DB artifact key/hash relations, then switches under maintenance. Missing/mismatched media fails closed before LLM. A restored image review reruns. External object storage later requires versioned snapshot/manifest; AI5.1 implements local recovery. Virus scanning blocks public production, not local/staging.

Store changes affect store/quarantine adapters/composition/tests. Codec changes affect codec adapter/composition/fixtures. Provider changes affect profiles/LLM config/composition/gate. OpenHands changes affect adapter/message factory/factory/SDK tests/gap. Audio/PDF adds codec/route/UI/message mapping/runtime contribution/narrative provider using existing UoW/table and does not alter Manager loop/scoring schema/Monad. Disabling AI5 removes conditional route/capability/contribution/UI/resolver while nullable schema remains and disabled import graph stays clean. Any broader radius is an architecture failure.
