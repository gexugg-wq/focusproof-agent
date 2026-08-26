import { webcrypto } from "node:crypto";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { EvidencePanel } from "@/features/evidence/EvidencePanel";
import type { ImageEvidenceCapability } from "@/lib/api/contracts";

const capability: ImageEvidenceCapability = {
  capabilityId: "image_evidence", enabled: true,
  formats: ["image/png", "image/jpeg", "image/webp"], maxCount: 4,
  maxOriginalBytes: 10_485_760, maxNormalizedBytesPerSession: 20_971_520,
  explanationRequired: true
};
const png = (name = "diagram.png", bytes = [137, 80, 78, 71]) => new File([new Uint8Array(bytes)], name, { type: "image/png" });
const props = (override: Record<string, unknown> = {}) => ({
  sessionId: "sess_1", ownerUserId: "owner_1", domain: "general",
  walletAddress: null, submittedEvidence: [], imageCapability: capability,
  onSubmitEvidence: vi.fn().mockResolvedValue({ syncPending: false }),
  onUploadImage: vi.fn().mockResolvedValue({ evidenceId: "ev_image", mediaType: "image/png", normalizedBytes: 4, replayed: false }),
  ...override
});

beforeAll(() => {
  vi.stubGlobal("crypto", {
    randomUUID: () => webcrypto.randomUUID(),
    subtle: { digest: (algorithm: AlgorithmIdentifier, data: BufferSource) => webcrypto.subtle.digest(algorithm, Buffer.from(new Uint8Array(data as ArrayBuffer))) }
  });
});
beforeEach(() => sessionStorage.clear());

describe("unified evidence composer", () => {
  it("has one composer without text, URL, Web3, image, or voice duplicate modes", () => {
    render(<EvidencePanel {...props()} />);
    expect(screen.getAllByRole("button", { name: /submit evidence/i })).toHaveLength(1);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /image evidence/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/voice|record|coming soon/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/learning evidence/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/choose images/i)).toBeInTheDocument();
    expect(screen.getByTitle("Choose images")).toBeInTheDocument();
  });

  it.each([
    ["Notes about replay", { evidenceType: "text", textContent: "Notes about replay", metadata: {} }],
    [" https://example.com/lesson ", { evidenceType: "url", sourceUrl: "https://example.com/lesson", textContent: "", metadata: {} }],
    ["https://example.com one more word", { evidenceType: "text", textContent: "https://example.com one more word", metadata: {} }]
  ])("classifies composer content predictably: %s", async (content, payload) => {
    const submit = vi.fn().mockResolvedValue({ syncPending: false });
    render(<EvidencePanel {...props({ imageCapability: null, onUploadImage: undefined, onSubmitEvidence: submit })} />);
    await userEvent.type(screen.getByLabelText(/learning evidence/i), content);
    await userEvent.click(screen.getByRole("button", { name: /submit evidence/i }));
    expect(submit).toHaveBeenCalledWith(payload);
    expect(screen.getByLabelText(/learning evidence/i)).toHaveValue("");
  });

  it("accepts selected, dropped, and clipboard image attachments and removes one", async () => {
    render(<EvidencePanel {...props()} />);
    const input = screen.getByLabelText(/choose images/i);
    await userEvent.upload(input, png("selected.png"));
    fireEvent.drop(screen.getByTestId("evidence-dropzone"), { dataTransfer: { files: [png("dropped.png", [1, 2, 3, 4])] } });
    fireEvent.paste(screen.getByLabelText(/learning evidence/i), { clipboardData: { files: [png("pasted.png", [4, 3, 2, 1])], getData: () => "" } });
    expect(screen.getByText("selected.png")).toBeInTheDocument();
    expect(screen.getByText("dropped.png")).toBeInTheDocument();
    expect(screen.getByText("pasted.png")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /remove dropped.png/i }));
    expect(screen.queryByText("dropped.png")).not.toBeInTheDocument();
  });

  it("uses text only as image explanation and never creates duplicate text evidence", async () => {
    const submit = vi.fn();
    const upload = vi.fn().mockResolvedValue({ evidenceId: "ev_image", mediaType: "image/png", normalizedBytes: 4, replayed: false });
    render(<EvidencePanel {...props({ onSubmitEvidence: submit, onUploadImage: upload })} />);
    await userEvent.upload(screen.getByLabelText(/choose images/i), png());
    await userEvent.type(screen.getByLabelText(/learning evidence/i), "The diagram explains replay.");
    await userEvent.click(screen.getByRole("button", { name: /submit evidence/i }));
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
    expect((upload.mock.calls[0][0] as FormData).get("explanation")).toBe("The diagram explains replay.");
    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/learning evidence/i)).toHaveValue("");
    expect(screen.queryByText("diagram.png")).not.toBeInTheDocument();
  });

  it("removes confirmed files but retains unknown and unattempted files for safe retry", async () => {
    const calls: string[] = [];
    const upload = vi.fn(async (form: FormData) => {
      const name = (form.get("file") as File).name;
      calls.push(name);
      if (name === "second.png" && calls.filter((item) => item === name).length === 1) throw Object.assign(new Error("Network result unknown"), { retryable: true });
      return { evidenceId: `ev_${name}`, mediaType: "image/png", normalizedBytes: 4, replayed: false };
    });
    render(<EvidencePanel {...props({ onUploadImage: upload })} />);
    await userEvent.upload(screen.getByLabelText(/choose images/i), [png("first.png"), png("second.png", [1, 2, 3, 4]), png("third.png", [4, 3, 2, 1])]);
    await userEvent.type(screen.getByLabelText(/learning evidence/i), "Three diagrams.");
    await userEvent.click(screen.getByRole("button", { name: /submit evidence/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/unknown/i);
    expect(screen.queryByText("first.png")).not.toBeInTheDocument();
    expect(screen.getByText("second.png")).toBeInTheDocument();
    expect(screen.getByText("third.png")).toBeInTheDocument();
    expect(screen.getByLabelText(/learning evidence/i)).toHaveValue("Three diagrams.");
    await userEvent.click(screen.getByRole("button", { name: /submit evidence/i }));
    await waitFor(() => expect(calls).toEqual(["first.png", "second.png", "second.png", "third.png"]));
  });
});
