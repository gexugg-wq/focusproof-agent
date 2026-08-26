from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from ipaddress import ip_address
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
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
LOCAL_OIDC_PUBLISHED_PORT = "18443"
LOCAL_FRONTEND_PUBLISHED_PORT = "13000"
LOCAL_OIDC_HOST_PORT = f"127.0.0.1:{LOCAL_OIDC_PUBLISHED_PORT}"
LOCAL_FRONTEND_HOST_PORT = f"127.0.0.1:{LOCAL_FRONTEND_PUBLISHED_PORT}"
LOCAL_USER_PASSWORD = "task4-local-user-password"
STAGING_PUBLISHED_PORT_DEFAULTS = {
    "FOCUSPROOF_STAGING_BACKEND_HOST_PORT": "18080",
    "FOCUSPROOF_STAGING_FRONTEND_HOST_PORT": LOCAL_FRONTEND_PUBLISHED_PORT,
    "FOCUSPROOF_STAGING_OIDC_HOST_PORT": LOCAL_OIDC_PUBLISHED_PORT,
}

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


def _canonical_release_snapshot_for_image(
    image: str,
    *,
    image_name: str,
    context_dir: Path,
    dockerfile: Path,
    output_dir: Path,
    env: dict[str, str],
) -> Any:
    import check_staging_reproducibility

    inspected = _run_checked(
        ["docker", "image", "inspect", image], cwd=context_dir, timeout=30, env=env
    )
    payload = json.loads(inspected.stdout)
    assert isinstance(payload, list) and len(payload) == 1
    image_data = payload[0]
    assert isinstance(image_data, dict)
    os_name = image_data.get("Os")
    architecture = image_data.get("Architecture")
    config = image_data.get("Config")
    assert isinstance(os_name, str) and isinstance(architecture, str)
    assert isinstance(config, dict)
    relevant_config = {
        key: config.get(key)
        for key in (
            "Entrypoint",
            "Cmd",
            "Env",
            "User",
            "WorkingDir",
            "Labels",
            "ExposedPorts",
            "Volumes",
            "StopSignal",
            "Healthcheck",
        )
    }
    normalization_profile = None
    if image_name == "frontend":
        package_lock = json.loads(
            (context_dir / "frontend/package-lock.json").read_text(encoding="utf-8")
        )
        assert package_lock["packages"]["node_modules/next"]["version"] == "15.5.21"
        normalization_profile = "next@15.5.21"
    descriptor = check_staging_reproducibility.ReleaseDescriptor(
        schema=check_staging_reproducibility.CANONICAL_RELEASE_SCHEMA,
        platform=f"{os_name}/{architecture}",
        pinned_base_images=check_staging_reproducibility.pinned_base_images_from_dockerfile(
            dockerfile
        ),
        runtime_path="/app",
        config=relevant_config,
        normalization_profile=normalization_profile,
    )
    container_id = _run_checked(
        ["docker", "create", image], cwd=context_dir, timeout=30, env=env
    ).stdout.strip()
    assert container_id
    rootfs_tar = output_dir / f"{image_name}-rootfs.tar"
    try:
        with rootfs_tar.open("wb") as output:
            exported = subprocess.run(
                ["docker", "export", container_id],
                cwd=context_dir,
                check=False,
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=180,
                env=_safe_subprocess_env(env),
            )
        assert exported.returncode == 0, exported.stderr.decode("utf-8", errors="replace")[-2000:]
        return check_staging_reproducibility.canonical_release_snapshot(rootfs_tar, descriptor)
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container_id],
            cwd=context_dir,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            env=_safe_subprocess_env(env),
        )


def _validate_staging_published_port(name: str, value: str) -> str:
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name} must be an integer published port")
    if not 1 <= int(value) <= 65535:
        raise ValueError(f"{name} must use a port from 1 through 65535")
    return value


def _staging_published_ports() -> dict[str, str]:
    return {
        name: _validate_staging_published_port(name, os.environ.get(name, default_value))
        for name, default_value in STAGING_PUBLISHED_PORT_DEFAULTS.items()
    }


def _disposable_compose_project_name(run_path: Path) -> str:
    suffix = sha256(os.fsencode(run_path.resolve())).hexdigest()[:12]
    return f"focusproof-ai4c-task4-{suffix}"


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
            LOCAL_USER_PASSWORD,
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
    check_ai4c_capabilities.require_staging_browser_capabilities()


def _require_reproducible_buildkit(builder: str) -> str:
    inspect = _run_checked(
        ["docker", "buildx", "inspect", "--bootstrap", builder],
        cwd=PROJECT_ROOT,
        timeout=120,
    )
    fields = {
        name.strip(): value.strip()
        for line in inspect.stdout.splitlines()
        if (separator := line.find(":")) >= 0
        for name, value in ((line[:separator], line[separator + 1 :]),)
    }
    driver = fields.get("Driver")
    assert driver is not None
    assert driver in {"docker", "docker-container"}
    assert fields.get("Status") == "running"
    assert fields.get("BuildKit version")
    return driver


def _build_staging_image(
    *,
    builder: str,
    driver: str,
    context_dir: Path,
    dockerfile: str,
    image: str,
    build_args: dict[str, str],
) -> None:
    command = [
        "docker",
        "buildx",
        "build",
        "--builder",
        builder,
        "--provenance=false",
        "--sbom=false",
        "--no-cache",
        "--pull",
        "--file",
        dockerfile,
        "--tag",
        image,
    ]
    if driver == "docker-container":
        command.append("--load")
    for name, value in build_args.items():
        command.extend(["--build-arg", f"{name}={value}"])
    command.append(str(context_dir))
    _run_checked(command, cwd=context_dir, timeout=1800, env={"SOURCE_DATE_EPOCH": "1735689600"})


def _safe_staging_image_snapshot(
    image: str, *, cwd: Path, env: dict[str, str]
) -> dict[str, object]:
    inspected = _run_checked(["docker", "image", "inspect", image], cwd=cwd, timeout=30, env=env)
    payload = json.loads(inspected.stdout)
    assert isinstance(payload, list) and len(payload) == 1
    image_data = payload[0]
    assert isinstance(image_data, dict)

    image_id = image_data.get("Id")
    created = image_data.get("Created")
    rootfs = image_data.get("RootFS")
    config = image_data.get("Config")
    assert isinstance(image_id, str) and image_id.startswith("sha256:")
    assert isinstance(created, str)
    assert isinstance(rootfs, dict)
    assert isinstance(config, dict)

    layers = rootfs.get("Layers")
    assert isinstance(layers, list) and all(isinstance(layer, str) for layer in layers)

    def nullable_command(value: object) -> list[str] | None:
        assert value is None or (
            isinstance(value, list) and all(isinstance(item, str) for item in value)
        )
        return value

    env_values = config.get("Env")
    assert isinstance(env_values, list) and all(isinstance(value, str) for value in env_values)
    user = config.get("User")
    assert user is None or isinstance(user, str)

    return {
        "Id": image_id,
        "Created": created,
        "RootFS": {"Layers": list(layers)},
        "Config": {
            "Entrypoint": nullable_command(config.get("Entrypoint")),
            "Cmd": nullable_command(config.get("Cmd")),
            "EnvNames": sorted(value.partition("=")[0] for value in env_values),
            "User": user,
        },
    }


