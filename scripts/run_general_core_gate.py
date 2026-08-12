from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

SCHEMA_VERSION = "1.0"
SCENARIOS = (
    {
        "name": "photosynthesis-text",
        "goal": "Explain how photosynthesis converts light energy into stored chemical energy.",
        "evidenceType": "text",
        "textContent": "Chlorophyll absorbs light. Light reactions form ATP and NADPH, which support carbon fixation into sugars.",
        "answer": "Chlorophyll captures photons; electron transport produces ATP and NADPH, and the Calvin cycle uses them to fix carbon into sugars.",
    },
    {
        "name": "python-closure-url",
        "goal": "Explain how Python closures retain lexical state.",
        "evidenceType": "url",
        "sourceUrl": "https://docs.python.org/3/tutorial/classes.html#python-scopes-and-namespaces",
        "answer": "A closure retains references to bindings from its enclosing lexical scope, so those values remain accessible after the outer call returns.",
    },
)


class GateConfigurationError(RuntimeError):
    pass


class ProviderBlocked(RuntimeError):
    pass


class BusinessFailure(RuntimeError):
    pass


RequestFunction = Callable[..., dict[str, Any]]


def request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    payload = kwargs.get("payload")
    timeout = float(kwargs.get("timeout_seconds", 30))
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=body, method=method, headers={"content-type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read())
    except HTTPError as exc:
        if exc.code in {401, 402, 403, 408, 429, 500, 502, 503, 504}:
            raise ProviderBlocked(f"provider/API unavailable (HTTP {exc.code})") from None
        raise BusinessFailure(f"API request failed (HTTP {exc.code})") from None
    except (TimeoutError, URLError):
        raise ProviderBlocked("provider/API network unavailable") from None
    if not isinstance(value, dict):
        raise BusinessFailure("API response was not an object")
    return value


def _endpoint(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _safe_setting(environ: Mapping[str, str], suffix: str) -> str:
    for name, value in environ.items():
        if name.startswith("FOCUSPROOF_LLM_") and name.endswith(suffix):
            return value
    return "unknown"


def _redact(value: str, environ: Mapping[str, str]) -> str:
    safe = value
    for name, secret in environ.items():
        upper = name.upper()
        if secret and any(word in upper for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            safe = safe.replace(secret, "[REDACTED]")
    return safe


def _report_base(environ: Mapping[str, str]) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "timestamp": datetime.now(UTC).isoformat(),
        "gitSha": _git_sha(),
        "provider": _safe_setting(environ, "PROVIDER"),
        "model": _safe_setting(environ, "MODEL"),
        "scenarios": [],
        "overall": "FAIL",
    }


def write_terminal_report(
    path: Path, overall: str, reason: str, environ: Mapping[str, str]
) -> int:
    report = _report_base(environ)
    report["overall"] = overall
    report["scenarios"] = [{"status": overall, "reason": _redact(reason, environ)}]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 2 if overall == "BLOCKED" else 1


def server_environment(environ: Mapping[str, str], root: Path) -> dict[str, str]:
    """Build the server environment without consulting a dotenv file."""
    data_dir = root / "data"
    database = data_dir / "focusproof.db"
    child = dict(environ)
    child.update(
        {
            "FOCUSPROOF_PROFILE": "local-dev",
            "FOCUSPROOF_DATA_DIR": str(data_dir),
            "FOCUSPROOF_DATABASE_URL": f"sqlite:///{database}",
            "DATABASE_URL": f"sqlite:///{database}",
            "FOCUSPROOF_PLUGIN_MONAD_ENABLED": "false",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "agent-server"),
        }
    )
    child.pop("DOTENV", None)
    return child


def _scenario(
    base_url: str,
    scenario: Mapping[str, Any],
    request: RequestFunction,
) -> dict[str, Any]:
    created = request("POST", _endpoint(base_url, "/sessions"), payload={
        "domain": "general", "title": str(scenario["name"]), "goal": str(scenario["goal"]),
        "expectedOutput": "Independent grounded explanation", "plannedMinutes": 10,
    })
    session_id = str(created["sessionId"])
    evidence_payload = {k: v for k, v in scenario.items() if k in {"evidenceType", "textContent", "sourceUrl"}}
    request("POST", _endpoint(base_url, f"/sessions/{session_id}/evidence"), payload=evidence_payload)
    questions: list[str] = []
    completed: dict[str, Any] = {}
    for _ in range(4):
        review = request("POST", _endpoint(base_url, f"/sessions/{session_id}/review"))
        status = review.get("reviewStatus")
        if status == "completed":
            completed = review
            break
        if status != "awaiting_user":
            raise BusinessFailure(f"unexpected review status: {status}")
        items = review.get("agentQuestions")
        if not isinstance(items, list) or not items:
            raise BusinessFailure("awaiting_user omitted a question")
        question = items[0]
        questions.append(str(question.get("question", "")))
        request("POST", _endpoint(base_url, f"/sessions/{session_id}/answer"), payload={
            "questionId": str(question["questionId"]), "answer": str(scenario["answer"]),
        })
    if not completed:
        raise BusinessFailure("review exceeded the interaction limit")
    events = request("GET", _endpoint(base_url, f"/sessions/{session_id}/events"))
    session = request("GET", _endpoint(base_url, f"/sessions/{session_id}"))
    nested_result = completed.get("reviewResult")
    result: dict[str, Any] = nested_result if isinstance(nested_result, dict) else completed
    native_events = events.get("events")
    build_log = session.get("buildLog") or native_events
    conversation_id = completed.get("conversationId") or session.get("state", {}).get("conversationId")
    reason = result.get("reason") or result.get("status")
    required = ("score", "confidence", "findings", "summary", "nextStep")
    if any(key not in result for key in required) or not reason or not conversation_id or not native_events or not build_log:
        raise BusinessFailure("completed review omitted required acceptance evidence")
    capabilities = session.get("view", {}).get("pluginCapabilities", [])
    if any(str(item.get("pluginId", "")).lower() == "monad" for item in capabilities if isinstance(item, dict)):
        raise BusinessFailure("Monad capability count was not zero")
    return {
        "name": scenario["name"], "status": "PASS", "sessionId": session_id,
        "conversationId": conversation_id, "question": questions[0],
        "questions": questions, "score": result["score"], "reason": reason,
        "confidence": result["confidence"], "findings": result["findings"],
        "summary": result["summary"], "nextStep": result["nextStep"],
        "buildLog": build_log, "nativeEvents": native_events,
    }


def run_gate(*, base_url: str, scenarios: Sequence[Mapping[str, Any]], request: RequestFunction,
             report_path: Path, environ: Mapping[str, str], platform_name: str) -> int:
    if platform_name != "linux":
        raise GateConfigurationError("General Core Gate requires Linux/Python 3.12")
    if sys.version_info[:2] != (3, 12):
        raise GateConfigurationError("General Core Gate requires Linux/Python 3.12")
    report = _report_base(environ)
    try:
        capabilities = request("GET", _endpoint(base_url, "/openhands/capabilities"))
        plugins = capabilities.get("plugins", [])
        if any(str(item.get("name", "")).lower() == "monad" for item in plugins if isinstance(item, dict)):
            raise BusinessFailure("Monad capability count was not zero")
        report["scenarios"] = [_scenario(base_url, item, request) for item in scenarios]
        if len(scenarios) > 1:
            questions = [item["question"] for item in report["scenarios"]]
            if len(set(questions)) != len(questions):
                raise BusinessFailure("scenario questions were not dynamically different")
        report["overall"] = "PASS"
        code = 0
    except ProviderBlocked as exc:
        report["overall"] = "BLOCKED"
        report["scenarios"].append({"status": "BLOCKED", "reason": _redact(str(exc), environ)})
        code = 2
    except (BusinessFailure, KeyError) as exc:
        report["overall"] = "FAIL"
        report["scenarios"].append({"status": "FAIL", "reason": _redact(str(exc), environ)})
        code = 1
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Linux-only FocusProof General Core Gate")
    parser.add_argument("--base-url")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.base_url:
            return run_gate(base_url=args.base_url, scenarios=SCENARIOS, request=request_json,
                            report_path=args.report, environ=os.environ, platform_name=platform.system().lower())
        if platform.system().lower() != "linux" or sys.version_info[:2] != (3, 12):
            raise GateConfigurationError("General Core Gate requires Linux/Python 3.12")
        with tempfile.TemporaryDirectory(prefix="focusproof-general-core-gate-") as raw_root:
            root = Path(raw_root)
            child = server_environment(os.environ, root)
            data_dir = Path(child["FOCUSPROOF_DATA_DIR"])
            data_dir.mkdir(parents=True)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = int(sock.getsockname()[1])
            server = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("run_general_core_gate_server.py")),
                 "--port", str(port), "--database-url", child["FOCUSPROOF_DATABASE_URL"],
                 "--data-dir", str(data_dir)], env=child,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            base_url = f"http://127.0.0.1:{port}"
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        raise ProviderBlocked("official FastAPI server failed to start")
                    try:
                        request_json("GET", _endpoint(base_url, "/health"), timeout_seconds=1)
                        break
                    except ProviderBlocked:
                        time.sleep(0.1)
                else:
                    raise ProviderBlocked("official FastAPI server startup timed out")
                return run_gate(base_url=base_url, scenarios=SCENARIOS, request=request_json,
                                report_path=args.report, environ=os.environ, platform_name="linux")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
    except ProviderBlocked as exc:
        return write_terminal_report(args.report, "BLOCKED", str(exc), os.environ)
    except GateConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
