from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from typing import Any, cast

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import jwt
import pytest
import yaml  # type: ignore[import-untyped]


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_DIR = PROJECT_ROOT / "deploy"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
PROVIDER_KEYS = (
    "DASHSCOPE_API_KEY",
    "OPENAI_API_KEY",
    "FOCUSPROOF_LLM_API_KEY",
    "ANTHROPIC_API_KEY",
)
LOCAL_ISSUER = "https://oidc-issuer:8443"
LOCAL_AUDIENCE = "focusproof-api"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))


def _read_required(path: Path) -> str:
    assert path.is_file(), f"missing required Task 4 artifact: {path.relative_to(PROJECT_ROOT)}"
    return path.read_text(encoding="utf-8")


def _load_compose() -> dict[str, Any]:
    payload = yaml.safe_load(_read_required(DEPLOY_DIR / "compose.staging.yml"))
    assert isinstance(payload, dict)
    services = payload.get("services")
    assert isinstance(services, dict)
    return payload


def _service(compose: dict[str, Any], name: str) -> dict[str, Any]:
    services = compose["services"]
    service = services.get(name)
    assert isinstance(service, dict), f"missing compose service {name}"
    return service


def _flatten_command(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return ""


def _host_ports(service: dict[str, Any]) -> list[str]:
    ports = service.get("ports") or []
    assert isinstance(ports, list)
    return [str(port) for port in ports]


def _safe_subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    if "DOCKER_HOST" in os.environ:
        env["DOCKER_HOST"] = os.environ["DOCKER_HOST"]
    if extra:
        env.update(extra)
    for key in PROVIDER_KEYS:
        env.pop(key, None)
    return env


def _run_checked(
    args: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_safe_subprocess_env(env),
        )
    except subprocess.CalledProcessError as exc:
        diagnostic = "\n".join((exc.stdout or "", exc.stderr or ""))[-8_000:]
        if "compose" in args and "up" in args:
            compose_prefix = args[: args.index("up")]
            logs = subprocess.run(
                compose_prefix + ["logs", "--no-color", "--tail", "200"],
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                env=_safe_subprocess_env(env),
            )
            diagnostic += "\n" + "\n".join((logs.stdout, logs.stderr))[-8_000:]
        for local_secret in (
            "local-task4-postgres-password",
            "task4-local-fingerprint-key-32-bytes",
        ):
            diagnostic = diagnostic.replace(local_secret, "[redacted]")
        pytest.fail(
            f"command failed with exit {exc.returncode}: {args!r}\n{diagnostic}",
            pytrace=False,
        )


def _require_staging_capabilities() -> None:
    import check_ai4c_capabilities

    report = check_ai4c_capabilities.detect_capabilities()
    check_ai4c_capabilities.require_capabilities(
        report,
        ("container_cli", "compose", "postgres_client"),
    )


def _git_index_archive_context(tmp_path: Path) -> Path:
    tree = _run_checked(["git", "write-tree"], cwd=PROJECT_ROOT, timeout=10).stdout.strip()
    assert tree
    archive_path = tmp_path / "focusproof-source.tar"
    with archive_path.open("wb") as archive_file:
        subprocess.run(
            ["git", "archive", "--format=tar", tree],
            cwd=PROJECT_ROOT,
            check=True,
            stdout=archive_file,
            stderr=subprocess.PIPE,
            timeout=20,
            env=_safe_subprocess_env(),
        )
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    with tarfile.open(archive_path) as archive:
        archive.extractall(context_dir, filter="data")
    return context_dir


def _write_local_issuer_materials(tmp_path: Path) -> tuple[Path, str]:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "postgres_password").write_text(
        "local-task4-postgres-password",
        encoding="utf-8",
    )
    (secrets_dir / "oidc_fingerprint_key").write_text(
        "task4-local-fingerprint-key-32-bytes",
        encoding="utf-8",
    )

    jwt_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwt_private = jwt_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(jwt_key.public_key()))
    kid = "task4-local-kid"
    public_jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    (secrets_dir / "oidc_jwks.json").write_text(
        json.dumps({"keys": [public_jwk]}, sort_keys=True),
        encoding="utf-8",
    )

    tls_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "oidc-issuer")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(tls_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=7))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("oidc-issuer")]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(tls_key, hashes.SHA256())
    )
    (secrets_dir / "oidc_tls_key.pem").write_bytes(
        tls_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    tls_cert = cert.public_bytes(serialization.Encoding.PEM)
    (secrets_dir / "oidc_tls_cert.pem").write_bytes(tls_cert)
    (secrets_dir / "oidc_ca.pem").write_bytes(tls_cert)

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": LOCAL_ISSUER,
            "aud": LOCAL_AUDIENCE,
            "sub": "task4-owner",
            "iat": int(now.timestamp()),
            "nbf": int((now - timedelta(seconds=1)).timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        },
        jwt_private,
        algorithm="RS256",
        headers={"kid": kid},
    )
    return secrets_dir, str(token)


