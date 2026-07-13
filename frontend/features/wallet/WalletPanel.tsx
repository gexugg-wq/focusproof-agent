"use client";

import React from "react";
import { PlugZap, Unplug } from "lucide-react";
import { useState } from "react";
import { normalizeWalletAddress, shortenAddress } from "@/lib/wallet/config";

type EthereumProvider = {
  request: (input: { method: string; params?: unknown[] }) => Promise<unknown>;
};

declare global {
  interface Window {
    ethereum?: EthereumProvider;
  }
}

export function WalletPanel({ onWalletChange }: { onWalletChange: (address: string | null) => void }) {
  const [address, setAddress] = useState<string | null>(null);
  const [chainId, setChainId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function connect() {
    if (!window.ethereum) {
      setMessage("No injected wallet detected.");
      return;
    }
    setBusy(true);
    try {
      const accounts = await window.ethereum.request({ method: "eth_requestAccounts" });
      const chain = await window.ethereum.request({ method: "eth_chainId" });
      const first = Array.isArray(accounts) && typeof accounts[0] === "string" ? normalizeWalletAddress(accounts[0]) : null;
      setAddress(first);
      setChainId(typeof chain === "string" ? chain : null);
      onWalletChange(first);
      setMessage(first ? "Wallet connected as optional metadata." : "No wallet account returned.");
    } catch {
      setMessage("Wallet connection was rejected or failed.");
    } finally {
      setBusy(false);
    }
  }
  function disconnect() {
    setAddress(null);
    setChainId(null);
    onWalletChange(null);
    setMessage("Wallet disconnected locally.");
  }
  return (
    <section className="grid gap-3 border-t border-line pt-4" aria-labelledby="wallet-heading">
      <h2 id="wallet-heading" className="text-base font-semibold">Wallet metadata</h2>
      {address ? (
        <>
          <p className="text-sm">Connected {shortenAddress(address)} on chain {chainId ?? "unknown"}</p>
          <button className="btn secondary w-fit" type="button" onClick={disconnect}><Unplug size={16} />Disconnect wallet</button>
        </>
      ) : (
        <>
          <p className="text-sm text-slate-700">Optional for Web3 evidence. This is metadata, not identity verification.</p>
          <button className="btn secondary w-fit" disabled={busy} type="button" onClick={connect}><PlugZap size={16} />Connect wallet</button>
        </>
      )}
      <p aria-live="polite" className="text-sm text-slate-700">{message}</p>
    </section>
  );
}
