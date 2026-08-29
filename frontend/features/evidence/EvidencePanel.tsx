"use client";

import React from "react";
import type { Evidence, ImageEvidenceCapability, ImageEvidenceResponse, SpeechTranscriptionCapabilityEnabled, SubmitEvidenceRequest, SyncResponse } from "@/lib/api/contracts";
import { ImageEvidenceForm } from "./ImageEvidenceForm";

export function EvidencePanel({
  sessionId, ownerUserId,
  submittedEvidence = [],
  imageCapability = null,
  speechCapability = null,
  onUploadImage,
  onSubmitEvidence
}: {
  sessionId: string; ownerUserId?: string; domain: string; walletAddress: string | null;
  submittedEvidence?: Evidence[];
  includeWeb3Mode?: boolean;
  imageCapability?: ImageEvidenceCapability | null;
  speechCapability?: SpeechTranscriptionCapabilityEnabled | null;
  onUploadImage?: (form: FormData) => Promise<ImageEvidenceResponse>;
  onSubmitEvidence: (payload: SubmitEvidenceRequest) => Promise<Pick<SyncResponse, "syncPending">>;
}) {
  const fallbackCapability: ImageEvidenceCapability = imageCapability ?? { capabilityId: "image_evidence", enabled: true, formats: [], maxCount: 1, maxOriginalBytes: 1, maxNormalizedBytesPerSession: 1, explanationRequired: false };
  return (
    <section className="panel grid gap-4 p-4" aria-labelledby="evidence-heading">
      <h2 id="evidence-heading" className="text-base font-semibold">Submit evidence</h2>
      <ImageEvidenceForm unified allowAttachments={Boolean(imageCapability && onUploadImage)} ownerUserId={ownerUserId} sessionId={sessionId} capability={fallbackCapability} speechCapability={speechCapability} submittedEvidence={submittedEvidence} onUpload={onUploadImage ?? (async () => { throw new Error("Image evidence is unavailable."); })} onSubmitEvidence={onSubmitEvidence} />
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