def _compose_env(secrets_dir: Path) -> dict[str, str]:
    return {
        "FOCUSPROOF_STAGING_BACKEND_HOST_PORT": "127.0.0.1:18080",
        "FOCUSPROOF_STAGING_FRONTEND_HOST_PORT": "127.0.0.1:13000",
        "FOCUSPROOF_STAGING_POSTGRES_PASSWORD_FILE": str(secrets_dir / "postgres_password"),
        "FOCUSPROOF_STAGING_OIDC_FINGERPRINT_KEY_FILE": str(secrets_dir / "oidc_fingerprint_key"),
        "FOCUSPROOF_STAGING_OIDC_JWKS_FILE": str(secrets_dir / "oidc_jwks.json"),
        "FOCUSPROOF_STAGING_OIDC_TLS_CERT_FILE": str(secrets_dir / "oidc_tls_cert.pem"),
        "FOCUSPROOF_STAGING_OIDC_TLS_KEY_FILE": str(secrets_dir / "oidc_tls_key.pem"),
        "FOCUSPROOF_STAGING_OIDC_CA_FILE": str(secrets_dir / "oidc_ca.pem"),
    }


def _http_json(
    url: str,
    *,
    token: str,
    payload: dict[str, object] | None = None,
) -> dict[str, Any]:
    from urllib import request

    data = None if payload is None else json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        url,
        data=data,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        },
        method="GET" if payload is None else "POST",
    )
    with request.urlopen(http_request, timeout=15) as response:
        assert response.status == 200
        payload = json.loads(response.read().decode("utf-8"))
        assert isinstance(payload, dict)
        return cast(dict[str, Any], payload)


def test_static_backend_image_is_pinned_hash_locked_and_non_root_single_worker() -> None:
    dockerfile = _read_required(DEPLOY_DIR / "agent-server.Dockerfile")

    assert "FROM python:" in dockerfile
    assert "@sha256:" in dockerfile
    assert "latest" not in dockerfile
    assert "USER focusproof" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "requirements/production.lock" in dockerfile
    assert "pip install" in dockerfile
    assert "--no-deps" in dockerfile
    assert "alembic upgrade" not in dockerfile
    assert "--workers" in dockerfile
    assert " 1" in dockerfile or '", "1"' in dockerfile


def test_static_frontend_image_uses_npm_ci_and_production_next_start_non_root() -> None:
    dockerfile = _read_required(DEPLOY_DIR / "frontend.Dockerfile")

    assert "FROM node:" in dockerfile
    assert "@sha256:" in dockerfile
    assert "latest" not in dockerfile
    assert "npm ci" in dockerfile
    assert "npm run build" in dockerfile
    assert "next" in dockerfile
    assert "start" in dockerfile
    assert "next dev" not in dockerfile
    assert "USER focusproof" in dockerfile


def test_static_dockerignore_excludes_host_state_and_test_artifacts() -> None:
    dockerignore = _read_required(PROJECT_ROOT / ".dockerignore")
    required_patterns = {
        ".env",
        ".env.*",
        "var/",
        ".venv/",
        "agent-server/tests/",
        "frontend/tests/",
        "frontend/e2e/",
        "frontend/node_modules/",
        "frontend/.next/",
        "frontend/test-results/",
        "**/__pycache__/",
    }

    for pattern in required_patterns:
        assert pattern in dockerignore
    assert "!.env.example" in dockerignore


