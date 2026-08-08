from __future__ import annotations

import subprocess
import sys


def test_core_persistence_imports_when_monad_package_is_physically_absent() -> None:
    script = r'''import builtins
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith("focusproof.domain.plugins.monad"):
        raise ImportError("Monad package is physically absent")
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
from focusproof.persistence.models import Base
assert "monad_evidence_claims" not in Base.metadata.tables
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
