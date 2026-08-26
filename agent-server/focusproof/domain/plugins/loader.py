from __future__ import annotations

from collections.abc import Mapping

from focusproof.domain.plugins.base import EvidencePluginProvider


def load_evidence_plugin_providers(
    environ: Mapping[str, str],
) -> tuple[EvidencePluginProvider, ...]:
    del environ
    return ()
