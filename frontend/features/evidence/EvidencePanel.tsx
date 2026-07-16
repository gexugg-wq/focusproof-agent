"use client";

import React from "react";
import { FileText, Link, Send, WalletCards } from "lucide-react";
import { useState } from "react";
import type { Evidence, SubmitEvidenceRequest, SyncResponse } from "@/lib/api/contracts";
import { getSafeErrorMessage } from "@/lib/api/client";
import { buildEvidencePayload } from "@/lib/evidence/payloads";

type EvidenceMode = "text" | "url" | "web3";

export function EvidencePanel({
  sessionId,
  domain,
  walletAddress,
  submittedEvidence = [],
  onSubmitEvidence
}: {
  sessionId: string;
  domain: string;
  walletAddress: string | null;
  submittedEvidence?: Evidence[];
  onSubmitEvidence: (payload: SubmitEvidenceRequest) => Promise<Pick<SyncResponse, "syncPending">>;
}) {
  const [mode, setMode] = useState<EvidenceMode>(domain.toLowerCase() === "web3" ? "web3" : "text");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    if (busy) return;
    setBusy(true);
    setMessage("");
    try {
      const payload = mode === "text"
        ? buildEvidencePayload({ mode, textContent: String(formData.get("textContent") || "") })
        : mode === "url"
          ? buildEvidencePayload({ mode, sourceUrl: String(formData.get("sourceUrl") || ""), textContent: String(formData.get("urlExplanation") || "") })
          : buildEvidencePayload({
              mode,
              txHash: String(formData.get("txHash") || ""),
              contractAddress: String(formData.get("contractAddress") || ""),
              chainId: String(formData.get("chainId") || ""),
              sourceUrl: String(formData.get("explorerUrl") || ""),
              explanation: String(formData.get("web3Explanation") || ""),
              walletAddress
            });
      const response = await onSubmitEvidence(payload);
      setMessage(response.syncPending ? "Evidence saved, waiting for Agent sync." : "Evidence submitted.");
    } catch (error) {
      setMessage(getSafeErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel grid gap-4 p-4" aria-labelledby="evidence-heading">
      <h2 id="evidence-heading" className="text-base font-semibold">Evidence</h2>
      <div role="tablist" aria-label="Evidence type" className="flex flex-wrap gap-2">
        <button className={"btn secondary " + (mode === "text" ? "border-ink" : "")} role="tab" aria-selected={mode === "text"} onClick={() => setMode("text")} type="button"><FileText size={16} />Text</button>
        <button className={"btn secondary " + (mode === "url" ? "border-ink" : "")} role="tab" aria-selected={mode === "url"} onClick={() => setMode("url")} type="button"><Link size={16} />URL</button>
        <button className={"btn secondary " + (mode === "web3" ? "border-ink" : "")} role="tab" aria-selected={mode === "web3"} onClick={() => setMode("web3")} type="button"><WalletCards size={16} />Web3</button>
      </div>
      <form onSubmit={submit} className="grid gap-3">
        {mode === "text" ? (
          <div className="field"><label htmlFor={sessionId + "-text"}>Learning notes, explanation, code, or error record</label><textarea id={sessionId + "-text"} name="textContent" className="input min-h-28" required /></div>
        ) : null}
        {mode === "url" ? (
          <>
            <div className="field"><label htmlFor={sessionId + "-url"}>Source URL</label><input id={sessionId + "-url"} name="sourceUrl" className="input" required /></div>
            <div className="field"><label htmlFor={sessionId + "-url-exp"}>Explanation of the linked content</label><textarea id={sessionId + "-url-exp"} name="urlExplanation" className="input min-h-24" required /></div>
          </>
        ) : null}
        {mode === "web3" ? (
          <>
            <div className="field"><label htmlFor={sessionId + "-tx"}>Transaction hash</label><input id={sessionId + "-tx"} name="txHash" className="input" required /></div>
            <div className="field"><label htmlFor={sessionId + "-contract"}>Contract address optional</label><input id={sessionId + "-contract"} name="contractAddress" className="input" /></div>
            <div className="field"><label htmlFor={sessionId + "-chain"}>Chain ID or network name</label><input id={sessionId + "-chain"} name="chainId" className="input" required /></div>
            <div className="field"><label htmlFor={sessionId + "-explorer"}>Block explorer URL optional</label><input id={sessionId + "-explorer"} name="explorerUrl" className="input" /></div>
            <div className="field"><label htmlFor={sessionId + "-web3-exp"}>What did this operation complete?</label><textarea id={sessionId + "-web3-exp"} name="web3Explanation" className="input min-h-24" required /></div>
          </>
        ) : null}
        <button className="btn w-fit" disabled={busy} type="submit"><Send size={16} />{busy ? "Submitting..." : "Submit evidence"}</button>
        <p aria-live="polite" className="text-sm text-slate-700">{message}</p>
      </form>
      {submittedEvidence.length > 0 ? (
        <div aria-label="Submitted evidence" className="grid gap-2">
          <h3 className="font-medium">Submitted evidence</h3>
          <ol className="grid gap-2">
            {submittedEvidence.map((evidence) => (
              <li className="rounded-md border border-line p-3 text-sm" key={evidence.evidenceId}>
                <p className="font-medium">{evidence.evidenceType}</p>
                {evidence.textContent ? <p>{evidence.textContent}</p> : null}
                {evidence.sourceUrl ? <p>{evidence.sourceUrl}</p> : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </section>
  );
}
