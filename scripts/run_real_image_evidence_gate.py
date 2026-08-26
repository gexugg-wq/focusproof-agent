from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import socket
import struct
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-server"))

from focusproof.media_adapters.clamd_limits import ClamdLimits  # noqa: E402
from focusproof.media_adapters.clamd_malware_scanner import (  # noqa: E402
    ClamdMalwareScanner,
)
from focusproof.media_core.ports import ReadOnlyMediaSource  # noqa: E402

_CASE_NAMES = ("benign_png", "eicar", "timeout", "unavailable", "error")
_EXPECTED_OUTCOMES = {
    "benign_png": "clean",
    "eicar": "malicious",
    "timeout": "timeout",
    "unavailable": "unavailable",
    "error": "error",
}
_BENIGN_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
)
_EICAR_PARTS = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$",
    b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!",
    b"$H+H*",
)


@dataclass(frozen=True, slots=True)
class GateCaseResult:
    name: str
    outcome: str
    passed: bool
    rejection_code: str | None = None
    finalized: bool = False


def _source(payload: bytes) -> ReadOnlyMediaSource:
    return ReadOnlyMediaSource(
        stream=BytesIO(payload),
        byte_size=len(payload),
        streaming_sha256=sha256(payload).hexdigest(),
    )


def _scanner(endpoint: str, *, deadline_ms: int = 2_000) -> ClamdMalwareScanner:
    return ClamdMalwareScanner(
        endpoint=endpoint,
        limits=ClamdLimits(
            max_bytes=10 * 1024 * 1024,
            max_concurrent_scans=1,
            deadline_ms=deadline_ms,
            socket_timeout_ms=min(deadline_ms, 500),
            admission_timeout_ms=min(deadline_ms, 500),
            definitions_version="live-gate",
            definitions_fresh_at=datetime.now(UTC),
        ),
    )


def _case(name: str, outcome: object, *, finalized: bool = False) -> GateCaseResult:
    status = str(getattr(outcome, "status", "error"))
    rejection = getattr(outcome, "rejection_code", None)
    rejection_code = getattr(rejection, "value", None)
    return GateCaseResult(
        name=name,
        outcome=status,
        passed=status == _EXPECTED_OUTCOMES[name],
        rejection_code=rejection_code,
        finalized=finalized,
    )


class _FaultEndpoint:
    def __init__(self, mode: str) -> None:
        self._mode = mode
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self._server.settimeout(0.1)
        host, port = self._server.getsockname()
        self.endpoint = f"tcp://{host}:{port}"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> _FaultEndpoint:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop.set()
        self._server.close()
        self._thread.join(timeout=1.0)

    def _serve(self) -> None:
        connection: socket.socket | None = None
        try:
            while not self._stop.is_set():
                try:
                    connection, _ = self._server.accept()
                    break
                except TimeoutError:
                    continue
                except OSError:
                    return
            if connection is None:
                return
            connection.settimeout(0.1)
            if self._mode == "timeout":
                self._stop.wait(1.0)
                return
            self._consume_request(connection)
            connection.sendall(b"stream: daemon protocol ERROR\0")
        except OSError:
            return
        finally:
            if connection is not None:
                connection.close()

    def _consume_request(self, connection: socket.socket) -> None:
        if self._recv_exact(connection, len(b"zINSTREAM\0")) != b"zINSTREAM\0":
            return
        while not self._stop.is_set():
            size = struct.unpack("!I", self._recv_exact(connection, 4))[0]
            if size == 0:
                return
            self._recv_exact(connection, size)

    @staticmethod
    def _recv_exact(connection: socket.socket, size: int) -> bytes:
        received = bytearray()
        while len(received) < size:
            chunk = connection.recv(size - len(received))
            if not chunk:
                raise OSError("fault endpoint request closed")
            received.extend(chunk)
        return bytes(received)


def _unavailable_endpoint() -> tuple[str, socket.socket]:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    host, port = probe.getsockname()
    return f"tcp://{host}:{port}", probe


def run_live_matrix(endpoint: str) -> tuple[GateCaseResult, ...]:
    live_scanner = _scanner(endpoint)
    benign = live_scanner.scan(_source(_BENIGN_PNG))
    benign_case = _case("benign_png", benign, finalized=benign.status == "clean")
    eicar = live_scanner.scan(_source(b"".join(_EICAR_PARTS)))
    eicar_case = _case("eicar", eicar, finalized=False)

    with _FaultEndpoint("timeout") as fault:
        timeout = _scanner(fault.endpoint, deadline_ms=100).scan(_source(_BENIGN_PNG))
    timeout_case = _case("timeout", timeout)

    unavailable_endpoint, probe = _unavailable_endpoint()
    try:
        unavailable = _scanner(unavailable_endpoint, deadline_ms=200).scan(_source(_BENIGN_PNG))
    finally:
        probe.close()
    unavailable_case = _case("unavailable", unavailable)

    with _FaultEndpoint("error") as fault:
        error = _scanner(fault.endpoint, deadline_ms=500).scan(_source(_BENIGN_PNG))
    error_case = _case("error", error)
    return benign_case, eicar_case, timeout_case, unavailable_case, error_case


def build_report(
    cases: tuple[GateCaseResult, ...], *, live_clamd_executed: bool
) -> dict[str, object]:
    exact_matrix = tuple(case.name for case in cases) == _CASE_NAMES
    all_passed = exact_matrix and all(case.passed for case in cases)
    return {
        "gate": "production_clamd",
        "status": "PASS" if live_clamd_executed and all_passed else "FAIL",
        "liveClamdExecuted": live_clamd_executed,
        "visualProviderEnabled": False,
        "productionLlmEnabled": False,
        "productionMalwareScanningVerified": live_clamd_executed and all_passed,
        "cases": [
            {
                "name": case.name,
                "outcome": case.outcome,
                "passed": case.passed,
                "rejectionCode": case.rejection_code,
                "finalized": case.finalized,
            }
            for case in cases
        ],
    }


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clamd-endpoint")
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.clamd_endpoint:
        report = build_report((), live_clamd_executed=False)
        report["reasonCode"] = "live_clamd_not_configured"
        _write_report(args.report, report)
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 2
    try:
        cases = run_live_matrix(args.clamd_endpoint)
        report = build_report(cases, live_clamd_executed=True)
    except Exception:
        report = build_report((), live_clamd_executed=False)
        report["reasonCode"] = "live_clamd_gate_error"
    _write_report(args.report, report)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["productionMalwareScanningVerified"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
