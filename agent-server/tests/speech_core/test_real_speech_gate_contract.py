from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import pytest

import scripts.run_real_speech_gate as gate


def _valid_environment() -> dict[str, str]:
    return {
        "FOCUSPROOF_ASR_PROVIDER": "dashscope",
        "FOCUSPROOF_ASR_MODEL": "qwen3-asr-flash",
        "FOCUSPROOF_ASR_BASE_URL": (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        "DASHSCOPE_API_KEY": "placeholder",
        "FOCUSPROOF_ASR_E2E_TIMEOUT_SECONDS": "120",
        "FOCUSPROOF_ASR_MAX_CONCURRENCY": "4",
        "FOCUSPROOF_SPEECH_IDEMPOTENCY_HMAC_KEY": "different-hmac-test-secret",
        "DATABASE_URL": "postgresql+psycopg://gate:secret@example.invalid/focusproof_test_task3_real_speech_gate",
        "FOCUSPROOF_PROFILE": "staging",
        "FOCUSPROOF_MEDIA_SCANNER_MODE": "clamd",
        "FOCUSPROOF_CLAMD_ENDPOINT": "tcp://127.0.0.1:3310",
        "FOCUSPROOF_CLAMD_DEFINITIONS_VERSION": "contract-test",
    }


def _clips(root: Path) -> tuple[Path, Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths = (root / "zh.wav", root / "en.mp3", root / "mixed.webm")
    for index, path in enumerate(paths, start=1):
        path.write_bytes(bytes((index,)) * 8)
    return paths


def _passing_summary() -> gate.GateExecutionSummary:
    return gate.GateExecutionSummary(
        duration_ms=321,
        clip_count=3,
        provider_call_count=3,
        editable_candidate_count=3,
        language_feature_count=2,
        clamd_case_count=6,
        clamd_pass_count=6,
        postgres_check_count=4,
        postgres_pass_count=4,
        privacy_check_count=5,
        privacy_pass_count=5,
        cleanup_passed=True,
        residue_free=True,
    )


def test_gate_refuses_without_authorization_before_any_preflight_or_external_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = tmp_path / "report.json"
    monkeypatch.setattr(
        gate,
        "_preflight",
        lambda *a, **k: pytest.fail("preflight must not run without authorization"),
    )
    monkeypatch.setattr(
        gate,
        "_run_real_gate",
        lambda *a, **k: pytest.fail("external gate must not run without authorization"),
    )

    assert gate.main(["--report", str(report)]) == 2

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["reasonCode"] == "authorization_required"
    assert payload["authorized"] is False
    assert payload["realAsrExecuted"] is False
    assert payload["realClamdExecuted"] is False


def test_authorized_gate_requires_all_three_explicit_clip_arguments_before_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = tmp_path / "report.json"
    monkeypatch.setattr(
        gate,
        "load_project_env",
        lambda *a, **k: pytest.fail("configuration must not load before arguments pass"),
    )

    assert gate.main(["--authorized", "--report", str(report)]) == 2

    assert json.loads(report.read_text(encoding="utf-8"))["reasonCode"] == (
        "three_audio_files_required"
    )




def test_real_gate_does_not_require_manual_submit_for_task7_candidate_acceptance(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.json"
    chinese, english, mixed = _clips(tmp_path / "focusproof-real-speech")

    args = gate._parse_args(
        [
            "--authorized",
            "--report",
            str(report),
            "--chinese",
            str(chinese),
            "--english",
            str(english),
            "--mixed",
            str(mixed),
        ]
    )

    assert not hasattr(args, "manual_submit")
    summary = _passing_summary()
    assert gate._summary_passes(summary)
    assert gate.build_report(
        authorized=True,
        summary=summary,
        reason_code=None,
    )["repositoryBoundaryEvidenceSeeded"] is False


@pytest.mark.parametrize("invalid_kind", ["relative", "outside", "symlink", "duplicate"])
def test_clip_paths_must_be_distinct_canonical_regular_files_under_the_locked_root(
    invalid_kind: str, tmp_path: Path
) -> None:
    root = tmp_path / "focusproof-real-speech"
    chinese, english, mixed = _clips(root)
    if invalid_kind == "relative":
        chinese = Path("relative.wav")
    elif invalid_kind == "outside":
        chinese = tmp_path / "outside.wav"
        chinese.write_bytes(b"outside")
    elif invalid_kind == "symlink":
        link = root / "alias.wav"
        link.symlink_to(chinese)
        chinese = link
    else:
        mixed = chinese

    with pytest.raises(gate.GateBlocked) as caught:
        gate._resolve_audio_inputs(chinese, english, mixed, root=root)

    assert caught.value.reason_code == "invalid_audio_inputs"


def test_clip_paths_are_labeled_in_the_exact_chinese_english_mixed_order(
    tmp_path: Path,
) -> None:
    root = tmp_path / "focusproof-real-speech"
    inputs = gate._resolve_audio_inputs(*_clips(root), root=root)
    assert tuple(item.language_category for item in inputs) == (
        "chinese",
        "english",
        "mixed",
    )


def test_preflight_reuses_project_and_speech_configuration_and_accepts_only_postgres(
    tmp_path: Path,
) -> None:
    root = tmp_path / "focusproof-real-speech"
    args = SimpleNamespace(
        chinese=_clips(root)[0],
        english=root / "en.mp3",
        mixed=root / "mixed.webm",
    )
    calls: list[str] = []

    result = gate._preflight(
        args,
        root=root,
        environ={"DATABASE_URL": _valid_environment()["DATABASE_URL"]},
        project_env_loader=lambda _root: _valid_environment(),
        tool_probe=lambda: calls.append("tools") or True,
        database_probe=lambda _url: calls.append("database") or True,
        clamd_probe=lambda _policy: calls.append("clamd") or True,
    )

    assert result.settings.provider == "dashscope"
    assert result.settings.model == "qwen3-asr-flash"
    assert calls == ["tools", "database", "clamd"]

    sqlite_environment = _valid_environment()
    sqlite_environment["DATABASE_URL"] = "sqlite+pysqlite:////tmp/not-allowed.sqlite3"
    with pytest.raises(gate.GateBlocked) as caught:
        gate._preflight(
            args,
            root=root,
            environ={},
            project_env_loader=lambda _root: sqlite_environment,
            tool_probe=lambda: True,
            database_probe=lambda _url: pytest.fail("SQLite must not be probed"),
            clamd_probe=lambda _policy: pytest.fail("Clamd must not be probed"),
        )
    assert caught.value.reason_code == "postgresql_required"
    nondisposable = _valid_environment()
    nondisposable["DATABASE_URL"] = (
        "postgresql+psycopg://gate:secret@example.invalid/focusproof"
    )
    with pytest.raises(gate.GateBlocked) as caught:
        gate._preflight(
            args,
            root=root,
            environ={},
            project_env_loader=lambda _root: nondisposable,
            tool_probe=lambda: True,
            database_probe=lambda _url: pytest.fail("shared database must not be probed"),
            clamd_probe=lambda _policy: pytest.fail("Clamd must not be probed"),
        )
    assert caught.value.reason_code == "postgresql_required"


@pytest.mark.parametrize(
    ("failed_probe", "reason_code"),
    [
        ("tools", "required_tools_unavailable"),
        ("database", "postgresql_unavailable"),
        ("clamd", "clamd_unavailable"),
    ],
)
def test_every_real_preflight_probe_fails_closed(
    failed_probe: str, reason_code: str, tmp_path: Path
) -> None:
    root = tmp_path / "focusproof-real-speech"
    chinese, english, mixed = _clips(root)
    args = SimpleNamespace(
        chinese=chinese,
        english=english,
        mixed=mixed,
    )

    with pytest.raises(gate.GateBlocked) as caught:
        gate._preflight(
            args,
            root=root,
            environ={},
            project_env_loader=lambda _root: _valid_environment(),
            tool_probe=lambda: failed_probe != "tools",
            database_probe=lambda _url: failed_probe != "database",
            clamd_probe=lambda _policy: failed_probe != "clamd",
        )

    assert caught.value.reason_code == reason_code


def test_incomplete_or_fake_configuration_is_blocked_without_external_probes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "focusproof-real-speech"
    chinese, english, mixed = _clips(root)
    args = SimpleNamespace(
        chinese=chinese,
        english=english,
        mixed=mixed,
    )
    invalid = _valid_environment()
    invalid["FOCUSPROOF_MEDIA_SCANNER_MODE"] = "fake-clean"

    with pytest.raises(gate.GateBlocked) as caught:
        gate._preflight(
            args,
            root=root,
            environ={},
            project_env_loader=lambda _root: invalid,
            tool_probe=lambda: pytest.fail("tools probe must not run"),
            database_probe=lambda _url: pytest.fail("database probe must not run"),
            clamd_probe=lambda _policy: pytest.fail("Clamd probe must not run"),
        )

    assert caught.value.reason_code == "real_configuration_required"


@pytest.mark.parametrize(
    "field",
    [
        "provider_call_count",
        "editable_candidate_count",
        "clamd_pass_count",
        "postgres_pass_count",
        "privacy_pass_count",
        "cleanup_passed",
        "residue_free",
    ],
)
def test_pass_is_false_when_any_required_check_is_incomplete(field: str) -> None:
    value: Any = False if isinstance(getattr(_passing_summary(), field), bool) else 0
    report = gate.build_report(
        authorized=True,
        summary=replace(_passing_summary(), **{field: value}),
        reason_code="gate_failed",
    )
    assert report["status"] == "FAIL"
    assert report["passed"] is False


def test_passing_report_contains_only_redacted_counts_booleans_and_model_metadata() -> None:
    report = gate.build_report(
        authorized=True,
        summary=_passing_summary(),
        reason_code=None,
    )
    encoded = json.dumps(report, sort_keys=True)

    assert report["status"] == "PASS"
    assert report["provider"] == "dashscope"
    assert report["model"] == "qwen3-asr-flash"
    assert report["clipCount"] == 3
    assert report["providerCallCount"] == 3
    assert report["clamdCaseCount"] == 6
    assert report["postgresCheckCount"] == 4
    assert report["repositoryBoundaryEvidenceSeeded"] is False
    assert report["productManualSubmitProved"] is False
    assert report["task8ProductUiRequired"] is True
    assert "manualEvidenceOnly" not in report
    for forbidden in (
        "placeholder",
        "different-hmac-test-secret",
        "postgresql+psycopg",
        "/tmp/",
        "transcript",
        "endpoint",
        "databaseurl",
        "a learner said",
    ):
        assert forbidden not in encoded.lower()


def test_atomic_report_replaces_cleanly_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text("old", encoding="utf-8")

    gate._write_report(report_path, {"status": "BLOCKED", "passed": False})

    assert json.loads(report_path.read_text(encoding="utf-8")) == {
        "passed": False,
        "status": "BLOCKED",
    }
    assert not report_path.with_suffix(".json.tmp").exists()

def test_language_features_are_bounded_without_storing_candidate_text() -> None:
    assert gate._language_feature_matches("chinese", "这是中文")
    assert not gate._language_feature_matches("chinese", "plain english")
    assert gate._language_feature_matches("english", "plain English words")
    assert not gate._language_feature_matches("english", "纯中文")
    assert gate._language_feature_matches("mixed", "中文 and English")
    assert not gate._language_feature_matches("mixed", "English only")


def test_postgres_verification_runs_exact_real_nodes_with_bounded_redacted_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setenv("DASHSCOPE_API_KEY", "provider-child-secret")
    monkeypatch.setenv("FOCUSPROOF_SPEECH_IDEMPOTENCY_HMAC_KEY", "hmac-child-secret")
    monkeypatch.setenv("FOCUSPROOF_REAL_SPEECH_CHINESE", "/private/zh.wav")

    def runner(command: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="4 passed in 1.0s", stderr="")

    assert gate._run_postgres_checks(
        "postgresql+psycopg://gate:secret@127.0.0.1/focusproof_test_task3_speech_gate",
        runner=runner,
    ) == (4, 4)

    command, kwargs = calls[0]
    assert command[:3] == [str(gate.ROOT / ".venv" / "bin" / "python"), "-m", "pytest"]
    assert "-m" in command
    assert "postgres" in command
    assert kwargs["timeout"] <= 180
    assert kwargs["capture_output"] is True
    assert kwargs["env"]["FOCUSPROOF_TEST_POSTGRES_URL"].startswith("postgresql")
    assert all("secret" not in argument for argument in command)
    assert set(kwargs["env"]) <= {
        "FOCUSPROOF_TEST_POSTGRES_URL",
        "HOME",
        "LANG",
        "PATH",
        "PYTHONIOENCODING",
        "PYTHONUNBUFFERED",
        "TMPDIR",
    }
    child_values = tuple(kwargs["env"].values())
    assert all("provider-child-secret" not in value for value in child_values)
    assert all("hmac-child-secret" not in value for value in child_values)
    assert all("/private/" not in value for value in kwargs["env"].values())


def test_postgres_verification_rejects_skipped_or_deselected_nodes() -> None:
    def runner(_command: list[str], **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout="4 skipped, 4 deselected in 0.1s",
            stderr="",
        )

    with pytest.raises(RuntimeError, match="PostgreSQL acceptance failed"):
        gate._run_postgres_checks(
            "postgresql+psycopg://gate:secret@127.0.0.1/"
            "focusproof_test_task3_speech_gate",
            runner=runner,
        )


def test_database_probe_requires_an_empty_public_schema_and_always_disposes() -> None:
    class Connection:
        def execute(self, statement: object) -> SimpleNamespace:
            del statement
            return SimpleNamespace(scalar_one=lambda: 1)

        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        connect=lambda: Connection(),
        dispose=lambda: setattr(engine, "disposed", True),
        disposed=False,
    )

    assert not gate._database_available(
        "postgresql://unused", engine_factory=lambda _: engine
    )
    assert engine.disposed is True


@pytest.mark.parametrize(
    ("current_schema", "object_count", "expected"),
    [("public", 0, True), ("private", 0, False), ("public", 1, False)],
)
def test_database_probe_checks_public_schema_and_all_user_objects(
    current_schema: str,
    object_count: int,
    expected: bool,
) -> None:
    queries: list[str] = []

    class Connection:
        def execute(self, statement: object) -> SimpleNamespace:
            query = str(statement).lower()
            queries.append(query)
            value = current_schema if "current_schema" in query else object_count
            return SimpleNamespace(scalar_one=lambda: value)

        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        connect=lambda: Connection(),
        dispose=lambda: None,
    )

    assert gate._database_available(
        "postgresql://unused", engine_factory=lambda _: engine
    ) is expected
    assert any("current_schema" in query.lower() for query in queries)
    assert any("pg_namespace" in query.lower() for query in queries)


@pytest.mark.parametrize(
    "catalog_marker", ["nspname <> 'public'", "pg_proc", "pg_type"]
)
def test_database_probe_rejects_user_schemas_and_nonrelation_objects(
    catalog_marker: str,
) -> None:
    queries: list[str] = []

    class Connection:
        def execute(self, statement: object) -> SimpleNamespace:
            query = str(statement).lower()
            queries.append(query)
            value = "public" if "current_schema" in query else int(catalog_marker in query)
            return SimpleNamespace(scalar_one=lambda: value)

        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        connect=lambda: Connection(),
        dispose=lambda: None,
    )

    assert not gate._database_available(
        "postgresql://unused", engine_factory=lambda _: engine
    )
    assert any(catalog_marker in query for query in queries)


