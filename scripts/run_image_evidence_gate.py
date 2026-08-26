from __future__ import annotations

import json


BLOCKED_BY_OFFICIAL_SDK_GATE = "BLOCKED_BY_OFFICIAL_SDK_GATE"


def gate_result() -> dict[str, object]:
    return {
        "gate": "real_image_provider",
        "status": BLOCKED_BY_OFFICIAL_SDK_GATE,
        "sdk": {"name": "openhands-sdk", "version": "1.31.0"},
        "provider_executed": False,
        "env_file_read": False,
        "missing_public_contracts": [
            "inner_llm_composition",
            "wrapper_identity_on_restore",
            "stats_budget_call_accounting",
            "replacement_agent_negative_case",
        ],
        "verified_non_visual_paths": [
            "native_message_image_content_roundtrip",
            "media_ingestion_and_storage",
            "runtime_contribution_and_safe_facts",
            "image_review_projection_and_scoring",
        ],
    }


def main() -> int:
    print(json.dumps(gate_result(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
