from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import Any, Literal, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect as inspect_schema
from sqlalchemy import text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-server"))

from focusproof.bootstrap.media_composition import build_malware_scanner  # noqa: E402
from focusproof.config.env import load_project_env, load_speech_settings  # noqa: E402
from focusproof.config.profiles import (  # noqa: E402
    MediaSecurityPolicy,
    load_media_security_policy,
)
from focusproof.media_core.ports import ReadOnlyMediaSource  # noqa: E402
from focusproof.persistence.database import create_database_engine  # noqa: E402
from focusproof.speech_adapters.mediainfo_inspector import (  # noqa: E402
    MediainfoAudioInspector,
)
from focusproof.api.speech_routes import SuffixAwareAudioInspector  # noqa: E402
from focusproof.media_application import ResourceSlotController  # noqa: E402
from focusproof.persistence.database import create_session_factory  # noqa: E402
from focusproof.persistence.repositories import (  # noqa: E402
    StoredSession,
)
from focusproof.persistence.unit_of_work import UnitOfWorkFactory  # noqa: E402
from focusproof.speech_adapters.dashscope_asr import (  # noqa: E402
    DashScopeSpeechTranscriptionProvider,
)
from focusproof.speech_application import (  # noqa: E402
    SpeechExecutionAdmission,
    TranscriptionService,
    UploadedSpeechFile,
)
from focusproof.speech_core.models import (  # noqa: E402
    LanguageHint,
    TranscriptionRequest,
    TranscriptionResult,
)
from focusproof.speech_core.models import (  # noqa: E402
    MAX_AUDIO_BYTES,
    SpeechSettings,
)
from scripts.run_real_image_evidence_gate import run_live_matrix  # noqa: E402

_APPROVED_AUDIO_ROOT = Path("/tmp/focusproof-real-speech")
_ALLOWED_SUFFIXES = frozenset({".mp3", ".wav", ".webm"})
LanguageCategory = Literal["chinese", "english", "mixed"]


