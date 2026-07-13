import type { SubmitEvidenceRequest } from "@/lib/api/contracts";

type TextEvidenceInput = { mode: "text"; textContent: string };
type UrlEvidenceInput = { mode: "url"; sourceUrl: string; textContent: string };
type Web3EvidenceInput = {
  mode: "web3";
  txHash: string;
  chainId: string;
  explanation: string;
  contractAddress?: string;
  sourceUrl?: string;
  walletAddress?: string | null;
};

export type EvidenceFormInput = TextEvidenceInput | UrlEvidenceInput | Web3EvidenceInput;

function withoutEmptyValues(values: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== ""));
}

export function buildEvidencePayload(input: EvidenceFormInput): SubmitEvidenceRequest {
  if (input.mode === "text") {
    return { evidenceType: "text", textContent: input.textContent, metadata: {} };
  }
  if (input.mode === "url") {
    return { evidenceType: "url", sourceUrl: input.sourceUrl, textContent: input.textContent, metadata: {} };
  }
  return {
    evidenceType: "web3",
    textContent: input.explanation,
    sourceUrl: input.sourceUrl || undefined,
    metadata: withoutEmptyValues({
      txHash: input.txHash,
      contractAddress: input.contractAddress,
      chainId: input.chainId,
      walletAddress: input.walletAddress
    })
  };
}