def test_preflight_rejects_search_path_override_before_external_probes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "focusproof-real-speech"
    chinese, english, mixed = _clips(root)
    args = SimpleNamespace(
        chinese=chinese,
        english=english,
        mixed=mixed,
    )
    configured = _valid_environment()
    configured["DATABASE_URL"] += "?options=-csearch_path%3Dprivate"

    with pytest.raises(gate.GateBlocked) as caught:
        gate._preflight(
            args,
            root=root,
            environ={},
            project_env_loader=lambda _root: configured,
            tool_probe=lambda: pytest.fail("tools must not be probed"),
            database_probe=lambda _url: pytest.fail("database must not be probed"),
            clamd_probe=lambda _policy: pytest.fail("Clamd must not be probed"),
        )

    assert caught.value.reason_code == "postgresql_required"


def test_real_gate_rechecks_fresh_database_immediately_before_postgres(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "focusproof-real-speech"
    chinese, english, mixed = _clips(root)
    args = SimpleNamespace(
        chinese=chinese,
        english=english,
        mixed=mixed,
    )
    context = gate._preflight(
        args,
        root=root,
        environ={},
        project_env_loader=lambda _root: _valid_environment(),
        tool_probe=lambda: True,
        database_probe=lambda _url: True,
        clamd_probe=lambda _policy: True,
    )
    events: list[str] = []
    monkeypatch.setattr(
        gate,
        "_run_clamd_checks",
        lambda _policy: events.append("clamd") or (6, 6),
    )
    monkeypatch.setattr(
        gate,
        "_database_available",
        lambda _url: events.append("database") or False,
    )
    monkeypatch.setattr(
        gate,
        "_run_postgres_checks",
        lambda _url: pytest.fail("destructive checks must not start"),
    )

    with pytest.raises(gate.GateBlocked) as caught:
        gate._run_real_gate(context)

    assert caught.value.reason_code == "postgresql_not_fresh"
    assert events == ["clamd", "database"]


def test_clamd_matrix_reuses_existing_live_matrix_and_adds_typed_oversize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_cases = tuple(
        SimpleNamespace(passed=True)
        for _ in ("clean", "eicar", "timeout", "unavailable", "error")
    )
    monkeypatch.setattr(gate, "run_live_matrix", lambda _endpoint: live_cases)

    class Scanner:
        def scan(self, source: object) -> SimpleNamespace:
            del source
            return SimpleNamespace(status="oversize")

    monkeypatch.setattr(gate, "build_malware_scanner", lambda _policy: Scanner())
    policy = gate.load_media_security_policy("staging", _valid_environment())

    assert gate._run_clamd_checks(policy) == (6, 6)


def test_real_gate_aggregates_only_counts_and_cleanup_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "focusproof-real-speech"
    args = SimpleNamespace(
        chinese=_clips(root)[0],
        english=root / "en.mp3",
        mixed=root / "mixed.webm",
    )
    context = gate._preflight(
        args,
        root=root,
        environ={},
        project_env_loader=lambda _root: _valid_environment(),
        tool_probe=lambda: True,
        database_probe=lambda _url: True,
        clamd_probe=lambda _policy: True,
    )
    monkeypatch.setattr(gate, "_run_clamd_checks", lambda _policy: (6, 6))
    monkeypatch.setattr(gate, "_database_available", lambda _url: True)
    monkeypatch.setattr(gate, "_run_postgres_checks", lambda _url: (4, 4))
    monkeypatch.setattr(
        gate,
        "_run_speech_acceptance",
        lambda _context: gate.SpeechAcceptanceSummary(
            provider_call_count=3,
            editable_candidate_count=3,
            language_feature_count=3,
            privacy_check_count=5,
            privacy_pass_count=5,
            cleanup_passed=True,
            residue_free=True,
        ),
    )

    summary = gate._run_real_gate(context)

    assert summary.clip_count == 3
    assert summary.clamd_pass_count == 6
    assert summary.postgres_pass_count == 4
    assert summary.provider_call_count == 3
    assert summary.privacy_pass_count == 5
    assert summary.cleanup_passed is True


def test_unexpected_external_failure_never_leaks_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    arguments = _clips(tmp_path / "focusproof-real-speech")
    report = tmp_path / "report.json"
    monkeypatch.setattr(gate, "_preflight", lambda _args: object())

    def fail(_context: object) -> gate.GateExecutionSummary:
        raise RuntimeError("provider-secret candidate-text /private/audio.wav")

    monkeypatch.setattr(gate, "_run_real_gate", fail)

    exit_code = gate.main(
        [
            "--authorized",
            "--report",
            str(report),
            "--chinese",
            str(arguments[0]),
            "--english",
            str(arguments[1]),
            "--mixed",
            str(arguments[2]),
        ]
    )

    encoded = report.read_text(encoding="utf-8")
    assert exit_code == 1
    assert "provider-secret" not in encoded
    assert "candidate-text" not in encoded
    assert "/private/" not in encoded
    assert json.loads(encoded)["passed"] is False

def test_local_upload_streams_to_destination_without_retaining_audio(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.wav"
    destination = tmp_path / "destination.audio"
    payload = b"x" * (130 * 1024)
    source.write_bytes(payload)
    upload = gate._LocalSpeechUpload(source)

    uploaded = gate.asyncio.run(
        upload.write_to(destination, deadline=gate.time.monotonic() + 2)
    )

    assert uploaded.byte_size == len(payload)
    assert uploaded.streaming_sha256 == gate.sha256(payload).hexdigest()
    assert destination.read_bytes() == payload
    assert upload.declared_media_type == "audio/wav"


def test_preflight_context_repr_hides_database_and_audio_paths(tmp_path: Path) -> None:
    root = tmp_path / "focusproof-real-speech"
    args = SimpleNamespace(
        chinese=_clips(root)[0],
        english=root / "en.mp3",
        mixed=root / "mixed.webm",
    )
    context = gate._preflight(
        args,
        root=root,
        environ={},
        project_env_loader=lambda _root: _valid_environment(),
        tool_probe=lambda: True,
        database_probe=lambda _url: True,
        clamd_probe=lambda _policy: True,
    )

    rendered = repr(context)
    assert "placeholder" not in rendered
    assert "postgresql+psycopg" not in rendered
    assert str(root) not in rendered


def test_real_speech_path_uses_existing_provider_service_scanner_and_uow() -> None:
    source = Path(gate.__file__).read_text(encoding="utf-8")

    for required in (
        "DashScopeSpeechTranscriptionProvider",
        "TranscriptionService",
        "build_malware_scanner",
        "ResourceSlotController",
        "UnitOfWorkFactory",
        "MediainfoAudioInspector",
    ):
        assert required in source
    for forbidden in ("TestLLM", "Conversation(", "EventLog(", "requests.post"):
        assert forbidden not in source


def test_real_gate_reuses_mediainfo_only_inspector_prerequisites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        gate.MediainfoAudioInspector,
        "prerequisites_available",
        lambda: calls.append("speech-inspector") or True,
    )

    assert gate._tools_available()
    assert calls == ["speech-inspector"]

    inspector_source = Path(
        gate.MediainfoAudioInspector.__module__.replace(".", "/") + ".py"
    )
    source = (gate.ROOT / "agent-server" / inspector_source).read_text(
        encoding="utf-8"
    )
    lowered = source.casefold()
    assert 'path("/usr/bin/mediainfo")' in lowered
    for forbidden in ("ffprobe", "ffmpeg", "blas", "lapack"):
        assert forbidden not in lowered

def test_real_fixture_and_script_documentation_keep_audio_outside_git() -> None:
    fixture_readme = (
        gate.ROOT / "agent-server" / "tests" / "fixtures" / "real-speech" / "README.md"
    ).read_text(encoding="utf-8")
    scripts_readme = (gate.ROOT / "scripts" / "README.md").read_text(encoding="utf-8")

    assert "/tmp/focusproof-real-speech/" in fixture_readme
    assert "Do not commit" in fixture_readme
    assert "--authorized" in scripts_readme
    assert "--manual-submit" not in scripts_readme
    assert "productManualSubmitProved" in scripts_readme
    assert "real_asr" in scripts_readme

def test_provider_close_is_bounded_and_temp_cleanup_is_idempotent(
    tmp_path: Path,
) -> None:
    class SlowProvider:
        async def aclose(self) -> None:
            await gate.asyncio.sleep(1)

    with pytest.raises(TimeoutError):
        gate.asyncio.run(
            gate._close_provider(SlowProvider(), timeout_seconds=0.001)
        )

    temp_dir = tmp_path / "gate-temp"
    temp_dir.mkdir()
    (temp_dir / "request.audio").write_bytes(b"temporary")
    assert gate._cleanup_temp_dir(temp_dir) == (False, True)
    assert gate._cleanup_temp_dir(temp_dir) == (True, True)


def test_cleanup_continues_after_provider_timeout_and_disposes_engine(
    tmp_path: Path,
) -> None:
    class SlowProvider:
        async def aclose(self) -> None:
            await gate.asyncio.sleep(1)

    class Engine:
        disposed = False

        def dispose(self) -> None:
            self.disposed = True

    engine = Engine()
    temp_dir = tmp_path / "gate-temp"
    temp_dir.mkdir()
    (temp_dir / "request.audio").write_bytes(b"temporary")

    with pytest.raises(TimeoutError):
        gate.asyncio.run(
            gate._cleanup_gate_resources(
                provider=SlowProvider(),
                temp_dir=temp_dir,
                engine=engine,
                timeout_seconds=0.001,
            )
        )

    assert not temp_dir.exists()
    assert engine.disposed is True


def test_cleanup_reports_service_residue_even_after_bounded_removal(
    tmp_path: Path,
) -> None:
    class Provider:
        async def aclose(self) -> None:
            return None

    temp_dir = tmp_path / "gate-temp"
    temp_dir.mkdir()
    (temp_dir / "request.audio").write_bytes(b"temporary")

    result = gate.asyncio.run(
        gate._cleanup_gate_resources(
            provider=Provider(), temp_dir=temp_dir, engine=None
        )
    )

    assert result == (False, True)
    assert not temp_dir.exists()


def test_local_upload_rejects_source_replaced_by_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    outside = tmp_path / "outside.wav"
    destination = tmp_path / "destination.audio"
    source.write_bytes(b"original")
    outside.write_bytes(b"outside")
    upload = gate._LocalSpeechUpload(source)
    source.unlink()
    source.symlink_to(outside)

    with pytest.raises(RuntimeError, match="audio source changed"):
        gate.asyncio.run(
            upload.write_to(destination, deadline=gate.time.monotonic() + 2)
        )

    assert not destination.exists()


def test_local_upload_rejects_parent_directory_replaced_by_symlink(
    tmp_path: Path,
) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    source = approved / "source.wav"
    source.write_bytes(b"original")
    upload = gate._LocalSpeechUpload(source)

    original = tmp_path / "original"
    approved.rename(original)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.wav").write_bytes(b"outside")
    approved.symlink_to(outside, target_is_directory=True)
    destination = tmp_path / "destination.audio"

    with pytest.raises(RuntimeError, match="audio source changed"):
        gate.asyncio.run(
            upload.write_to(destination, deadline=gate.time.monotonic() + 2)
        )

    assert not destination.exists()


def test_local_upload_opens_nonblocking_and_requires_a_regular_file() -> None:
    source = Path(gate.__file__).read_text(encoding="utf-8")

    assert "os.O_NONBLOCK" in source
    assert "stat.S_ISREG" in source


def test_database_payload_scan_is_parameterized_and_fails_on_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio = tmp_path / "clip.wav"
    audio_bytes = b"raw-audio-private"
    audio.write_bytes(audio_bytes)
    queries: list[str] = []
    parameters: list[dict[str, object]] = []

    class Inspector:
        def get_table_names(self, *, schema: str) -> list[str]:
            assert schema == "public"
            return ["evidence"]

        def get_columns(self, table_name: str, *, schema: str) -> list[dict[str, object]]:
            assert table_name == "evidence"
            assert schema == "public"
            return [
                {"name": "text_content", "type": "TEXT"},
                {"name": "metadata_json", "type": "JSONB"},
                {"name": "raw_bytes", "type": "BYTEA"},
            ]

    monkeypatch.setattr(gate, "inspect_schema", lambda _engine: Inspector())

    class Connection:
        def execute(self, statement: object, params: dict[str, object] | None = None) -> SimpleNamespace:
            query = str(statement)
            queries.append(query)
            parameters.append(params or {})
            matched = params is not None and params.get("audio_bytes") == audio_bytes
            return SimpleNamespace(scalar_one=lambda: int(matched))


        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    engine = SimpleNamespace(
        dialect=SimpleNamespace(identifier_preparer=SimpleNamespace(quote=lambda name: '"' + name + '"')),
        connect=lambda: Connection(),
    )

    assert not gate._database_payload_absent(
        engine,
        candidates=("private candidate",),
        audio_inputs=(gate.AudioInput("english", audio),),
    )
    assert any(":needle" in query for query in queries)
    assert any(":audio_bytes" in query for query in queries)
    assert all("private candidate" not in query for query in queries)
    assert all(audio_bytes.decode() not in query for query in queries)
    assert any("private candidate" in str(value) for params in parameters for value in params.values())


def test_privacy_matrix_includes_direct_evidence_query_and_payload_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[str, dict[str, object]]] = []
    payload_calls: list[tuple[Sequence[str], Sequence[gate.AudioInput]]] = []

    class Connection:
        def execute(self, statement: object, params: dict[str, object]) -> SimpleNamespace:
            queries.append((str(statement), params))
            return SimpleNamespace(scalar_one=lambda: 0)

        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class Uow:
        evidence = SimpleNamespace(list_for_session=lambda _session_id: [])
        reviews = SimpleNamespace(list_for_session=lambda _session_id: [])
        audit_events = SimpleNamespace(list=lambda _session_id: [])
        sessions = SimpleNamespace(get=lambda _session_id: SimpleNamespace(review_result=None))

        def __enter__(self) -> "Uow":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class Factory:
        def __call__(self) -> Uow:
            return Uow()

    class Inspector:
        def get_columns(self, _table_name: str) -> list[dict[str, str]]:
            return [{"name": "state"}]

    monkeypatch.setattr(gate, "inspect_schema", lambda _engine: Inspector())
    monkeypatch.setattr(
        gate,
        "_database_payload_absent",
        lambda _engine, *, candidates, audio_inputs: payload_calls.append(
            (candidates, audio_inputs)
        ) or True,
    )
    engine = SimpleNamespace(connect=lambda: Connection())

    assert gate._privacy_checks(
        Factory(),
        engine,
        "session-under-test",
        candidates=("private candidate",),
        audio_inputs=(),
    ) == (5, 5)
    assert any("FROM public.evidence" in query for query, _params in queries)
    assert any(params.get("session_id") == "session-under-test" for _query, params in queries)
    assert payload_calls == [(('private candidate',), ())]