def test_static_compose_has_single_host_topology_with_private_database() -> None:
    compose = _load_compose()

    assert set(compose["services"]) >= {"postgres", "migrate", "agent-server", "frontend"}
    postgres = _service(compose, "postgres")
    migrate = _service(compose, "migrate")
    backend = _service(compose, "agent-server")
    frontend = _service(compose, "frontend")

    assert postgres.get("ports") in (None, [])
    assert "focusproof-postgres-data" in str(postgres.get("volumes", ""))
    assert "focusproof-openhands-data" in str(backend.get("volumes", ""))
    assert all(port.startswith("127.0.0.1:") for port in _host_ports(backend))
    assert all(port.startswith("127.0.0.1:") for port in _host_ports(frontend))
    assert "condition: service_completed_successfully" in yaml.safe_dump(backend.get("depends_on"))
    assert "postgres" in str(migrate.get("depends_on", ""))
    assert migrate.get("restart") in {"no", "on-failure"}

    backend_command = _flatten_command(backend.get("command"))
    assert "--workers" in backend_command
    assert " 1" in backend_command or "workers=1" in backend_command or '", "1"' in backend_command
    assert "alembic upgrade" not in backend_command


def test_static_compose_uses_read_only_secrets_health_checks_and_explicit_migration() -> None:
    compose = _load_compose()
    postgres = _service(compose, "postgres")
    migrate = _service(compose, "migrate")
    backend = _service(compose, "agent-server")
    frontend = _service(compose, "frontend")

    assert compose.get("secrets")
    for service in (postgres, migrate, backend):
        mounted_secrets = service.get("secrets")
        assert mounted_secrets, f"{service} must mount required secrets"
        assert all(
            isinstance(secret, dict) and secret.get("mode") in {0o440, "0440"}
            for secret in mounted_secrets
        )
    for service in (postgres, backend, frontend):
        assert service.get("healthcheck"), f"{service} must define a healthcheck"

    assert "alembic upgrade head" in _flatten_command(migrate.get("command"))
    assert "/ready" in yaml.safe_dump(backend.get("healthcheck"))
    assert "FOCUSPROOF_API_BASE_URL" in yaml.safe_dump(frontend.get("environment"))
    assert "OPENAI_API_KEY" not in yaml.safe_dump(compose)
    assert "DASHSCOPE_API_KEY" not in yaml.safe_dump(compose)
    assert "ANTHROPIC_API_KEY" not in yaml.safe_dump(compose)


def test_static_env_example_documents_non_secret_staging_inputs_only() -> None:
    example = _read_required(PROJECT_ROOT / ".env.example")

    for name in (
        "FOCUSPROOF_STAGING_BACKEND_HOST_PORT",
        "FOCUSPROOF_STAGING_FRONTEND_HOST_PORT",
        "FOCUSPROOF_STAGING_POSTGRES_PASSWORD_FILE",
        "FOCUSPROOF_STAGING_OIDC_FINGERPRINT_KEY_FILE",
        "FOCUSPROOF_STAGING_OIDC_JWKS_FILE",
        "FOCUSPROOF_STAGING_OIDC_TLS_CERT_FILE",
        "FOCUSPROOF_STAGING_OIDC_TLS_KEY_FILE",
        "FOCUSPROOF_STAGING_OIDC_CA_FILE",
    ):
        assert name in example
    assert "password-goes-here" not in example.lower()


def test_static_backend_exposes_dedicated_ready_endpoint() -> None:
    source = _read_required(PROJECT_ROOT / "agent-server/focusproof/api/app.py")

    assert '@application.get("/ready")' in source
    assert "readiness_error" in source
    assert "database_unavailable" in source


