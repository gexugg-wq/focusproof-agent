from __future__ import annotations

import pytest

from focusproof.persistence.unit_of_work import UnitOfWorkFactory

from .test_session_repository import _session


def test_exception_rolls_back_transaction(uow_factory: UnitOfWorkFactory) -> None:
    with pytest.raises(RuntimeError):
        with uow_factory() as uow:
            uow.sessions.create(_session())
            raise RuntimeError("abort")

    with uow_factory() as uow:
        assert uow.sessions.get("sess_1") is None


def test_list_recoverable_excludes_terminal_sessions(
    uow_factory: UnitOfWorkFactory,
) -> None:
    with uow_factory() as uow:
        uow.sessions.create(_session("sess_running"))
        completed = _session("sess_completed").model_copy(update={"status": "reviewed"})
        uow.sessions.create(completed)
        uow.commit()
    with uow_factory() as uow:
        recoverable = uow.sessions.list_recoverable()
    assert [session.session_id for session in recoverable] == ["sess_running"]
