from __future__ import annotations

import logging
from typing import cast
from uuid import uuid4

from _pytest.logging import LogCaptureFixture

from focusproof.openhands_runtime.handle import ConversationHandle
from focusproof.openhands_runtime.manager import ConversationManager


class _FakeState:
    events: list[object] = []


class _FakeConversation:
    state = _FakeState()


class _FakeHandle:
    session_id = "sess_failure_diag"
    conversation_id = uuid4()
    conversation = _FakeConversation()


def test_failure_result_keeps_client_error_stable_and_logs_redacted_root_cause(
    caplog: LogCaptureFixture,
) -> None:
    manager = ConversationManager.__new__(ConversationManager)
    root = RuntimeError(
        "Authorization: Bearer sk-test-secret "
        "api_key=sk-test-secret "
        "base_url=https://api.openai.com/v1 "
        "provider says Free quota exhausted"
    )
    exc = RuntimeError("OpenHands wrapper")
    exc.__cause__ = root

    with caplog.at_level(
        logging.WARNING,
        logger="focusproof.openhands_runtime.manager",
    ):
        result = manager._failure_result(cast(ConversationHandle, _FakeHandle()), exc)

    client_payload = result.model_dump_json()
    assert result.error == "RuntimeError: OpenHands conversation run failed"
    assert "Free quota exhausted" not in client_payload

    records = [
        record
        for record in caplog.records
        if record.name == "focusproof.openhands_runtime.manager"
    ]
    assert records
    diagnostic = records[0]
    root_type = getattr(diagnostic, "root_exception_type")
    root_message = getattr(diagnostic, "root_exception_message")
    assert root_type == "RuntimeError"
    assert "Free quota exhausted" in root_message

    rendered = client_payload + caplog.text + root_message
    for sensitive in (
        "sk-test-secret",
        "Authorization",
        "Bearer",
        "api_key",
        "https://api.openai.com/v1",
    ):
        assert sensitive not in rendered