def _git_index_archive_context(tmp_path: Path, round_number: int) -> Path:
    tree = _run_checked(["git", "write-tree"], cwd=PROJECT_ROOT, timeout=10).stdout.strip()
    assert tree
    archive_path = tmp_path / f"round-{round_number}-source.tar"
    with archive_path.open("wb") as archive_file:
        subprocess.run(
            ["git", "archive", "--format=tar", "--mtime=1735689600", tree],
            cwd=PROJECT_ROOT,
            check=True,
            stdout=archive_file,
            stderr=subprocess.PIPE,
            timeout=20,
            env=_safe_subprocess_env(),
        )
    context_dir = tmp_path / f"round-{round_number}-context"
    context_dir.mkdir()
    with tarfile.open(archive_path) as archive:
        archive.extractall(context_dir, filter="data")

    epoch_ns = 1735689600 * 1_000_000_000
    for root, directory_names, file_names in os.walk(context_dir, topdown=False, followlinks=False):
        for name in (*file_names, *directory_names):
            os.utime(
                Path(root) / name,
                ns=(epoch_ns, epoch_ns),
                follow_symlinks=False,
            )
    os.utime(
        context_dir,
        ns=(epoch_ns, epoch_ns),
        follow_symlinks=False,
    )
    return context_dir


