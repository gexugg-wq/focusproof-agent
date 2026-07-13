import { beforeEach, describe, expect, it } from "vitest";
import { buildEvidencePayload } from "@/lib/evidence/payloads";
import { loadRecentSession, saveRecentSession } from "@/lib/storage/recent-sessions";

describe("evidence payloads", () => {
  it("builds text, URL, and Web3 evidence requests", () => {
    expect(buildEvidencePayload({ mode: "text", textContent: "notes" })).toEqual({ evidenceType: "text", textContent: "notes", metadata: {} });
    expect(buildEvidencePayload({ mode: "url", sourceUrl: "https://example.com", textContent: "summary" })).toEqual({ evidenceType: "url", sourceUrl: "https://example.com", textContent: "summary", metadata: {} });
    expect(buildEvidencePayload({ mode: "web3", txHash: "0xabc", chainId: "10143", explanation: "deployed", walletAddress: "0x123" })).toEqual({ evidenceType: "web3", textContent: "deployed", metadata: { txHash: "0xabc", chainId: "10143", walletAddress: "0x123" } });
  });

  it("allows Web3 evidence without a wallet address", () => {
    expect(buildEvidencePayload({ mode: "web3", txHash: "0xabc", chainId: "10143", explanation: "called a contract" }).metadata).not.toHaveProperty("walletAddress");
  });
});

describe("recent session storage", () => {
  beforeEach(() => localStorage.clear());

  it("stores only recent session metadata", () => {
    saveRecentSession({ sessionId: "sess_1", title: "Event logs", domain: "general", visitedAt: "2026-07-13T00:00:00.000Z" });
    expect(loadRecentSession()).toEqual({ sessionId: "sess_1", title: "Event logs", domain: "general", visitedAt: "2026-07-13T00:00:00.000Z" });
    expect(localStorage.getItem("focusproof.recentSession")).not.toContain("evidence");
  });
});