@pytest.mark.staging_external
def test_staging_external_stack_builds_runs_and_preserves_ids(tmp_path: Path) -> None:
    _require_staging_capabilities()
    context_dir = _git_index_archive_context(tmp_path)
    secrets_dir, token = _write_local_issuer_materials(tmp_path)
    compose_file = context_dir / "deploy/compose.staging.yml"
    assert compose_file.is_file()

    project_name = "focusproof-ai4c-task4"
    compose = [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "-f",
        str(compose_file),
    ]
    compose_env = _compose_env(secrets_dir)
    try:
        for _ in range(2):
            _run_checked(
                compose + ["up", "--build", "-d", "--wait"],
                cwd=context_dir,
                timeout=600,
                env=compose_env,
            )
            image_ids = _run_checked(
                compose + ["images", "-q"],
                cwd=context_dir,
                timeout=30,
                env=compose_env,
            ).stdout.splitlines()
            assert len([image_id for image_id in image_ids if image_id]) >= 2
            canonical_image_ids = {
                _run_checked(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", image_id],
                    cwd=context_dir,
                    timeout=30,
                    env=compose_env,
                ).stdout.strip()
                for image_id in image_ids
                if image_id
            }
            assert len(canonical_image_ids) >= 2
            assert all(image_id.startswith("sha256:") for image_id in canonical_image_ids)

            session = _http_json(
                "http://127.0.0.1:13000/api/focusproof/sessions",
                token=token,
                payload={
                    "domain": "general",
                    "title": "Task4 staging persistence",
                    "goal": "Verify a general learning claim survives restart.",
                    "expectedOutput": "A concise explanation",
                    "plannedMinutes": 15,
                },
            )
            session_id = str(session["sessionId"])
            _http_json(
                f"http://127.0.0.1:13000/api/focusproof/sessions/{session_id}/evidence",
                token=token,
                payload={
                    "evidenceType": "text",
                    "textContent": "OpenHands native events and product projections must survive restart.",
                    "metadata": {"source": "task4"},
                },
            )
            _http_json(
                f"http://127.0.0.1:13000/api/focusproof/sessions/{session_id}/answer",
                token=token,
                payload={"questionId": "q_task4", "answer": "Restart keeps durable IDs stable."},
            )
            review = _http_json(
                f"http://127.0.0.1:13000/api/focusproof/sessions/{session_id}/review",
                token=token,
                payload={},
            )
            assert review["reviewStatus"] == "completed"
            first_state = _http_json(
                f"http://127.0.0.1:13000/api/focusproof/sessions/{session_id}",
                token=token,
            )
            first_events = _http_json(
                f"http://127.0.0.1:13000/api/focusproof/sessions/{session_id}/events",
                token=token,
            )
            first_reviews = _http_json(
                f"http://127.0.0.1:13000/api/focusproof/sessions/{session_id}/reviews",
                token=token,
            )

            _run_checked(
                compose + ["restart", "agent-server"],
                cwd=context_dir,
                timeout=120,
                env=compose_env,
            )
            _run_checked(
                compose + ["up", "-d", "--wait", "agent-server"],
                cwd=context_dir,
                timeout=120,
                env=compose_env,
            )
            second_state = _http_json(
                f"http://127.0.0.1:13000/api/focusproof/sessions/{session_id}",
                token=token,
            )
            second_events = _http_json(
                f"http://127.0.0.1:13000/api/focusproof/sessions/{session_id}/events",
                token=token,
            )
            second_reviews = _http_json(
                f"http://127.0.0.1:13000/api/focusproof/sessions/{session_id}/reviews",
                token=token,
            )

            assert second_state["state"]["conversationId"] == first_state["state"]["conversationId"]
            assert [event["id"] for event in second_events["events"]] == [
                event["id"] for event in first_events["events"]
            ]
            assert [review["reviewId"] for review in second_reviews["reviews"]] == [
                review["reviewId"] for review in first_reviews["reviews"]
            ]
            _run_checked(
                compose + ["down", "--volumes", "--remove-orphans", "--timeout", "10"],
                cwd=context_dir,
                timeout=120,
                env=compose_env,
            )
    finally:
        subprocess.run(
            compose + ["down", "--volumes", "--remove-orphans", "--timeout", "10"],
            cwd=context_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            env=_safe_subprocess_env(compose_env),
        )
