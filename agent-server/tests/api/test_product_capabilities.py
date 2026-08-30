"""Behavioral RED tests for product capability disclosure and clean disablement."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from focusproof.api.app import _view
from focusproof.runtime.evidence import LearningGoal

IMAGE_CAPABILITY = {
    "capabilityId": "image_evidence",
    "enabled": True,
    "formats": ["image/png", "image/jpeg", "image/webp"],
    "maxCount": 4,
    "maxOriginalBytes": 10_485_760,
    "maxNormalizedBytesPerSession": 20_971_520,
    "explanationRequired": True,
}

FORBIDDEN_IMAGE_DELIVERY_MODULE_PREFIXES = (
    "focusproof.api.media_models",
    "focusproof.api.media_routes",
    "focusproof.bootstrap.media_composition",
    "focusproof.media_adapters",
    "focusproof.media_api",
    "focusproof.media_contribution",
    "focusproof.media_projection",
    "focusproof.openhands_runtime.media_evidence_facts",
    "focusproof.openhands_runtime.tools.media_evidence",
    "focusproof.openhands_runtime.tools.media_narrative",
)


def _goal() -> LearningGoal:
    return LearningGoal(
        domain="general",
        title="Images",
        goal="Explain an image",
        expectedOutput=None,
        plannedMinutes=None,
    )


def test_disabled_view_has_no_product_capabilities_and_keeps_plugins() -> None:
    plugin = {
        "pluginId": "existing",
        "capabilityId": "claim",
        "enabled": True,
        "metadata": {"stable": True},
    }
    rendered = _view(
        "sess-1",
        "running",
        _goal(),
        [],
        None,
        [plugin],
        product_capabilities=[],
    )

    assert rendered["productCapabilities"] == []
    assert rendered["pluginCapabilities"] == [plugin]


def test_enabled_view_discloses_exact_image_capability_and_keeps_plugins() -> None:
    plugin = {
        "pluginId": "existing",
        "capabilityId": "claim",
        "enabled": True,
        "metadata": {"stable": True},
    }
    rendered = _view(
        "sess-1",
        "running",
        _goal(),
        [],
        None,
        [plugin],
        product_capabilities=[IMAGE_CAPABILITY],
    )

    assert rendered["productCapabilities"] == [IMAGE_CAPABILITY]
    assert rendered["pluginCapabilities"] == [plugin]


def _disabled_media_import_audit(
    *,
    imports: tuple[str, ...],
    media_env: str | None,
    create_app: bool = False,
) -> dict[str, object]:
    project_root = Path(__file__).resolve().parents[3]
    code = f"""
import importlib
import json
import os
import sys

if {media_env is None!r}:
    os.environ.pop("FOCUSPROOF_MEDIA_ENABLED", None)
else:
    os.environ["FOCUSPROOF_MEDIA_ENABLED"] = {media_env!r}

for module_name in {list(imports)!r}:
    importlib.import_module(module_name)

routes = []
if {create_app!r}:
    from focusproof.api.app import create_app

    app = create_app()
    routes = [getattr(route, "path", None) for route in app.routes]

forbidden_prefixes = {list(FORBIDDEN_IMAGE_DELIVERY_MODULE_PREFIXES)!r}
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_prefixes)
)
print(json.dumps({{
    "loaded": loaded,
    "routes": routes,
}}, sort_keys=True))
"""
    environment = os.environ.copy()
    if media_env is None:
        environment.pop("FOCUSPROOF_MEDIA_ENABLED", None)
    else:
        environment["FOCUSPROOF_MEDIA_ENABLED"] = media_env
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("media_env", [None, "false"])
@pytest.mark.parametrize(
    "imports",
    [
        ("focusproof.api.app",),
        ("focusproof.openhands_runtime.manager",),
        ("focusproof.openhands_runtime.synchronizer",),
        ("focusproof.persistence.unit_of_work",),
    ],
)
def test_disabled_clean_process_imports_do_not_load_image_delivery_modules(
    media_env: str | None,
    imports: tuple[str, ...],
) -> None:
    audit = _disabled_media_import_audit(imports=imports, media_env=media_env)

    assert audit["loaded"] == []


def test_disabled_fresh_process_does_not_import_or_install_media_delivery() -> None:
    audit = _disabled_media_import_audit(
        imports=("focusproof.api.app",),
        media_env="false",
        create_app=True,
    )

    assert audit["loaded"] == []
    assert "/sessions/{session_id}/evidence/image" not in audit["routes"]