def _write_local_issuer_materials(tmp_path: Path) -> Path:
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
    (secrets_dir / "oidc_admin_password").write_text(
        "task4-local-admin-password",
        encoding="utf-8",
    )
    (secrets_dir / "oidc_test_user_password").write_text(
        LOCAL_USER_PASSWORD,
        encoding="utf-8",
    )
    (secrets_dir / "focusproof-realm.json").write_text(
        json.dumps(
            {
                "realm": "focusproof",
                "enabled": True,
                "sslRequired": "none",
                "clients": [
                    {
                        "clientId": "focusproof-staging",
                        "enabled": True,
                        "publicClient": True,
                        "standardFlowEnabled": True,
                        "directAccessGrantsEnabled": False,
                        "redirectUris": [f"http://{LOCAL_FRONTEND_HOST_PORT}/*"],
                        "webOrigins": [f"http://{LOCAL_FRONTEND_HOST_PORT}"],
                        "protocolMappers": [
                            {
                                "name": "focusproof-api-audience",
                                "protocol": "openid-connect",
                                "protocolMapper": "oidc-audience-mapper",
                                "config": {
                                    "included.client.audience": "focusproof-api",
                                    "included.custom.audience": "focusproof-api",
                                    "access.token.claim": "true",
                                },
                            }
                        ],
                    }
                ],
                "users": [
                    {
                        "username": "learner",
                        "enabled": True,
                        "email": "learner@focusproof.test",
                        "emailVerified": True,
                        "firstName": "Focus",
                        "lastName": "Proof",
                        "credentials": [
                            {
                                "type": "password",
                                "value": LOCAL_USER_PASSWORD,
                                "temporary": False,
                            }
                        ],
                    }
                ],
            },
            sort_keys=True,
        ),
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
            x509.SubjectAlternativeName(
                [x509.DNSName("oidc-provider"), x509.IPAddress(ip_address("127.0.0.1"))]
            ),
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

    return secrets_dir


def _compose_env(secrets_dir: Path) -> dict[str, str]:
    published_ports = _staging_published_ports()
    oidc_port = published_ports["FOCUSPROOF_STAGING_OIDC_HOST_PORT"]
    frontend_port = published_ports["FOCUSPROOF_STAGING_FRONTEND_HOST_PORT"]
    return {
        **published_ports,
        "FOCUSPROOF_STAGING_POSTGRES_PASSWORD_FILE": str(secrets_dir / "postgres_password"),
        "FOCUSPROOF_STAGING_OIDC_FINGERPRINT_KEY_FILE": str(secrets_dir / "oidc_fingerprint_key"),
        "FOCUSPROOF_STAGING_OIDC_REALM_FILE": str(secrets_dir / "focusproof-realm.json"),
        "FOCUSPROOF_STAGING_OIDC_ADMIN_PASSWORD_FILE": str(secrets_dir / "oidc_admin_password"),
        "FOCUSPROOF_STAGING_OIDC_TEST_USER_PASSWORD_FILE": str(
            secrets_dir / "oidc_test_user_password"
        ),
        "FOCUSPROOF_STAGING_OIDC_TLS_CERT_FILE": str(secrets_dir / "oidc_tls_cert.pem"),
        "FOCUSPROOF_STAGING_OIDC_TLS_KEY_FILE": str(secrets_dir / "oidc_tls_key.pem"),
        "FOCUSPROOF_STAGING_OIDC_CA_FILE": str(secrets_dir / "oidc_ca.pem"),
        "NEXT_PUBLIC_OIDC_ISSUER": f"https://127.0.0.1:{oidc_port}/realms/focusproof",
        "NEXT_PUBLIC_OIDC_CLIENT_ID": "focusproof-staging",
        "NEXT_PUBLIC_OIDC_AUDIENCE": "focusproof-api",
        "NEXT_PUBLIC_OIDC_REDIRECT_URI": f"http://127.0.0.1:{frontend_port}/",
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


def test_runtime_identity_provisioning_normalizes_exact_generated_paths() -> None:
    backend_dockerfile = _read_required(DEPLOY_DIR / "agent-server.Dockerfile")
    frontend_dockerfile = _read_required(DEPLOY_DIR / "frontend.Dockerfile")

    backend_expected = (
        'touch -h -d "@${SOURCE_DATE_EPOCH}" \\\n'
        "        /etc /etc/group /etc/gshadow /etc/passwd /etc/shadow /app /app/var"
    )
    frontend_expected = (
        'touch -h -d "@${SOURCE_DATE_EPOCH}" \\\n'
        "        /etc /etc/group /etc/gshadow /etc/passwd /etc/shadow"
    )

    assert backend_expected in backend_dockerfile
    assert frontend_expected in frontend_dockerfile
    for dockerfile in (backend_dockerfile, frontend_dockerfile):
        assert "USER focusproof" in dockerfile
        assert "find /etc" not in dockerfile


def test_frontend_oidc_metadata_is_required_before_next_build() -> None:
    dockerfile = _read_required(DEPLOY_DIR / "frontend.Dockerfile")
    build_prefix = dockerfile[: dockerfile.index("npm run build")]

    for name in (
        "NEXT_PUBLIC_OIDC_ISSUER",
        "NEXT_PUBLIC_OIDC_CLIENT_ID",
        "NEXT_PUBLIC_OIDC_AUDIENCE",
        "NEXT_PUBLIC_OIDC_REDIRECT_URI",
    ):
        assert f"ARG {name}" in build_prefix
        assert name in build_prefix
    assert "Invalid public OIDC build configuration" in build_prefix


def test_compose_builds_frontend_with_required_public_oidc_metadata() -> None:
    compose = _load_compose()
    frontend = _service(compose, "frontend")
    build = frontend.get("build")
    assert isinstance(build, dict)
    args = build.get("args")
    assert isinstance(args, dict)
    assert args == {
        "SOURCE_DATE_EPOCH": "1735689600",
        "NEXT_PUBLIC_OIDC_ISSUER": "${NEXT_PUBLIC_OIDC_ISSUER:?required}",
        "NEXT_PUBLIC_OIDC_CLIENT_ID": "${NEXT_PUBLIC_OIDC_CLIENT_ID:?required}",
        "NEXT_PUBLIC_OIDC_AUDIENCE": "${NEXT_PUBLIC_OIDC_AUDIENCE:?required}",
        "NEXT_PUBLIC_OIDC_REDIRECT_URI": "${NEXT_PUBLIC_OIDC_REDIRECT_URI:?required}",
    }
    runtime_environment = yaml.safe_dump(frontend.get("environment"))
    assert "NEXT_PUBLIC_OIDC_" not in runtime_environment


def test_staging_images_fix_build_epoch_and_deterministic_next_build_id() -> None:
    backend_dockerfile = _read_required(DEPLOY_DIR / "agent-server.Dockerfile")
    frontend_dockerfile = _read_required(DEPLOY_DIR / "frontend.Dockerfile")
    compose = _load_compose()
    next_config = _read_required(PROJECT_ROOT / "frontend" / "next.config.mjs")

    for dockerfile in (backend_dockerfile, frontend_dockerfile):
        assert "ARG SOURCE_DATE_EPOCH=1735689600" in dockerfile
        assert "SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}" in dockerfile

    assert "find /app/.venv" in backend_dockerfile
    assert "find /app/node_modules" in frontend_dockerfile
    assert "find /app/.next" in frontend_dockerfile

    backend_build = _service(compose, "agent-server").get("build")
    frontend_build = _service(compose, "frontend").get("build")
    assert isinstance(backend_build, dict)
    assert isinstance(frontend_build, dict)
    assert backend_build.get("args", {}).get("SOURCE_DATE_EPOCH") == "1735689600"
    assert frontend_build.get("args", {}).get("SOURCE_DATE_EPOCH") == "1735689600"
    assert "generateBuildId" in next_config
    assert "SOURCE_DATE_EPOCH" in next_config


def test_staging_images_normalize_only_copy_affected_app_inputs_and_dependencies() -> None:
    backend_dockerfile = _read_required(DEPLOY_DIR / "agent-server.Dockerfile")
    frontend_dockerfile = _read_required(DEPLOY_DIR / "frontend.Dockerfile")

    assert "find /usr/" not in backend_dockerfile
    for required in (
        "--mount=type=bind,source=requirements/production.lock",
        "python -m venv /app/.venv",
        'find /app/.venv -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} +',
        "--mount=type=bind,source=alembic.ini",
        "--mount=type=bind,source=agent-server/focusproof",
        "--mount=type=bind,source=agent-server/migrations",
    ):
        assert required in backend_dockerfile

    for required in (
        "--mount=type=bind,source=frontend/package.json",
        "--mount=type=bind,source=frontend,target=/mnt/input/frontend",
        "--mount=type=bind,from=build,source=/app/.next",
        'find /app/node_modules -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} +',
        'find /app/.next -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} +',
    ):
        assert required in frontend_dockerfile

    for dockerfile in (backend_dockerfile, frontend_dockerfile):
        assert "find /etc" not in dockerfile
        assert "/usr/" not in dockerfile
        assert "\nCOPY " not in dockerfile


def test_frontend_runtime_removes_build_tool_caches_and_normalizes_empty_action_manifest() -> None:
    dockerfile = _read_required(DEPLOY_DIR / "frontend.Dockerfile")

    assert "rm -rf /root/.npm /tmp/node-compile-cache" in dockerfile
    assert "scripts/normalize_next_empty_action_manifest.mjs" in dockerfile
    assert "/app/.next/server/server-reference-manifest.json" in dockerfile


def test_clean_builds_do_not_seed_unused_next_features_or_accept_build_secrets() -> None:
    backend_dockerfile = _read_required(DEPLOY_DIR / "agent-server.Dockerfile")
    frontend_dockerfile = _read_required(DEPLOY_DIR / "frontend.Dockerfile")
    compose = _load_compose()

    assert "--mount=type=bind,source=requirements/production.lock" in backend_dockerfile
    assert "--mount=type=bind,source=agent-server/focusproof" in backend_dockerfile
    assert "--mount=type=bind,source=frontend/package.json" in frontend_dockerfile
    assert "--mount=type=bind,source=frontend,target=/mnt/input/frontend" in frontend_dockerfile
    assert "--mount=type=secret" not in frontend_dockerfile
    assert "next_" + "build_keys" not in frontend_dockerfile
    assert ".previewinfo" not in frontend_dockerfile
    assert ".rscinfo" not in frontend_dockerfile
    assert "rm -rf /app/.next/cache /app/.next/trace" in frontend_dockerfile
    assert "--canonicalize-build-manifests /app/.next" in frontend_dockerfile
    assert "Object.entries(value).sort" not in frontend_dockerfile
    assert "ARG NEXT_SERVER_ACTIONS_ENCRYPTION_KEY" not in frontend_dockerfile
    assert "ENV NEXT_SERVER_ACTIONS_ENCRYPTION_KEY" not in frontend_dockerfile

    frontend_build = _service(compose, "frontend").get("build")
    assert isinstance(frontend_build, dict)
    assert "secrets" not in frontend_build
    assert "next_" + "build_keys" not in compose.get("secrets", {})


def test_frontend_source_does_not_use_draft_mode_or_server_actions() -> None:
    source_files = [
        path
        for path in (PROJECT_ROOT / "frontend").rglob("*")
        if path.is_file()
        and path.suffix in {".js", ".jsx", ".mjs", ".ts", ".tsx"}
        and not ({"node_modules", ".next", "test-results"} & set(path.parts))
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
    assert "draftMode" not in source
    assert '"use server"' not in source
    assert "'use server'" not in source


def test_staging_configuration_has_no_next_build_key_plumbing() -> None:
    artifacts = (
        PROJECT_ROOT / ".env.example",
        DEPLOY_DIR / "compose.staging.yml",
        PROJECT_ROOT / "docs/deployment/AI4C_STAGING.md",
    )
    combined = "\n".join(_read_required(path) for path in artifacts)
    assert "NEXT_" + "BUILD_KEYS" not in combined
    assert "next_" + "build_keys" not in combined


def test_external_reproducibility_gate_requires_buildx_without_attestations() -> None:
    source = _read_required(Path(__file__))
    external_gate = source[source.rindex("@pytest.mark.staging_external") :]
    buildkit_helper = source[
        source.index("def _require_reproducible_buildkit") : source.index(
            "def _build_staging_image"
        )
    ]
    build_helper = source[
        source.index("def _build_staging_image") : source.index("def _git_index_archive_context")
    ]

    for required in (
        '"docker", "buildx", "inspect", "--bootstrap"',
        'driver in {"docker", "docker-container"}',
        'fields.get("Status") == "running"',
    ):
        assert required in buildkit_helper
    for required in (
        '"docker",',
        '"buildx",',
        '"build",',
        '"--load"',
        '"--provenance=false"',
        '"--sbom=false"',
    ):
        assert required in build_helper
    for required in (
        '"FOCUSPROOF_STAGING_BUILDER": "default"',
        '"SOURCE_DATE_EPOCH": "1735689600"',
        '"{{.Id}}"',
        "_canonical_release_snapshot_for_image(",
    ):
        assert required in external_gate


def test_strict_external_reproducibility_gate_has_two_independent_rounds_and_no_bypass() -> None:
    source = _read_required(Path(__file__))
    external_gate = source[source.rindex("@pytest.mark.staging_external") :]
    build_helper = source[
        source.index("def _build_staging_image") : source.index("def _git_index_archive_context")
    ]

    round_loop = "for round_number in range(1, 3):"
    archive_call = "context_dir = _git_index_archive_context(tmp_path, round_number)"
    assert round_loop in external_gate
    assert archive_call in external_gate
    assert external_gate.index(round_loop) < external_gate.index(archive_call)
    assert "FOCUSPROOF_STAGING_DIAGNOSTIC_ROUNDS" not in external_gate
    assert "round_count" not in external_gate
    assert "if round_count == 2" not in external_gate
    assert "round_snapshots: list[dict[str, dict[str, object]]] = []" in external_gate
    assert "round_canonical_digests: list[dict[str, str]] = []" in external_gate
    assert "*round_canonical_digests" in external_gate
    assert "round_snapshots.append(" in external_gate
    assert "round_one_snapshots=round_snapshots[0]" in external_gate
    assert "round_two_snapshots=round_snapshots[1]" in external_gate
    assert "for name, image in image_names.items()" in external_gate
    assert '"--no-cache"' in build_helper
    assert '"--pull"' in build_helper
    assert '"--rmi", "local"' in external_gate
    assert external_gate.count("_build_staging_image(") == 2
    assert external_gate.index("_build_staging_image(") > external_gate.index(archive_call)
    assert external_gate.index("finally:") > external_gate.index("_build_staging_image(")
    cleanup = external_gate[external_gate.index("finally:") :]
    assert cleanup.index("round_snapshots.append(") < cleanup.index('"down"')

    archive_helper = source[
        source.index("def _git_index_archive_context") : source.index(
            "def _write_local_issuer_materials"
        )
    ]
    assert "round_number: int" in archive_helper
    assert 'f"round-{round_number}-source.tar"' in archive_helper
    assert 'f"round-{round_number}-context"' in archive_helper
    assert '"--mtime=1735689600"' in archive_helper


def test_git_index_archive_context_normalizes_all_extracted_mtimes(
    tmp_path: Path,
) -> None:
    first = _git_index_archive_context(tmp_path, 1)
    second = _git_index_archive_context(tmp_path, 2)
    epoch_ns = 1735689600 * 1_000_000_000

    for relative in (
        Path("frontend"),
        Path("frontend/package.json"),
        Path("requirements"),
        Path("requirements/production.lock"),
    ):
        assert (first / relative).stat(follow_symlinks=False).st_mtime_ns == epoch_ns
        assert (second / relative).stat(follow_symlinks=False).st_mtime_ns == epoch_ns

    source = _read_required(Path(__file__))
    archive_helper = source[
        source.index("def _git_index_archive_context") : source.index(
            "def _write_local_issuer_materials"
        )
    ]
    assert "os.walk(" in archive_helper
    assert "context_dir, topdown=False, followlinks=False" in archive_helper
    assert "followlinks=False" in archive_helper
    assert "follow_symlinks=False" in archive_helper


def test_compose_reuses_a_fixed_mature_oidc_code_pkce_provider() -> None:
    compose = _load_compose()
    provider = _service(compose, "oidc-provider")
    image = str(provider.get("image", ""))

    assert "keycloak" in image.lower()
    assert "@sha256:" in image
    assert ":latest" not in image
    assert "python -c" not in _flatten_command(provider.get("command"))
    assert provider.get("healthcheck")


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
    oidc_provider = _service(compose, "oidc-provider")
    backend = _service(compose, "agent-server")
    frontend = _service(compose, "frontend")

    assert postgres.get("ports") in (None, [])
    assert "focusproof-postgres-data" in str(postgres.get("volumes", ""))
    assert "focusproof-openhands-data" in str(backend.get("volumes", ""))
    assert _host_ports(backend) == [
        "127.0.0.1:${FOCUSPROOF_STAGING_BACKEND_HOST_PORT:?required}:8000"
    ]
    assert _host_ports(frontend) == [
        "127.0.0.1:${FOCUSPROOF_STAGING_FRONTEND_HOST_PORT:?required}:3000"
    ]
    assert _host_ports(oidc_provider) == [
        "127.0.0.1:${FOCUSPROOF_STAGING_OIDC_HOST_PORT:?required}:8443"
    ]
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

    for base_setting in (
        "FOCUSPROOF_PROFILE=local-dev",
        "DATABASE_URL=sqlite+pysqlite:///./var/focusproof.db",
        "FOCUSPROOF_DATA_DIR=./var",
        "LITELLM_LOCAL_MODEL_COST_MAP=true",
        "NEXT_PUBLIC_OIDC_ISSUER=",
    ):
        assert base_setting in example

    for name in (
        "FOCUSPROOF_STAGING_BACKEND_HOST_PORT",
        "FOCUSPROOF_STAGING_FRONTEND_HOST_PORT",
        "FOCUSPROOF_STAGING_POSTGRES_PASSWORD_FILE",
        "FOCUSPROOF_STAGING_OIDC_FINGERPRINT_KEY_FILE",
        "FOCUSPROOF_STAGING_OIDC_REALM_FILE",
        "FOCUSPROOF_STAGING_OIDC_ADMIN_PASSWORD_FILE",
        "FOCUSPROOF_STAGING_OIDC_TEST_USER_PASSWORD_FILE",
        "FOCUSPROOF_STAGING_OIDC_TLS_CERT_FILE",
        "FOCUSPROOF_STAGING_OIDC_TLS_KEY_FILE",
        "FOCUSPROOF_STAGING_OIDC_CA_FILE",
    ):
        assert name in example
    for setting in (
        "FOCUSPROOF_STAGING_BACKEND_HOST_PORT=18080",
        "FOCUSPROOF_STAGING_FRONTEND_HOST_PORT=13000",
        "FOCUSPROOF_STAGING_OIDC_HOST_PORT=18443",
    ):
        assert setting in example
    assert "password-goes-here" not in example.lower()


def test_staging_deployment_documentation_matches_compose_oidc_and_secret_boundaries() -> None:
    document = _read_required(PROJECT_ROOT / "docs/deployment/AI4C_STAGING.md")

    for required in (
        "ASCII decimal",
        "1..65535",
        "not host:port",
        "127.0.0.1",
        "PostgreSQL has no published host port",
        "NEXT_PUBLIC_OIDC_ISSUER",
        "NEXT_PUBLIC_OIDC_CLIENT_ID",
        "NEXT_PUBLIC_OIDC_AUDIENCE",
        "NEXT_PUBLIC_OIDC_REDIRECT_URI",
        "build args",
        "public, non-secret",
        "start --import-realm",
        "FOCUSPROOF_STAGING_OIDC_REALM_FILE",
        "FOCUSPROOF_STAGING_OIDC_ADMIN_PASSWORD_FILE",
        "FOCUSPROOF_STAGING_OIDC_TEST_USER_PASSWORD_FILE",
        "Docker secrets",
        "FOCUSPROOF_STAGING_OIDC_TLS_CERT_FILE",
        "FOCUSPROOF_STAGING_OIDC_TLS_KEY_FILE",
        "FOCUSPROOF_STAGING_OIDC_CA_FILE",
        "trust this local CA",
        "Do not disable TLS verification",
        "issuer, audience, JWKS URI, and RS256",
        "runtime public endpoint",
        "does not generate or mount a signing JWKS file",
        "Code+PKCE",
        "not Python hand-crafted Bearer tokens",
        "realm `focusproof`",
        "focusproof-staging-admin",
        "test user `learner`",
        "temporary Chromium/NSS profile",
        "never ignores certificate errors",
        "one FastAPI worker",
        "focusproof-openhands-data",
        "Task 5",
    ):
        assert required in document
    assert "Generate the PostgreSQL password, OIDC fingerprint key, signing JWKS" not in document


@pytest.mark.parametrize("published_port", ("1", "18080", "65535"))
def test_strict_compose_env_accepts_only_numeric_published_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published_port: str
) -> None:
    expected = {
        "FOCUSPROOF_STAGING_BACKEND_HOST_PORT": published_port,
        "FOCUSPROOF_STAGING_FRONTEND_HOST_PORT": published_port,
        "FOCUSPROOF_STAGING_OIDC_HOST_PORT": published_port,
    }

    for name, value in expected.items():
        monkeypatch.setenv(name, value)

    compose_env = _compose_env(tmp_path)

    assert {name: compose_env[name] for name in expected} == expected


@pytest.mark.parametrize(
    "invalid_value",
    (
        "0.0.0.0:18080",
        "127.0.0.1:18080",
        "[::1]:18080",
        "localhost:18080",
        ":18080",
        "192.168.1.2:18080",
        "8.8.8.8:18080",
        "::1:18080",
        "0",
        "65536",
        "not-a-port",
    ),
)
@pytest.mark.parametrize(
    "host_port_name",
    (
        "FOCUSPROOF_STAGING_BACKEND_HOST_PORT",
        "FOCUSPROOF_STAGING_FRONTEND_HOST_PORT",
        "FOCUSPROOF_STAGING_OIDC_HOST_PORT",
    ),
)
def test_strict_compose_env_rejects_hosts_or_invalid_ports_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_port_name: str,
    invalid_value: str,
) -> None:
    subprocess_calls: list[object] = []

    def fail_if_called(*args: object, **kwargs: object) -> None:
        subprocess_calls.append((args, kwargs))
        raise AssertionError("invalid staging host port reached a subprocess")

    monkeypatch.setattr(subprocess, "run", fail_if_called)

    monkeypatch.setenv(host_port_name, invalid_value)

    with pytest.raises(ValueError, match=host_port_name):
        _compose_env(tmp_path)

    assert subprocess_calls == []


def test_staging_host_port_security_contract_validates_before_strict_external_commands() -> None:
    source = _read_required(Path(__file__))
    compose = _load_compose()
    external_gate = source[source.rindex("@pytest.mark.staging_external") :]
    validator = source[
        : source.index(
            "def test_static_backend_image_is_pinned_hash_locked_and_non_root_single_worker"
        )
    ]

    for name, container_port in (
        ("FOCUSPROOF_STAGING_BACKEND_HOST_PORT", "8000"),
        ("FOCUSPROOF_STAGING_FRONTEND_HOST_PORT", "3000"),
        ("FOCUSPROOF_STAGING_OIDC_HOST_PORT", "8443"),
    ):
        assert "def _validate_staging_published_port" in validator
        assert name in validator
        service = {
            "8000": "agent-server",
            "3000": "frontend",
            "8443": "oidc-provider",
        }[container_port]
        assert _host_ports(_service(compose, service)) == [
            f"127.0.0.1:${{{name}:?required}}:{container_port}"
        ]
    assert "value.isdecimal()" in validator
    assert "127.0.0.1" in validator
    assert external_gate.index("compose_env = _compose_env(secrets_dir)") < external_gate.index(
        "subprocess.run("
    )
    assert _service(compose, "postgres").get("ports") in (None, [])


def test_compose_consumes_host_port_overrides_and_fails_closed_without_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compose_file = DEPLOY_DIR / "compose.staging.yml"
    required = {
        "FOCUSPROOF_STAGING_POSTGRES_PASSWORD_FILE": str(tmp_path / "postgres"),
        "FOCUSPROOF_STAGING_CLAMD_ENDPOINT": "tcp://127.0.0.1:3310",
        "FOCUSPROOF_STAGING_OIDC_FINGERPRINT_KEY_FILE": str(tmp_path / "fingerprint"),
        "FOCUSPROOF_STAGING_OIDC_TLS_CERT_FILE": str(tmp_path / "cert"),
        "FOCUSPROOF_STAGING_OIDC_TLS_KEY_FILE": str(tmp_path / "key"),
        "FOCUSPROOF_STAGING_OIDC_CA_FILE": str(tmp_path / "ca"),
        "FOCUSPROOF_STAGING_OIDC_REALM_FILE": str(tmp_path / "realm"),
        "FOCUSPROOF_STAGING_OIDC_ADMIN_PASSWORD_FILE": str(tmp_path / "admin"),
        "FOCUSPROOF_STAGING_OIDC_TEST_USER_PASSWORD_FILE": str(tmp_path / "user"),
        "NEXT_PUBLIC_OIDC_ISSUER": "https://127.0.0.1:19443/realms/focusproof",
        "NEXT_PUBLIC_OIDC_CLIENT_ID": "focusproof-staging",
        "NEXT_PUBLIC_OIDC_AUDIENCE": "focusproof-api",
        "NEXT_PUBLIC_OIDC_REDIRECT_URI": "http://127.0.0.1:14000/learning",
        "FOCUSPROOF_STAGING_OIDC_HOST_PORT": "19443",
    }
    host_port_overrides = {
        "FOCUSPROOF_STAGING_BACKEND_HOST_PORT": "19080",
        "FOCUSPROOF_STAGING_FRONTEND_HOST_PORT": "14000",
        "FOCUSPROOF_STAGING_OIDC_HOST_PORT": "19443",
    }
    for name, value in host_port_overrides.items():
        monkeypatch.setenv(name, value)
    validated_compose_env = _compose_env(tmp_path)
    assert {
        name: validated_compose_env[name] for name in host_port_overrides
    } == host_port_overrides
    assert validated_compose_env["NEXT_PUBLIC_OIDC_ISSUER"] == (
        "https://127.0.0.1:19443/realms/focusproof"
    )
    assert validated_compose_env["NEXT_PUBLIC_OIDC_REDIRECT_URI"] == "http://127.0.0.1:14000/"

    overridden = _run_checked(
        ["docker", "compose", "-f", str(compose_file), "config", "--format", "json"],
        cwd=PROJECT_ROOT,
        timeout=30,
        env={
            **required,
            **host_port_overrides,
        },
    )
    rendered = json.loads(overridden.stdout)
    assert rendered["services"]["agent-server"]["ports"][0]["published"] == "19080"
    assert rendered["services"]["frontend"]["ports"][0]["published"] == "14000"
    assert rendered["services"]["agent-server"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert rendered["services"]["frontend"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert rendered["services"]["oidc-provider"]["ports"][0]["host_ip"] == "127.0.0.1"

    direct_host_string = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "config"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=_safe_subprocess_env(
            {
                **required,
                **host_port_overrides,
                "FOCUSPROOF_STAGING_BACKEND_HOST_PORT": "0.0.0.0:19080",
            }
        ),
    )
    assert direct_host_string.returncode != 0

    failed = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "config"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=_safe_subprocess_env(required),
    )
    assert failed.returncode != 0
    assert any(name in failed.stderr for name in host_port_overrides)


def test_demo_and_staging_test_llm_factories_expose_explicit_official_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from focusproof.api import app as app_module
    from openhands.sdk.testing import TestLLM

    monkeypatch.setenv("FOCUSPROOF_DATA_DIR", str(tmp_path))

    staging_llm = app_module.staging_test_llm("staging-contract")
    demo_llm = app_module.demo_deterministic_test_llm("demo-contract")

    assert isinstance(staging_llm, TestLLM)
    assert isinstance(demo_llm, TestLLM)
    assert staging_llm is not demo_llm

    first_staging = staging_llm.completion([]).message
    second_staging = staging_llm.completion([]).message
    first_demo = demo_llm.completion([], tools=[]).message

    assert first_staging.tool_calls[0].name == "focusproof_learner_input"
    assert second_staging.tool_calls[0].name == "focusproof_review_draft"
    assert first_demo.tool_calls[0].name == "focusproof_learner_input"


def test_staging_test_llm_selects_recovery_script_from_native_sdk_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staging-only TestLLM must not replay learner input after native restore."""
    from focusproof.api import app as app_module
    from openhands.sdk.conversation import LocalConversation

    session_id = "staging-native-recovery-contract"
    monkeypatch.setenv("FOCUSPROOF_DATA_DIR", str(tmp_path))

    first_response = app_module.staging_test_llm(session_id).completion([]).message
    assert first_response.tool_calls[0].name == "focusproof_learner_input"

    conversation_id = uuid5(NAMESPACE_URL, f"focusproof:{session_id}")
    persistence_base = tmp_path / "conversations" / session_id / "persistence"
    native_store = Path(LocalConversation.get_persistence_dir(persistence_base, conversation_id))
    native_store.mkdir(parents=True)
    # SDK 1.31.0 exposes the directory derivation but no public restored-state
    # predicate. This is deliberately native persistence, never a SQL projection.
    (native_store / "base_state.json").write_text("{}", encoding="utf-8")

    restored_response = app_module.staging_test_llm(session_id).completion([]).message
    assert restored_response.tool_calls[0].name == "focusproof_review_draft"


def test_staging_test_llm_keeps_follow_up_review_draft_in_same_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from focusproof.api import app as app_module

    session_id = "staging-native-follow-up-contract"
    monkeypatch.setenv("FOCUSPROOF_DATA_DIR", str(tmp_path))

    llm = app_module.staging_test_llm(session_id)
    first_response = llm.completion([]).message
    second_response = llm.completion([]).message

    assert first_response.tool_calls[0].name == "focusproof_learner_input"
    assert second_response.tool_calls[0].name == "focusproof_review_draft"


def test_reproducibility_comparison_rejects_cross_round_digest_drift() -> None:
    import check_staging_reproducibility

    stable = {
        "agent-server": "sha256:" + "a" * 64,
        "frontend": "sha256:" + "b" * 64,
    }
    check_staging_reproducibility.compare_round_digests(stable, dict(stable))
    with pytest.raises(check_staging_reproducibility.ReproducibilityError):
        check_staging_reproducibility.compare_round_digests(
            stable,
            {**stable, "frontend": "sha256:" + "c" * 64},
        )


def test_reproducibility_comparison_reports_secret_safe_first_layer_divergence() -> None:
    import check_staging_reproducibility

    round_one = {
        "agent-server": "sha256:" + "a" * 64,
        "frontend": "sha256:" + "b" * 64,
    }
    round_two = {
        "agent-server": "sha256:" + "c" * 64,
        "frontend": "sha256:" + "b" * 64,
    }
    round_one_snapshots = {
        "agent-server": {
            "Id": round_one["agent-server"],
            "Created": "2025-01-01T00:00:00Z",
            "RootFS": {"Layers": ["sha256:layer-a", "sha256:layer-b"]},
            "Config": {"Cmd": ["serve"], "Entrypoint": None, "EnvNames": ["PATH"]},
        },
        "frontend": {
            "Id": round_one["frontend"],
            "Created": "2025-01-01T00:00:00Z",
            "RootFS": {"Layers": ["sha256:frontend"]},
            "Config": {"Cmd": ["start"], "Entrypoint": None, "EnvNames": ["PATH"]},
        },
    }
    round_two_snapshots = {
        **round_one_snapshots,
        "agent-server": {
            **round_one_snapshots["agent-server"],
            "Id": round_two["agent-server"],
            "RootFS": {"Layers": ["sha256:layer-a", "sha256:layer-c"]},
        },
    }

    with pytest.raises(check_staging_reproducibility.ReproducibilityError) as exc_info:
        check_staging_reproducibility.compare_round_digests(
            round_one,
            round_two,
            round_one_snapshots=round_one_snapshots,
            round_two_snapshots=round_two_snapshots,
        )

    diagnostic = str(exc_info.value)
    assert "agent-server" in diagnostic
    assert "Created equal" in diagnostic
    assert "RootFS.Layers first differs at index 1" in diagnostic
    assert "sha256:layer-b" in diagnostic
    assert "sha256:layer-c" in diagnostic
    assert "Config identical" in diagnostic

    check_staging_reproducibility.compare_round_digests(
        round_one,
        dict(round_one),
        round_one_snapshots=round_one_snapshots,
        round_two_snapshots=round_one_snapshots,
    )


def test_staging_image_snapshot_keeps_only_secret_safe_forensic_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    inspect_payload = [
        {
            "Id": "sha256:" + "a" * 64,
            "Created": "2025-01-01T00:00:00Z",
            "RootFS": {"Layers": ["sha256:base", "sha256:app"]},
            "Config": {
                "Entrypoint": ["python", "-m", "focusproof"],
                "Cmd": ["serve"],
                "Env": ["PATH=/usr/bin", "API_SECRET=must-not-leak"],
                "User": "focusproof",
                "Labels": {"unsafe": "must-not-leak"},
            },
            "ContainerConfig": {"Env": ["HISTORICAL_SECRET=must-not-leak"]},
            "RepoTags": ["focusproof-agent-server:staging"],
        }
    ]

    def inspect(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["docker", "image", "inspect"],
            0,
            stdout=json.dumps(inspect_payload),
            stderr="",
        )

    monkeypatch.setattr(sys.modules[__name__], "_run_checked", inspect)

    snapshot = _safe_staging_image_snapshot(
        "focusproof-agent-server:staging", cwd=tmp_path, env={"NODE_ENV": "test"}
    )

    assert snapshot == {
        "Id": "sha256:" + "a" * 64,
        "Created": "2025-01-01T00:00:00Z",
        "RootFS": {"Layers": ["sha256:base", "sha256:app"]},
        "Config": {
            "Entrypoint": ["python", "-m", "focusproof"],
            "Cmd": ["serve"],
            "EnvNames": ["API_SECRET", "PATH"],
            "User": "focusproof",
        },
    }
    assert "must-not-leak" not in json.dumps(snapshot)


def test_staging_browser_recovery_contract_preserves_projection_and_review_identity() -> None:
    e2e = _read_required(PROJECT_ROOT / "frontend/e2e/ai4c-staging.spec.ts")
    api_client = _read_required(PROJECT_ROOT / "frontend/lib/api/client.ts")
    workspace = _read_required(PROJECT_ROOT / "frontend/features/session/SessionWorkspace.tsx")

    for required in (
        "type ReviewProjection",
        "const sessionResponses: BffResponse[] = []",
        "let latestReviews: ReviewProjection[] = []",
        'url.endsWith("/reviews")',
        "const beforeProductEventIds",
        "const beforeSourceOpenHandsEventIds",
        "const postRecoveryProductEventIds",
        "const postRecoverySourceOpenHandsEventIds",
        "const postRecoverySessionResponse",
        "const reviewId",
        "const secondRestartProductEventIds",
        "const secondRestartSourceOpenHandsEventIds",
        "await page.reload()",
        "preProductEventCount",
        "postRecoveryProductEventCount",
        "secondRestartProductEventCount",
        "reviewId,",
    ):
        assert required in e2e
    assert e2e.count("restartBackend();") == 2
    assert e2e.index("restartBackend();") < e2e.index("submit answer")
    assert e2e.index("const reviewId") < e2e.rindex("restartBackend();")
    assert e2e.index("const secondRestartProductEventIds") > e2e.rindex("restartBackend();")
    assert "slice(0, beforeProductEventIds.length)" in e2e
    assert "slice(0, postRecoveryProductEventIds.length)" in e2e
    assert "getReviews:" in api_client
    assert 'queryKey: ["reviews", sessionId]' in workspace
    assert 'invalidateQueries({ queryKey: ["reviews", sessionId] })' in workspace


def test_staging_browser_tls_contract_uses_an_isolated_ca_trust_profile() -> None:
    config = _read_required(PROJECT_ROOT / "frontend/playwright.staging.config.ts")
    e2e = _read_required(PROJECT_ROOT / "frontend/e2e/ai4c-staging.spec.ts")

    for source in (config, e2e):
        assert "ignoreHTTPSErrors" not in source
        assert "--ignore-certificate-errors" not in source
        assert "--allow-insecure-localhost" not in source

    for required in (
        "FOCUSPROOF_STAGING_OIDC_CA_FILE",
        "testInfo.outputPath",
        'execFileSync("certutil"',
        '"-N"',
        '"-A"',
        "const nssDatabase = `sql:${nssDirectory}`",
        "chromium.launchPersistentContext",
        "HOME: profileHome",
        "const browserUrl = requiredStagingBrowserUrl()",
        "await page.goto(browserUrl)",
        "requiredHttpsIssuer",
    ):
        assert required in e2e
    assert "async ({ page })" not in e2e


def test_strict_external_gate_passes_fixed_node_env_to_the_playwright_child() -> None:
    source = _read_required(Path(__file__))
    external_gate = source[source.rindex("@pytest.mark.staging_external") :]
    round_environment = external_gate[
        external_gate.index("round_env = {") : external_gate.index("try:")
    ]
    playwright_call = external_gate[
        external_gate.index("playwright = _run_checked(") : external_gate.index("evidence_lines =")
    ]

    assert '"NODE_ENV": "test"' in round_environment
    assert "env=round_env" in playwright_call


@pytest.mark.parametrize(
    ("driver", "status_line"),
    (
        ("docker", "Status:                running"),
        ("docker", "Status:           running"),
        ("docker-container", "Status: running"),
    ),
)
def test_reproducible_buildkit_accepts_running_status_with_variable_whitespace(
    monkeypatch: pytest.MonkeyPatch, driver: str, status_line: str
) -> None:
    def inspect(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["docker", "buildx", "inspect"],
            0,
            stdout=f"Driver: {driver}\n{status_line}\nBuildKit version: v0.26.2\n",
            stderr="",
        )

    monkeypatch.setattr(sys.modules[__name__], "_run_checked", inspect)

    assert _require_reproducible_buildkit("default") == driver


@pytest.mark.parametrize(
    "inspect_output",
    (
        "Driver: docker\nStatus: stopped\nBuildKit version: v0.26.2\n",
        "Driver: docker\nBuildKit version: v0.26.2\n",
        "Driver: docker\nStatus: running\n",
    ),
)
def test_reproducible_buildkit_fails_closed_for_missing_or_non_running_fields(
    monkeypatch: pytest.MonkeyPatch, inspect_output: str
) -> None:
    def inspect(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["docker", "buildx", "inspect"], 0, stdout=inspect_output, stderr=""
        )

    monkeypatch.setattr(sys.modules[__name__], "_run_checked", inspect)

    with pytest.raises(AssertionError):
        _require_reproducible_buildkit("default")


def test_static_backend_exposes_dedicated_ready_endpoint() -> None:
    source = _read_required(PROJECT_ROOT / "agent-server/focusproof/api/app.py")

    assert '@application.get("/ready")' in source
    assert "readiness_error" in source
    assert "database_unavailable" in source


def test_disposable_compose_project_names_do_not_collide_across_runs(
    tmp_path: Path,
) -> None:
    first = _disposable_compose_project_name(tmp_path / "run-one")
    second = _disposable_compose_project_name(tmp_path / "run-two")

    assert first != second
    assert first.startswith("focusproof-ai4c-task4-")
    assert second.startswith("focusproof-ai4c-task4-")


@pytest.mark.staging_external
def test_staging_external_stack_builds_runs_and_preserves_ids(tmp_path: Path) -> None:
    _require_staging_capabilities()
    secrets_dir = _write_local_issuer_materials(tmp_path)

    project_name = _disposable_compose_project_name(tmp_path)
    compose_env = _compose_env(secrets_dir)
    compose_env.update(
        {
            "FOCUSPROOF_STAGING_BUILDER": "default",
            "SOURCE_DATE_EPOCH": "1735689600",
            "FOCUSPROOF_STAGING_BROWSER_URL": "http://127.0.0.1:13000",
            "FOCUSPROOF_STAGING_TEST_USER_PASSWORD": LOCAL_USER_PASSWORD,
            "FOCUSPROOF_STAGING_COMPOSE_PROJECT": project_name,
        }
    )
    image_names = {
        "agent-server": "focusproof-agent-server:staging",
        "frontend": "focusproof-frontend:staging",
    }
    import check_staging_reproducibility

    build_driver = _require_reproducible_buildkit(compose_env["FOCUSPROOF_STAGING_BUILDER"])

    round_digests: list[dict[str, str]] = []
    round_canonical_digests: list[dict[str, str]] = []
    round_canonical_records: list[dict[str, tuple[dict[str, object], ...]]] = []
    round_snapshots: list[dict[str, dict[str, object]]] = []
    for round_number in range(1, 3):
        context_dir = _git_index_archive_context(tmp_path, round_number)
        compose_file = context_dir / "deploy/compose.staging.yml"
        assert compose_file.is_file()
        compose = [
            "docker",
            "compose",
            "--project-name",
            project_name,
            "-f",
            str(compose_file),
        ]
        round_env = {
            **compose_env,
            "FOCUSPROOF_STAGING_COMPOSE_FILE": str(compose_file),
            "NODE_ENV": "test",
            "NODE_PATH": str(PROJECT_ROOT / "frontend" / "node_modules"),
        }
        try:
            subprocess.run(
                compose
                + ["down", "--volumes", "--remove-orphans", "--rmi", "local", "--timeout", "10"],
                cwd=context_dir,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
                env=_safe_subprocess_env(round_env),
            )
            _build_staging_image(
                builder=compose_env["FOCUSPROOF_STAGING_BUILDER"],
                driver=build_driver,
                context_dir=context_dir,
                dockerfile="deploy/agent-server.Dockerfile",
                image=image_names["agent-server"],
                build_args={"SOURCE_DATE_EPOCH": compose_env["SOURCE_DATE_EPOCH"]},
            )
            _build_staging_image(
                builder=compose_env["FOCUSPROOF_STAGING_BUILDER"],
                driver=build_driver,
                context_dir=context_dir,
                dockerfile="deploy/frontend.Dockerfile",
                image=image_names["frontend"],
                build_args={
                    "SOURCE_DATE_EPOCH": compose_env["SOURCE_DATE_EPOCH"],
                    "NEXT_PUBLIC_OIDC_ISSUER": compose_env["NEXT_PUBLIC_OIDC_ISSUER"],
                    "NEXT_PUBLIC_OIDC_CLIENT_ID": compose_env["NEXT_PUBLIC_OIDC_CLIENT_ID"],
                    "NEXT_PUBLIC_OIDC_AUDIENCE": compose_env["NEXT_PUBLIC_OIDC_AUDIENCE"],
                    "NEXT_PUBLIC_OIDC_REDIRECT_URI": compose_env["NEXT_PUBLIC_OIDC_REDIRECT_URI"],
                },
            )
            digests = {
                name: _run_checked(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", image],
                    cwd=context_dir,
                    timeout=30,
                    env=round_env,
                ).stdout.strip()
                for name, image in image_names.items()
            }
            assert all(digest.startswith("sha256:") for digest in digests.values())
            round_digests.append(digests)
            canonical_snapshots = {
                name: _canonical_release_snapshot_for_image(
                    image,
                    image_name=name,
                    context_dir=context_dir,
                    dockerfile=context_dir
                    / (
                        "deploy/agent-server.Dockerfile"
                        if name == "agent-server"
                        else "deploy/frontend.Dockerfile"
                    ),
                    output_dir=context_dir,
                    env=round_env,
                )
                for name, image in image_names.items()
            }
            canonical_digests = {
                name: snapshot.digest for name, snapshot in canonical_snapshots.items()
            }
            round_canonical_digests.append(canonical_digests)
            round_canonical_records.append(
                {name: snapshot.records for name, snapshot in canonical_snapshots.items()}
            )
            print(
                "AI4C_RELEASE_DIGEST_EVIDENCE "
                f"round={round_number} canonical={canonical_digests} oci_ids={digests}"
            )
            _run_checked(
                compose + ["up", "-d", "--wait"],
                cwd=context_dir,
                timeout=900,
                env=round_env,
            )
            playwright = _run_checked(
                [
                    str(PROJECT_ROOT / "frontend/node_modules/.bin/playwright"),
                    "test",
                    "--config",
                    "playwright.staging.config.ts",
                ],
                cwd=context_dir / "frontend",
                timeout=900,
                env=round_env,
            )
            evidence_lines = [
                line
                for line in playwright.stdout.splitlines()
                if "AI4C_NATIVE_RECOVERY_EVIDENCE" in line
            ]
            assert len(evidence_lines) == 1, playwright.stdout[-8_000:]
            print(evidence_lines[0])
        finally:
            try:
                round_snapshots.append(
                    {
                        name: _safe_staging_image_snapshot(image, cwd=context_dir, env=round_env)
                        for name, image in image_names.items()
                    }
                )
            finally:
                subprocess.run(
                    compose
                    + [
                        "down",
                        "--volumes",
                        "--remove-orphans",
                        "--rmi",
                        "local",
                        "--timeout",
                        "10",
                    ],
                    cwd=context_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=120,
                    env=_safe_subprocess_env(round_env),
                )

    check_staging_reproducibility.compare_round_digests(
        *round_canonical_digests,
        round_one_snapshots=round_snapshots[0],
        round_two_snapshots=round_snapshots[1],
        round_one_records=round_canonical_records[0],
        round_two_records=round_canonical_records[1],
    )


def test_playwright_configs_separate_deterministic_and_external_staging_suites() -> None:
    playwright = PROJECT_ROOT / "frontend/node_modules/.bin/playwright"
    deterministic = subprocess.run(
        [str(playwright), "test", "--list", "--config", "playwright.config.ts"],
        cwd=PROJECT_ROOT / "frontend",
        env=_safe_subprocess_env(),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    staging = subprocess.run(
        [str(playwright), "test", "--list", "--config", "playwright.staging.config.ts"],
        cwd=PROJECT_ROOT / "frontend",
        env=_safe_subprocess_env(),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert deterministic.returncode == 0, deterministic.stderr
    assert "ai4b-real-flow.spec.ts" in deterministic.stdout
    assert "bff-image-retry.spec.ts" in deterministic.stdout
    assert "focusproof-flow.spec.ts" not in deterministic.stdout
    assert "ai4c-staging.spec.ts" not in deterministic.stdout
    assert staging.returncode == 0, staging.stderr
    assert "ai4c-staging.spec.ts" in staging.stdout
    assert "ai4b-real-flow.spec.ts" not in staging.stdout
    assert "focusproof-flow.spec.ts" not in staging.stdout
