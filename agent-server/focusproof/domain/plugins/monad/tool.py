from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar, Self

from openhands.sdk.tool import ToolAnnotations, ToolDefinition

from focusproof.domain.plugins.monad.executor import (
    MonadToolRepository,
    MonadVerificationAction,
    MonadVerificationExecutor,
    MonadVerificationObservation,
)


class MonadVerificationTool(ToolDefinition[MonadVerificationAction, MonadVerificationObservation]):
    name: ClassVar[str] = "verify_monad_learning_transaction"

    @classmethod
    def create(
        cls,
        conv_state: Any | None = None,
        *,
        session_id: str,
        repository: MonadToolRepository | None = None,
    ) -> Sequence[Self]:
        del conv_state
        return [
            cls(
                description=(
                    "Verify one authorized Monad learning transaction by evidence_id. "
                    "Never provide wallet, transaction, RPC, contract, ABI, or explanation."
                ),
                action_type=MonadVerificationAction,
                observation_type=MonadVerificationObservation,
                executor=MonadVerificationExecutor(repository, session_id),
                annotations=ToolAnnotations(
                    title="Verify Monad learning transaction",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]


__all__ = ["MonadVerificationAction", "MonadVerificationObservation", "MonadVerificationTool"]
