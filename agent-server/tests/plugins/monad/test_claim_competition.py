from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from focusproof.domain.plugins.monad.repository import MonadClaimConflict, MonadClaimRepository
from tests.plugins.monad.test_claim_repository import seed


def test_sqlite_unique_constraint_arbitrates_competing_sessions(
    migrated_engine: Engine,
) -> None:
    seed(migrated_engine)
    barrier = Barrier(2)
    tx_hash = "0x" + "ef" * 32

    def compete(suffix: str) -> str:
        try:
            with Session(migrated_engine) as session, session.begin():
                barrier.wait()
                MonadClaimRepository(session).claim(
                    1234, tx_hash, f"sess_{suffix}", f"ev_{suffix}", f"obs_{suffix}"
                )
            return "claimed"
        except MonadClaimConflict as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(compete, ("1", "2")))
    assert outcomes == ["claimed", "reused_transaction"]
