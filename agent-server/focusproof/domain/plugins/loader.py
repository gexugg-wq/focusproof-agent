from __future__ import annotations

from collections.abc import Mapping

from focusproof.domain.plugins.base import EvidencePluginProvider


def load_evidence_plugin_providers(
    environ: Mapping[str, str],
) -> tuple[EvidencePluginProvider, ...]:
    """Load optional providers only when their explicit feature flag is true."""

    enabled = environ.get("FOCUSPROOF_PLUGIN_MONAD_ENABLED", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return ()

    from focusproof.domain.plugins.monad.configuration import MonadPluginSettings
    from focusproof.domain.plugins.monad.manifest import MonadEvidencePluginProvider

    settings = MonadPluginSettings.from_environ(environ)
    if not settings.enabled:
        return ()

    return (MonadEvidencePluginProvider(settings),)
