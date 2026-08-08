"use client";

import React from "react";
import { Send } from "lucide-react";
import { useMemo, useState } from "react";

import { getSafeErrorMessage } from "@/lib/api/client";
import type { MonadPluginCapabilityMetadata, PluginCapability, SubmitEvidenceRequest, SyncResponse } from "@/lib/api/contracts";

type MonadCapability = PluginCapability & {
  pluginId: "monad";
  capabilityId: "monad_learning_transaction";
};

function toMonadMetadata(capability: MonadCapability): MonadPluginCapabilityMetadata {
  return capability.metadata as MonadPluginCapabilityMetadata;
}

export function MonadEvidencePanel({
  capability,
  onSubmitEvidence
}: {
  capability: MonadCapability;
  onSubmitEvidence: (payload: SubmitEvidenceRequest) => Promise<Pick<SyncResponse, "syncPending">>;
}) {
  const metadata = useMemo(() => toMonadMetadata(capability), [capability]);
  const [walletAddress, setWalletAddress] = useState("");
  const [transactionHash, setTransactionHash] = useState("");
  const [contractAddress, setContractAddress] = useState(String(metadata.contractAddress || ""));
  const [operationExplanation, setOperationExplanation] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setMessage("");
    try {
      const response = await onSubmitEvidence({
        evidenceType: "monad_transaction",
        metadata: {
          walletAddress,
          transactionHash,
          contractAddress,
          operationExplanation
        }
      });
      setMessage(response.syncPending ? "Monad evidence saved, waiting for Agent sync." : "Monad evidence submitted.");
    } catch (error) {
      setMessage(getSafeErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel grid gap-4 p-4" aria-labelledby="monad-evidence-heading">
      <div className="grid gap-1">
        <h2 id="monad-evidence-heading" className="text-base font-semibold">Monad chain evidence</h2>
        <p className="text-sm text-slate-700">{metadata.taskDescription || "Submit a wallet transaction that calls the configured Monad teaching contract."}</p>
      </div>
      <dl className="grid gap-2 text-sm sm:grid-cols-2">
        <div><dt className="font-medium">Network</dt><dd>{String(metadata.chainName || "Monad")} ({String(metadata.chainId)})</dd></div>
        <div><dt className="font-medium">Contract</dt><dd className="break-all">{String(metadata.contractAddress || contractAddress || "Not configured")}</dd></div>
      </dl>
      <form className="grid gap-3" onSubmit={submit}>
        <div className="field">
          <label htmlFor="monad-wallet-address">Wallet address</label>
          <input id="monad-wallet-address" className="input" name="walletAddress" required value={walletAddress} onChange={(event) => setWalletAddress(event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="monad-transaction-hash">Transaction hash</label>
          <input id="monad-transaction-hash" className="input" name="transactionHash" required value={transactionHash} onChange={(event) => setTransactionHash(event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="monad-contract-address">Contract address</label>
          <input id="monad-contract-address" className="input" name="contractAddress" required value={contractAddress} onChange={(event) => setContractAddress(event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="monad-operation-explanation">Operation explanation</label>
          <textarea id="monad-operation-explanation" className="input min-h-24" name="operationExplanation" required value={operationExplanation} onChange={(event) => setOperationExplanation(event.target.value)} />
        </div>
        <button className="btn w-fit" disabled={busy} type="submit"><Send size={16} />{busy ? "Submitting..." : "Submit Monad evidence"}</button>
        <p aria-live="polite" className="text-sm text-slate-700">{message}</p>
      </form>
    </section>
  );
}

