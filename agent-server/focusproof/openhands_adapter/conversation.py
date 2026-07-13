from __future__ import annotations

from dataclasses import dataclass

from focusproof.openhands_adapter.capabilities import get_openhands_capabilities


@dataclass(frozen=True)
class OpenHandsConversationAdapter:
    session_id: str
    mode: str
    blocked_reason: str | None = None

    @classmethod
    def create(cls, session_id: str) -> "OpenHandsConversationAdapter":
        capabilities = get_openhands_capabilities()
        mode = str(capabilities["adapterMode"])
        reason = None if capabilities["importOk"] else str(capabilities.get("error") or "SDK import failed")
        return cls(session_id=session_id, mode=mode, blocked_reason=reason)
