from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from focusproof.domain.plugins.monad.repository import MonadClaimConflict, MonadClaimRepository


def seed(engine: Engine) -> None:
    now = datetime.now(UTC)
    with engine.begin() as connection:
        for suffix in ("1", "2"):
            connection.execute(text("""
                INSERT INTO learning_sessions (
                    session_id, owner_user_id, status, adapter_mode, domain, title, goal,
                    conversation_id, runtime_mode, version, created_at, updated_at
                ) VALUES (:session, 'owner', 'running', 'test', 'general', 't', 'g',
                          :conversation, 'test', 1, :now, :now)
            """), {"session": f"sess_{suffix}", "conversation": f"00000000-0000-0000-0000-00000000000{suffix}", "now": now})
            connection.execute(text("""
                INSERT INTO evidence (evidence_id, session_id, evidence_type, content_hash,
                                      metadata_json, created_at)
                VALUES (:evidence, :session, 'monad_transaction', :hash, '{}', :now)
            """), {"evidence": f"ev_{suffix}", "session": f"sess_{suffix}", "hash": f"hash_{suffix}", "now": now})


def test_same_evidence_retry_is_idempotent(migrated_engine: Engine) -> None:
    seed(migrated_engine)
    tx_hash = "0x" + "AB" * 32
    with Session(migrated_engine) as session, session.begin():
        repository = MonadClaimRepository(session)
        first = repository.claim(1234, tx_hash, "sess_1", "ev_1", "obs_1")
        second = repository.claim(1234, tx_hash.lower(), "sess_1", "ev_1", "obs_1")
    assert first.claim_id == second.claim_id
    assert second.created is False
    assert second.transaction_hash == tx_hash.lower()


def test_database_uniqueness_rejects_cross_session_reuse(migrated_engine: Engine) -> None:
    seed(migrated_engine)
    tx_hash = "0x" + "ab" * 32
    with Session(migrated_engine) as first, first.begin():
        MonadClaimRepository(first).claim(1234, tx_hash, "sess_1", "ev_1", "obs_1")
    with Session(migrated_engine) as second:
        with pytest.raises(MonadClaimConflict, match="reused_transaction"):
            with second.begin():
                MonadClaimRepository(second).claim(1234, tx_hash.upper().replace("0X", "0x"),
                                                    "sess_2", "ev_2", "obs_2")
    with migrated_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM monad_evidence_claims")).scalar_one() == 1


def test_rollback_leaves_no_partial_claim(migrated_engine: Engine) -> None:
    seed(migrated_engine)
    session = Session(migrated_engine)
    MonadClaimRepository(session).claim(1234, "0x" + "cd" * 32, "sess_1", "ev_1", "obs_1")
    session.rollback()
    session.close()
    with migrated_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM monad_evidence_claims")).scalar_one() == 0