class GateBlocked(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__("real speech gate blocked")


@dataclass(frozen=True, slots=True)
class AudioInput:
    language_category: LanguageCategory
    path: Path = field(repr=False)


@dataclass(frozen=True, slots=True)
class PreflightContext:
    inputs: tuple[AudioInput, AudioInput, AudioInput]
    settings: SpeechSettings
    media_policy: MediaSecurityPolicy
    database_url: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class GateExecutionSummary:
    duration_ms: int
    clip_count: int
    provider_call_count: int
    editable_candidate_count: int
    language_feature_count: int
    clamd_case_count: int
    clamd_pass_count: int
    postgres_check_count: int
    postgres_pass_count: int
    privacy_check_count: int
    privacy_pass_count: int
    cleanup_passed: bool
    residue_free: bool


def _empty_summary() -> GateExecutionSummary:
    return GateExecutionSummary(
        duration_ms=0,
        clip_count=0,
        provider_call_count=0,
        editable_candidate_count=0,
        language_feature_count=0,
        clamd_case_count=0,
        clamd_pass_count=0,
        postgres_check_count=0,
        postgres_pass_count=0,
        privacy_check_count=0,
        privacy_pass_count=0,
        cleanup_passed=False,
        residue_free=False,
    )


def _summary_passes(summary: GateExecutionSummary) -> bool:
    return (
        summary.clip_count == 3
        and summary.provider_call_count == 3
        and summary.editable_candidate_count == 3
        and summary.clamd_case_count == 6
        and summary.clamd_pass_count == 6
        and summary.postgres_check_count == 4
        and summary.postgres_pass_count == 4
        and summary.privacy_check_count == 5
        and summary.privacy_pass_count == 5
        and summary.cleanup_passed
        and summary.residue_free
    )


def build_report(
    *,
    authorized: bool,
    summary: GateExecutionSummary | None = None,
    reason_code: str | None,
) -> dict[str, object]:
    actual = summary or _empty_summary()
    passed = authorized and reason_code is None and _summary_passes(actual)
    if passed:
        status = "PASS"
    elif authorized and summary is not None:
        status = "FAIL"
    else:
        status = "BLOCKED"
    return {
        "gate": "real_speech",
        "status": status,
        "passed": passed,
        "authorized": authorized,
        "reasonCode": reason_code,
        "provider": "dashscope" if passed else None,
        "model": "qwen3-asr-flash" if passed else None,
        "durationMs": actual.duration_ms,
        "clipCount": actual.clip_count,
        "providerCallCount": actual.provider_call_count,
        "editableCandidateCount": actual.editable_candidate_count,
        "languageFeatureCount": actual.language_feature_count,
        "clamdCaseCount": actual.clamd_case_count,
        "clamdPassCount": actual.clamd_pass_count,
        "postgresCheckCount": actual.postgres_check_count,
        "postgresPassCount": actual.postgres_pass_count,
        "privacyCheckCount": actual.privacy_check_count,
        "privacyPassCount": actual.privacy_pass_count,
        "repositoryBoundaryEvidenceSeeded": False,
        "productManualSubmitProved": False,
        "task8ProductUiRequired": True,
        "cleanupPassed": actual.cleanup_passed,
        "residueFree": actual.residue_free,
        "realAsrExecuted": actual.provider_call_count > 0,
        "realClamdExecuted": actual.clamd_case_count > 0,
    }


def _write_report(path: Path, report: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(dict(report), sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_redacted_report(path: Path) -> dict[str, Any]:
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("gate report is invalid")
    return decoded


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_audio_inputs(
    chinese: Path,
    english: Path,
    mixed: Path,
    *,
    root: Path = _APPROVED_AUDIO_ROOT,
) -> tuple[AudioInput, AudioInput, AudioInput]:
    if not all(path.is_absolute() for path in (chinese, english, mixed)):
        raise GateBlocked("invalid_audio_inputs")
    approved = root.resolve()
    resolved: list[Path] = []
    for path in (chinese, english, mixed):
        if path.is_symlink():
            raise GateBlocked("invalid_audio_inputs")
        candidate = path.resolve(strict=False)
        if (
            not _is_within(candidate, approved)
            or candidate.parent != approved
            or _is_within(candidate, ROOT.resolve())
            or not candidate.is_file()
            or candidate.suffix.lower() not in _ALLOWED_SUFFIXES
        ):
            raise GateBlocked("invalid_audio_inputs")
        size = candidate.stat().st_size
        if size <= 0 or size > MAX_AUDIO_BYTES:
            raise GateBlocked("invalid_audio_inputs")
        resolved.append(candidate)
    if len(set(resolved)) != 3:
        raise GateBlocked("invalid_audio_inputs")
    return (
        AudioInput("chinese", resolved[0]),
        AudioInput("english", resolved[1]),
        AudioInput("mixed", resolved[2]),
    )


def _tools_available() -> bool:
    return MediainfoAudioInspector.prerequisites_available()


def _database_available(
    database_url: str,
    *,
    engine_factory: Callable[[str], Any] = create_database_engine,
) -> bool:
    engine = None
    try:
        engine = engine_factory(database_url)
        if engine.dialect.name != "postgresql":
            return False
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current_schema = connection.execute(
                text("SELECT current_schema()")
            ).scalar_one()
            object_count = connection.execute(
                text(
                    "SELECT ("
                    "SELECT COUNT(*) FROM pg_namespace "
                    "WHERE nspname <> 'public' "
                    "AND nspname <> 'information_schema' "
                    "AND nspname <> 'pg_catalog' "
                    "AND nspname <> 'pg_toast' "
                    "AND nspname NOT LIKE 'pg_temp_%' "
                    "AND nspname NOT LIKE 'pg_toast_temp_%'"
                    ") + ("
                    "SELECT COUNT(*) FROM pg_class AS class "
                    "JOIN pg_namespace AS namespace "
                    "ON namespace.oid = class.relnamespace "
                    "WHERE namespace.nspname = 'public'"
                    ") + ("
                    "SELECT COUNT(*) FROM pg_proc AS proc_entry "
                    "JOIN pg_namespace AS namespace "
                    "ON namespace.oid = proc_entry.pronamespace "
                    "WHERE namespace.nspname = 'public'"
                    ") + ("
                    "SELECT COUNT(*) FROM pg_type AS type_entry "
                    "JOIN pg_namespace AS namespace "
                    "ON namespace.oid = type_entry.typnamespace "
                    "WHERE namespace.nspname = 'public'"
                    ")"
                )
            ).scalar_one()
        return current_schema == "public" and int(object_count) == 0
    except Exception:
        return False
    finally:
        if engine is not None:
            engine.dispose()


def _clamd_available(policy: MediaSecurityPolicy) -> bool:
    try:
        scanner = build_malware_scanner(policy)
        payload = b""
        verdict = scanner.scan(
            ReadOnlyMediaSource(
                stream=BytesIO(payload),
                byte_size=0,
                streaming_sha256=sha256(payload).hexdigest(),
            )
        )
        return verdict.status == "clean"
    except Exception:
        return False


def _merged_environment(
    root: Path,
    environ: Mapping[str, str],
    loader: Callable[[Path], dict[str, str]],
) -> dict[str, str]:
    configured = loader(ROOT)
    configured.update(environ)
    return configured


def _preflight(
    args: argparse.Namespace,
    *,
    root: Path = _APPROVED_AUDIO_ROOT,
    environ: Mapping[str, str] = os.environ,
    project_env_loader: Callable[[Path], dict[str, str]] = load_project_env,
    tool_probe: Callable[[], bool] = _tools_available,
    database_probe: Callable[[str], bool] = _database_available,
    clamd_probe: Callable[[MediaSecurityPolicy], bool] = _clamd_available,
) -> PreflightContext:
    inputs = _resolve_audio_inputs(args.chinese, args.english, args.mixed, root=root)
    configured = _merged_environment(ROOT, environ, project_env_loader)
    try:
        settings = load_speech_settings(configured)
        profile = configured.get("FOCUSPROOF_PROFILE", "")
        if settings is None or profile not in {"staging", "production"}:
            raise ValueError
        policy = load_media_security_policy(profile, configured)  # type: ignore[arg-type]
        if policy.mode != "clamd":
            raise ValueError
        database_url = configured.get("DATABASE_URL", "")
        parsed = make_url(database_url)
        query_names = {name.lower() for name in parsed.query}
        if (
            parsed.get_backend_name() != "postgresql"
            or parsed.database is None
            or not parsed.database.startswith("focusproof_test_task3_")
            or "options" in query_names
            or any("schema" in name for name in query_names)
        ):
            raise GateBlocked("postgresql_required")
    except GateBlocked:
        raise
    except Exception:
        raise GateBlocked("real_configuration_required") from None
    if not tool_probe():
        raise GateBlocked("required_tools_unavailable")
    if not database_probe(database_url):
        raise GateBlocked("postgresql_unavailable")
    if not clamd_probe(policy):
        raise GateBlocked("clamd_unavailable")
    return PreflightContext(
        inputs=inputs,
        settings=settings,
        media_policy=policy,
        database_url=database_url,
    )


@dataclass(frozen=True, slots=True)
class SpeechAcceptanceSummary:
    provider_call_count: int
    editable_candidate_count: int
    language_feature_count: int
    privacy_check_count: int
    privacy_pass_count: int
    cleanup_passed: bool
    residue_free: bool


def _language_feature_matches(category: LanguageCategory, candidate: str) -> bool:
    has_cjk = any("\u4e00" <= character <= "\u9fff" for character in candidate)
    has_latin = any(
        ("a" <= character.lower() <= "z")
        for character in candidate
    )
    if category == "chinese":
        return has_cjk
    if category == "english":
        return has_latin
    return has_cjk and has_latin


_POSTGRES_NODES = (
    (
        "agent-server/tests/persistence/test_speech_postgres_concurrency.py::"
        "test_independent_workers_cannot_overshoot_session_or_owner_quota"
    ),
    (
        "agent-server/tests/persistence/test_speech_postgres_concurrency.py::"
        "test_two_processes_cannot_duplicate_one_hmac_key"
    ),
    (
        "agent-server/tests/persistence/test_speech_postgres_concurrency.py::"
        "test_postgres_recovery_fences_stale_generation"
    ),
    (
        "agent-server/tests/integration/test_shared_scan_slots.py::"
        "test_independent_engines_and_processes_share_image_and_speech_scan_slots"
    ),
)


def _run_postgres_checks(
    database_url: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[int, int]:
    inherited_names = ("HOME", "LANG", "PATH", "TMPDIR")
    environment = {
        name: value
        for name in inherited_names
        if (value := os.environ.get(name)) is not None
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUNBUFFERED"] = "1"
    environment["FOCUSPROOF_TEST_POSTGRES_URL"] = database_url
    command = [
        str(ROOT / ".venv" / "bin" / "python"),
        "-m",
        "pytest",
        "-q",
        "-m",
        "postgres",
        *_POSTGRES_NODES,
    ]
    completed = runner(
        command,
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    output = completed.stdout.lower()
    if (
        completed.returncode != 0
        or f"{len(_POSTGRES_NODES)} passed" not in output
        or "skipped" in output
        or "deselected" in output
    ):
        raise RuntimeError("PostgreSQL acceptance failed")
    return len(_POSTGRES_NODES), len(_POSTGRES_NODES)


def _run_clamd_checks(policy: MediaSecurityPolicy) -> tuple[int, int]:
    if policy.endpoint is None:
        raise RuntimeError("Clamd acceptance unavailable")
    live_cases = run_live_matrix(policy.endpoint)
    scanner = build_malware_scanner(policy)
    payload = b""
    oversize = scanner.scan(
        ReadOnlyMediaSource(
            stream=BytesIO(payload),
            byte_size=policy.max_scan_bytes + 1,
            streaming_sha256=sha256(payload).hexdigest(),
        )
    )
    passed = sum(1 for case in live_cases if case.passed)
    if oversize.status == "oversize":
        passed += 1
    return 6, passed


class _LocalSpeechUpload:
    _MEDIA_TYPES = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".webm": "audio/webm;codecs=opus",
    }

    def __init__(self, source: Path) -> None:
        self._source = source
        self.declared_media_type: str | None = self._MEDIA_TYPES[source.suffix.lower()]

    async def write_to(
        self,
        destination: Path,
        *,
        deadline: float,
    ) -> UploadedSpeechFile:
        return await asyncio.to_thread(self._copy, destination, deadline)

    def _copy(self, destination: Path, deadline: float) -> UploadedSpeechFile:
        digest = sha256()
        byte_size = 0
        try:
            directory_descriptor = os.open(
                self._source.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            try:
                descriptor = os.open(
                    self._source.name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=directory_descriptor,
                )
            finally:
                os.close(directory_descriptor)
        except OSError:
            raise RuntimeError("audio source changed") from None
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size <= 0
                or metadata.st_size > MAX_AUDIO_BYTES
            ):
                raise RuntimeError("audio source changed")
            with destination.open("xb") as target:
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("speech upload deadline expired")
                    chunk = source.read(64 * 1024)
                    if not chunk:
                        break
                    byte_size += len(chunk)
                    if byte_size > MAX_AUDIO_BYTES:
                        raise RuntimeError("audio source exceeded gate limit")
                    digest.update(chunk)
                    target.write(chunk)
        return UploadedSpeechFile(
            byte_size=byte_size,
            streaming_sha256=digest.hexdigest(),
        )


class _CountingProvider:
    def __init__(self, delegate: DashScopeSpeechTranscriptionProvider) -> None:
        self._delegate = delegate
        self.call_count = 0

    async def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        deadline: float,
    ) -> TranscriptionResult:
        self.call_count += 1
        if self.call_count > 3:
            raise RuntimeError("provider call bound exceeded")
        return await self._delegate.transcribe(request, deadline=deadline)

    async def aclose(self) -> None:
        await self._delegate.aclose()
class _AsyncClosable(Protocol):
    async def aclose(self) -> None: ...


async def _close_provider(
    provider: _AsyncClosable,
    *,
    timeout_seconds: float = 5.0,
) -> None:
    async with asyncio.timeout(timeout_seconds):
        await provider.aclose()


def _upgrade_database(database_url: str) -> None:
    configuration = AlembicConfig(ROOT / "alembic.ini")
    configuration.set_main_option(
        "script_location",
        str(ROOT / "agent-server" / "migrations"),
    )
    configuration.cmd_opts = argparse.Namespace(x=["database_url=" + database_url])
    alembic_command.upgrade(configuration, "head")


def _gate_session(session_id: str, owner_id: str) -> StoredSession:
    now = datetime.now(UTC)
    return StoredSession(
        session_id=session_id,
        owner_user_id=owner_id,
        status="running",
        adapter_mode="real-speech-gate",
        domain="general",
        title="Real speech acceptance",
        goal="Verify authorized speech transcription",
        expected_output=None,
        planned_minutes=5,
        conversation_id=str(uuid5(NAMESPACE_URL, "focusproof:" + session_id)),
        runtime_mode="real-speech-gate",
        review_result=None,
        goal_conversation_synced_at=None,
        version=1,
        created_at=now,
        updated_at=now,
    )


def _database_payload_absent(
    engine: Any,
    *,
    candidates: Sequence[str],
    audio_inputs: Sequence[AudioInput],
) -> bool:
    from base64 import b64encode

    try:
        inspector = inspect_schema(engine)
        markers = [candidate for candidate in candidates if candidate.strip()]
        audio_payloads: list[bytes] = []
        for audio_input in audio_inputs:
            audio_bytes = audio_input.path.read_bytes()
            audio_payloads.append(audio_bytes)
            markers.extend(
                (
                    audio_bytes.hex(),
                    b64encode(audio_bytes).decode("ascii"),
                )
            )
        unique_markers = tuple(dict.fromkeys(markers))
        with engine.connect() as connection:
            for table_name in inspector.get_table_names(schema="public"):
                quoted_table = engine.dialect.identifier_preparer.quote(table_name)
                text_columns: list[str] = []
                bytea_columns: list[str] = []
                for column in inspector.get_columns(table_name, schema="public"):
                    quoted_column = engine.dialect.identifier_preparer.quote(
                        str(column["name"])
                    )
                    column_type = str(column["type"]).lower()
                    if "bytea" in column_type:
                        bytea_columns.append(quoted_column)
                    elif any(
                        fragment in column_type
                        for fragment in ("char", "text", "json", "uuid")
                    ):
                        text_columns.append(quoted_column)
                if text_columns:
                    predicates = " OR ".join(
                        "position(:needle IN CAST(" + column + " AS text)) > 0"
                        for column in text_columns
                    )
                    statement = text(
                        f"SELECT COUNT(*) FROM public.{quoted_table} WHERE {predicates}"
                    )
                    for marker in unique_markers:
                        if int(
                            connection.execute(
                                statement, {"needle": marker}
                            ).scalar_one()
                        ) != 0:
                            return False
                for bytea_column in bytea_columns:
                    statement = text(
                        f"SELECT COUNT(*) FROM public.{quoted_table} "
                        f"WHERE {bytea_column} = :audio_bytes"
                    )
                    for audio_bytes in audio_payloads:
                        if int(
                            connection.execute(
                                statement, {"audio_bytes": audio_bytes}
                            ).scalar_one()
                        ) != 0:
                            return False
        return True
    except Exception:
        return False


def _privacy_checks(
    factory: UnitOfWorkFactory,
    engine: Any,
    session_id: str,
    *,
    candidates: Sequence[str],
    audio_inputs: Sequence[AudioInput],
) -> tuple[int, int]:
    checks: list[bool] = []
    with factory() as uow:
        evidence_empty = uow.evidence.list_for_session(session_id) == []
        reviews_empty = uow.reviews.list_for_session(session_id) == []
        audit_empty = uow.audit_events.list(session_id) == []
        stored = uow.sessions.get(session_id)
        session_safe = stored is not None and stored.review_result is None
    try:
        with engine.connect() as connection:
            evidence_count = connection.execute(
                text("SELECT COUNT(*) FROM public.evidence WHERE session_id = :session_id"),
                {"session_id": session_id},
            ).scalar_one()
    except Exception:
        evidence_count = 1
    checks.append(evidence_empty and int(evidence_count) == 0)
    checks.append(reviews_empty)
    checks.append(audit_empty)
    checks.append(session_safe)
    forbidden = ("audio", "blob", "candidate", "payload", "path", "transcript")
    schema = inspect_schema(engine)
    safe_schema = True
    for table_name in ("speech_transcription_requests", "speech_resource_slots"):
        for column in schema.get_columns(table_name):
            name = str(column["name"]).lower()
            if any(fragment in name for fragment in forbidden):
                safe_schema = False
    checks.append(
        safe_schema
        and _database_payload_absent(
            engine,
            candidates=candidates,
            audio_inputs=audio_inputs,
        )
    )
    return len(checks), sum(checks)


def _cleanup_temp_dir(temp_dir: Path) -> tuple[bool, bool]:
    if not temp_dir.exists():
        return True, True
    leftovers = tuple(temp_dir.iterdir())
    service_clean = not leftovers
    for child in leftovers:
        if not child.is_file() and not child.is_symlink():
            raise RuntimeError("unexpected speech cleanup entry")
        child.unlink(missing_ok=True)
    temp_dir.rmdir()
    return service_clean, not temp_dir.exists()


async def _cleanup_gate_resources(
    *,
    provider: _AsyncClosable | None,
    temp_dir: Path | None,
    engine: Any | None,
    timeout_seconds: float = 5.0,
) -> tuple[bool, bool]:
    errors: list[BaseException] = []
    service_clean = True
    residue_free = True
    if provider is not None:
        try:
            await _close_provider(provider, timeout_seconds=timeout_seconds)
        except BaseException as exc:
            errors.append(exc)
    if temp_dir is not None:
        try:
            service_clean, residue_free = _cleanup_temp_dir(temp_dir)
        except BaseException as exc:
            errors.append(exc)
    if engine is not None:
        try:
            engine.dispose()
        except BaseException as exc:
            errors.append(exc)
    if errors:
        raise errors[0]
    return service_clean, residue_free


def _configure_speech_hmac_keyring(
    factory: UnitOfWorkFactory,
    settings: SpeechSettings,
) -> None:
    factory.configure_speech(
        active_hmac_key_version=settings.idempotency_hmac_active_version,
        hmac_keys={version: key.encode("utf-8") for version, key in settings.idempotency_hmac_keyring},
    )


async def _run_speech_acceptance_with_engine(
    context: PreflightContext,
    engine: Any,
) -> SpeechAcceptanceSummary:
    factory = UnitOfWorkFactory(create_session_factory(engine))
    _configure_speech_hmac_keyring(factory, context.settings)
    owner_id = "real-speech-gate"
    session_id = "sess_" + uuid4().hex
    with factory() as uow:
        uow.sessions.create(_gate_session(session_id, owner_id))
        uow.resource_slots.reconcile(
            "asr",
            configured_count=context.settings.max_concurrency,
            config_generation=1,
        )
        uow.commit()
    scan_slots = ResourceSlotController(factory, lease_seconds=20)
    scan_slots.reconcile(
        configured_count=context.media_policy.max_concurrent_scans,
        config_generation=1,
    )
    provider = _CountingProvider(
        DashScopeSpeechTranscriptionProvider(api_key=context.settings.api_key)
    )
    temp_dir = (_APPROVED_AUDIO_ROOT / ("gate-temp-" + uuid4().hex)).resolve()
    try:
        temp_dir.mkdir(parents=True, exist_ok=False)
    except BaseException:
        await _cleanup_gate_resources(
            provider=provider,
            temp_dir=None,
            engine=None,
        )
        raise
    candidates: list[str] = []
    language_feature_count = 0
    cleanup_attempted = False

    async def connected() -> bool:
        return False

    try:
        service = TranscriptionService(
            uow_factory=factory,
            malware_scanner=build_malware_scanner(context.media_policy),
            scan_slots=scan_slots,
            audio_inspector=SuffixAwareAudioInspector(MediainfoAudioInspector()),
            provider=provider,
            temp_dir=temp_dir,
        )
        hints = {
            "chinese": LanguageHint.ZH,
            "english": LanguageHint.EN,
            "mixed": LanguageHint.AUTO,
        }
        for item in context.inputs:
            idempotency_key = str(uuid4())
            with factory() as uow:
                token = uow.speech_requests.admit(
                    owner_user_id=owner_id,
                    session_id=session_id,
                    idempotency_key=idempotency_key,
                    request_fingerprint=None,
                    lease_owner="real-gate-" + item.language_category,
                )
                uow.commit()
            result = await service.execute(
                SpeechExecutionAdmission(
                    token=token,
                    deadline=time.monotonic() + context.settings.e2e_timeout_seconds,
                ),
                _LocalSpeechUpload(item.path),
                hints[item.language_category],
                connected,
            )
            candidate = result.transcript
            candidates.append(candidate)
            if _language_feature_matches(item.language_category, candidate):
                language_feature_count += 1
        privacy_count, privacy_passed = _privacy_checks(
            factory,
            engine,
            session_id,
            candidates=tuple(candidates),
            audio_inputs=context.inputs,
        )
        cleanup_attempted = True
        cleanup_passed, residue_free = await _cleanup_gate_resources(
            provider=provider,
            temp_dir=temp_dir,
            engine=None,
        )
        return SpeechAcceptanceSummary(
            provider_call_count=provider.call_count,
            editable_candidate_count=sum(
                1 for candidate in candidates if candidate.strip()
            ),
            language_feature_count=language_feature_count,
            privacy_check_count=privacy_count,
            privacy_pass_count=privacy_passed,
            cleanup_passed=cleanup_passed,
            residue_free=residue_free,
        )
    finally:
        if not cleanup_attempted:
            cleanup_attempted = True
            await _cleanup_gate_resources(
                provider=provider,
                temp_dir=temp_dir,
                engine=None,
            )


async def _run_speech_acceptance_async(
    context: PreflightContext,
) -> SpeechAcceptanceSummary:
    _upgrade_database(context.database_url)
    engine = create_database_engine(context.database_url)
    try:
        return await _run_speech_acceptance_with_engine(context, engine)
    finally:
        await _cleanup_gate_resources(
            provider=None,
            temp_dir=None,
            engine=engine,
        )


def _run_speech_acceptance(context: PreflightContext) -> SpeechAcceptanceSummary:
    return asyncio.run(_run_speech_acceptance_async(context))


def _run_real_gate(context: PreflightContext) -> GateExecutionSummary:
    clamd_count, clamd_passed = _run_clamd_checks(context.media_policy)
    if not _database_available(context.database_url):
        raise GateBlocked("postgresql_not_fresh")
    postgres_count, postgres_passed = _run_postgres_checks(context.database_url)
    speech = _run_speech_acceptance(context)
    return GateExecutionSummary(
        duration_ms=0,
        clip_count=len(context.inputs),
        provider_call_count=speech.provider_call_count,
        editable_candidate_count=speech.editable_candidate_count,
        language_feature_count=speech.language_feature_count,
        clamd_case_count=clamd_count,
        clamd_pass_count=clamd_passed,
        postgres_check_count=postgres_count,
        postgres_pass_count=postgres_passed,
        privacy_check_count=speech.privacy_check_count,
        privacy_pass_count=speech.privacy_pass_count,
        cleanup_passed=speech.cleanup_passed,
        residue_free=speech.residue_free,
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorized", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--chinese", type=Path)
    parser.add_argument("--english", type=Path)
    parser.add_argument("--mixed", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    started = time.monotonic()
    if not args.authorized:
        _write_report(
            args.report,
            build_report(
                authorized=False,
                reason_code="authorization_required",
            ),
        )
        return 2
    if args.chinese is None or args.english is None or args.mixed is None:
        _write_report(
            args.report,
            build_report(
                authorized=True,
                reason_code="three_audio_files_required",
            ),
        )
        return 2
    try:
        context = _preflight(args)
        summary = _run_real_gate(context)
        elapsed = max(0, round((time.monotonic() - started) * 1000))
        summary = GateExecutionSummary(
            duration_ms=elapsed,
            clip_count=summary.clip_count,
            provider_call_count=summary.provider_call_count,
            editable_candidate_count=summary.editable_candidate_count,
            language_feature_count=summary.language_feature_count,
            clamd_case_count=summary.clamd_case_count,
            clamd_pass_count=summary.clamd_pass_count,
            postgres_check_count=summary.postgres_check_count,
            postgres_pass_count=summary.postgres_pass_count,
            privacy_check_count=summary.privacy_check_count,
            privacy_pass_count=summary.privacy_pass_count,
            cleanup_passed=summary.cleanup_passed,
            residue_free=summary.residue_free,
        )
        report = build_report(authorized=True, summary=summary, reason_code=None)
        _write_report(args.report, report)
        return 0 if report["passed"] is True else 1
    except GateBlocked as exc:
        _write_report(
            args.report,
            build_report(
                authorized=True,
                reason_code=exc.reason_code,
            ),
        )
        return 2
    except Exception:
        _write_report(
            args.report,
            build_report(
                authorized=True,
                summary=_empty_summary(),
                reason_code="gate_execution_failed",
            ),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
