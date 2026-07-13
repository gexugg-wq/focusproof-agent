from focusproof.runtime.actions import Action
from focusproof.tools.fake_tools import FakeTextEvidenceTool, FakeWeb3TxTool


def test_fake_text_evidence_tool_returns_facts_not_score() -> None:
    observation = FakeTextEvidenceTool().execute(
        Action(
            type="verify_evidence",
            toolName="FakeTextEvidenceTool",
            input={"text": "Nonce and gas explain transaction ordering and execution cost."},
            evidenceIds=["ev_1"],
        )
    )

    assert observation.status == "success"
    assert observation.facts["isSpecific"] is True
    assert "score" not in observation.facts


def test_fake_web3_tx_tool_validates_hash_shape_without_scoring() -> None:
    observation = FakeWeb3TxTool().execute(
        Action(
            type="verify_evidence",
            toolName="FakeWeb3TxTool",
            input={"hash": "0x1234567890"},
            evidenceIds=["ev_tx"],
        )
    )

    assert observation.status == "success"
    assert observation.facts["exists"] is True
    assert observation.facts["chain"] == "monad-testnet-mock"
    assert "score" not in observation.facts
