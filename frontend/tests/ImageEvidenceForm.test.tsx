import { webcrypto } from "node:crypto";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { ImageEvidenceForm } from "@/features/evidence/ImageEvidenceForm";
import { ApiError } from "@/lib/api/errors";
import type { ImageEvidenceCapability } from "@/lib/api/contracts";

const capability: ImageEvidenceCapability = { capabilityId: "image_evidence", enabled: true, formats: ["image/png", "image/jpeg", "image/webp"], maxCount: 4, maxOriginalBytes: 10_485_760, maxNormalizedBytesPerSession: 20_971_520, explanationRequired: true };
const image = (bytes: number[], name = "diagram.png") => new File([new Uint8Array(bytes)], name, { type: "image/png" });
const png = (name = "diagram.png") => image([137, 80, 78, 71], name);
const pendingRecords = () => Array.from({ length: sessionStorage.length }, (_, index) => sessionStorage.key(index))
  .filter((key): key is string => key?.startsWith("focusproof:image-intent:v1:") === true)
  .map((key) => ({ key, value: JSON.parse(sessionStorage.getItem(key)!) as Record<string, unknown> }));
const uploadKey = (form: FormData) => String(form.get("idempotency_key"));
const explanation = /explain what these images show/i;
const chooseImages = /choose images/i;
const submit = /upload image evidence/i;

beforeAll(() => {
  vi.stubGlobal("crypto", {
    randomUUID: () => webcrypto.randomUUID(),
    subtle: {
      digest: (algorithm: AlgorithmIdentifier, data: BufferSource) => webcrypto.subtle.digest(algorithm, Buffer.from(new Uint8Array(data as ArrayBuffer)))
    }
  });
});

describe("ImageEvidenceForm", () => {
  it("renders server-provided formats and limits with accessible controls", () => {
    render(<ImageEvidenceForm sessionId="sess_1" capability={capability} submittedEvidence={[]} onUpload={vi.fn()} />);
    expect(screen.getByText(/PNG, JPEG, WEBP/i)).toBeInTheDocument();
    expect(screen.getByText(/4 images/i)).toBeInTheDocument();
    expect(screen.getByText(/10 MiB each/i)).toBeInTheDocument();
    expect(screen.getByText(/20 MiB normalized total/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/choose images/i)).toHaveAttribute("accept", "image/png,image/jpeg,image/webp");
    expect(screen.getByLabelText(/explain what these images show/i)).toBeRequired();
  });

  it("selects and removes pending files without persisting their bytes", async () => {
    render(<ImageEvidenceForm sessionId="sess_1" capability={capability} submittedEvidence={[]} onUpload={vi.fn()} />);
    await userEvent.upload(screen.getByLabelText(/choose images/i), [png("diagram.png"), png("notes.png")]);
    expect(screen.getByText("diagram.png")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /remove diagram.png/i }));
    expect(screen.queryByText("diagram.png")).not.toBeInTheDocument();
    expect(localStorage.length).toBe(0);
  });

  it("prevents duplicate submits and reports success", async () => {
    let finish!: (value: { evidenceId: string; mediaType: string; normalizedBytes: number; replayed: boolean }) => void;
    const upload = vi.fn(() => new Promise<{ evidenceId: string; mediaType: string; normalizedBytes: number; replayed: boolean }>((resolve) => { finish = resolve; }));
    render(<ImageEvidenceForm sessionId="sess_1" capability={capability} submittedEvidence={[]} onUpload={upload} />);
    await userEvent.upload(screen.getByLabelText(/choose images/i), png());
    await userEvent.type(screen.getByLabelText(/explain what these images show/i), "The diagram connects the event log to its derived view.");
    const button = screen.getByRole("button", { name: /upload image evidence/i });
    await userEvent.click(button);
    expect(button).toBeDisabled();
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
    finish({ evidenceId: "ev_image", mediaType: "image/png", normalizedBytes: 4, replayed: false });
    expect(await screen.findByText(/image evidence uploaded/i)).toBeInTheDocument();
  });

  it("linearizes two submit events dispatched before React rerenders", async () => {
    let finish!: (value: { evidenceId: string; mediaType: string; normalizedBytes: number; replayed: boolean }) => void;
    const upload = vi.fn(() => new Promise<{ evidenceId: string; mediaType: string; normalizedBytes: number; replayed: boolean }>((resolve) => { finish = resolve; }));
    render(<ImageEvidenceForm sessionId="sess_1" capability={capability} submittedEvidence={[]} onUpload={upload} />);
    await userEvent.upload(screen.getByLabelText(/choose images/i), png());
    await userEvent.type(screen.getByLabelText(/explain what these images show/i), "The event graph has one image intent.");
    const form = screen.getByRole("button", { name: /upload image evidence/i }).closest("form");
    expect(form).not.toBeNull();

    fireEvent.submit(form!);
    fireEvent.submit(form!);

    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
    finish({ evidenceId: "ev_image", mediaType: "image/png", normalizedBytes: 4, replayed: false });
    expect(await screen.findByText(/image evidence uploaded/i)).toBeInTheDocument();
  });

  it("retains pending input after retryable failure and retries", async () => {
    const upload = vi.fn().mockRejectedValueOnce(Object.assign(new Error("Upload interrupted. Please retry."), { retryable: true })).mockResolvedValueOnce({ evidenceId: "ev_image", mediaType: "image/png", normalizedBytes: 4, replayed: false });
    render(<ImageEvidenceForm sessionId="sess_1" capability={capability} submittedEvidence={[]} onUpload={upload} />);
    await userEvent.upload(screen.getByLabelText(/choose images/i), png());
    await userEvent.type(screen.getByLabelText(/explain what these images show/i), "A causal diagram of the topic.");
    await userEvent.click(screen.getByRole("button", { name: /upload image evidence/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/interrupted/i);
    expect(screen.getByText("diagram.png")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /retry upload/i }));
    expect(await screen.findByText(/image evidence uploaded/i)).toBeInTheDocument();
    expect(upload).toHaveBeenCalledTimes(2);
  });

  it("restores only submitted server evidence after remount", () => {
    const submitted = [{ evidenceId: "ev_image", evidenceType: "image", contentHash: "sha256:safe", textContent: "A causal diagram.", sourceUrl: null, metadata: { mediaType: "image/png", normalizedBytes: 4096 } }];
    const { unmount } = render(<ImageEvidenceForm sessionId="sess_1" capability={capability} submittedEvidence={submitted} onUpload={vi.fn()} />);
    expect(screen.getByText(/A causal diagram/i)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    unmount();
    render(<ImageEvidenceForm sessionId="sess_1" capability={capability} submittedEvidence={submitted} onUpload={vi.fn()} />);
    expect(screen.getByText(/A causal diagram/i)).toBeInTheDocument();
  });
  it("retries from the failed file without replaying successful files", async () => {
    const calls: string[] = [];
    const upload = vi.fn(async (form: FormData) => {
      const name = (form.get("file") as File).name;
      calls.push(name);
      if (name === "second.png" && calls.filter((value) => value === name).length === 1) throw Object.assign(new Error("Temporary failure"), { retryable: true });
      return { evidenceId: `ev_${name}`, mediaType: "image/png", normalizedBytes: 4, replayed: false };
    });
    render(<ImageEvidenceForm sessionId="sess_1" capability={capability} submittedEvidence={[]} onUpload={upload} />);
    await userEvent.upload(screen.getByLabelText(/choose images/i), [png("first.png"), png("second.png")]);
    await userEvent.type(screen.getByLabelText(/explain what these images show/i), "Two related diagrams.");
    await userEvent.click(screen.getByRole("button", { name: /upload image evidence/i }));
    expect(await screen.findByRole("button", { name: /retry upload/i })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /retry upload/i }));
    expect(await screen.findByText(/image evidence uploaded/i)).toBeVisible();
    expect(calls).toEqual(["first.png", "second.png", "second.png"]);
  });

  it("keeps the failed file retryable after removing the earlier successful file", async () => {
    const calls: Array<{ name: string; key: string }> = [];
    const upload = vi.fn(async (form: FormData) => {
      const name = (form.get("file") as File).name;
      const key = String(form.get("idempotency_key"));
      calls.push({ name, key });
      if (name === "second.png" && calls.filter((call) => call.name === name).length === 1) throw Object.assign(new Error("Temporary failure"), { retryable: true });
      return { evidenceId: `ev_${name}`, mediaType: "image/png", normalizedBytes: 4, replayed: false };
    });
    render(<ImageEvidenceForm sessionId="sess_1" capability={capability} submittedEvidence={[]} onUpload={upload} />);
    await userEvent.upload(screen.getByLabelText(/choose images/i), [png("first.png"), png("second.png")]);
    await userEvent.type(screen.getByLabelText(/explain what these images show/i), "Two related diagrams.");
    await userEvent.click(screen.getByRole("button", { name: /upload image evidence/i }));
    expect(await screen.findByRole("button", { name: /retry upload/i })).toBeVisible();
    expect(screen.queryByRole("button", { name: /remove first.png/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /remove second.png/i })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /retry upload/i }));
    expect(await screen.findByText(/image evidence uploaded/i)).toBeVisible();
    expect(calls.map((call) => call.name)).toEqual(["first.png", "second.png", "second.png"]);
    expect(calls[2].key).toBe(calls[1].key);
  });

  it("reuses an unknown pending intent after remount instead of creating a second identity", async () => {
    const keys: string[] = [];
    const interrupted = vi.fn(async (form: FormData) => {
      keys.push(String(form.get("idempotency_key")));
      throw Object.assign(new Error("Network result unknown"), { retryable: true });
    });
    const first = render(<ImageEvidenceForm sessionId="sess_1" capability={capability} submittedEvidence={[]} onUpload={interrupted} />);
    await userEvent.upload(screen.getByLabelText(/choose images/i), png());
    await userEvent.type(screen.getByLabelText(/explain what these images show/i), "A stable retry intent.");
    await userEvent.click(screen.getByRole("button", { name: /upload image evidence/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/unknown/i);
    const records = pendingRecords();
    expect(records).toHaveLength(1);
    const stored = JSON.stringify(records[0].value);
    expect(stored).not.toBeNull();
    expect(stored).not.toContain("diagram.png");
    expect(stored).not.toContain("A stable retry intent.");
    expect(Object.keys(records[0].value).sort()).toEqual([
      "baseKey", "createdAt", "intentFingerprint", "ownerUserId", "schemaVersion", "sessionId"
    ]);
    first.unmount();

    const recovered = vi.fn(async (form: FormData) => {
      keys.push(String(form.get("idempotency_key")));
      return { evidenceId: "ev_same", mediaType: "image/png", normalizedBytes: 4, replayed: true };
    });
    render(<ImageEvidenceForm sessionId="sess_1" capability={capability} submittedEvidence={[]} onUpload={recovered} />);
    await userEvent.upload(screen.getByLabelText(/choose images/i), png());
    await userEvent.type(screen.getByLabelText(/explain what these images show/i), "A stable retry intent.");
    await userEvent.click(screen.getByRole("button", { name: /upload image evidence/i }));

    expect(await screen.findByText(/image evidence uploaded/i)).toBeVisible();
    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(keys[0]);
    expect(sessionStorage.length).toBe(0);
  });

  it("fails safe on expired or corrupt pending intent storage", async () => {
    const firstKeys: string[] = [];
    const interrupted = vi.fn(async (form: FormData) => {
      firstKeys.push(String(form.get("idempotency_key")));
      throw Object.assign(new Error("Network result unknown"), { retryable: true });
    });
    const first = render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_ttl" capability={capability} submittedEvidence={[]} onUpload={interrupted} />);
    await userEvent.upload(screen.getByLabelText(/choose images/i), png());
    await userEvent.type(screen.getByLabelText(/explain what these images show/i), "TTL-bound intent.");
    await userEvent.click(screen.getByRole("button", { name: /upload image evidence/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/unknown/i);
    const ttlRecords = pendingRecords();
    expect(ttlRecords).toHaveLength(1);
    const key = ttlRecords[0].key;
    const expired = ttlRecords[0].value;
    expired.createdAt = Date.now() - (25 * 60 * 60 * 1000);
    sessionStorage.setItem(key, JSON.stringify(expired));
    first.unmount();

    const secondKeys: string[] = [];
    const recovered = vi.fn(async (form: FormData) => {
      secondKeys.push(String(form.get("idempotency_key")));
      return { evidenceId: "ev_new", mediaType: "image/png", normalizedBytes: 4, replayed: false };
    });
    render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_ttl" capability={capability} submittedEvidence={[]} onUpload={recovered} />);
    await userEvent.upload(screen.getByLabelText(/choose images/i), png());
    await userEvent.type(screen.getByLabelText(/explain what these images show/i), "TTL-bound intent.");
    await userEvent.click(screen.getByRole("button", { name: /upload image evidence/i }));
    expect(await screen.findByText(/image evidence uploaded/i)).toBeVisible();
    expect(secondKeys[0]).not.toBe(firstKeys[0]);

    sessionStorage.setItem("focusproof:image-intent:v1:sess_bad", "{not-json");
    const corruptKeys: string[] = [];
    render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_bad" capability={capability} submittedEvidence={[]} onUpload={async (form) => {
      corruptKeys.push(String(form.get("idempotency_key")));
      return { evidenceId: "ev_clean", mediaType: "image/png", normalizedBytes: 4, replayed: false };
    }} />);
    await userEvent.upload(screen.getAllByLabelText(/choose images/i).at(-1)!, png());
    await userEvent.type(screen.getAllByLabelText(/explain what these images show/i).at(-1)!, "Corrupt storage is ignored.");
    await userEvent.click(screen.getAllByRole("button", { name: /upload image evidence/i }).at(-1)!);
    await waitFor(() => expect(corruptKeys).toHaveLength(1));
    expect(corruptKeys[0]).not.toContain("not-json");
    expect(sessionStorage.getItem("focusproof:image-intent:v1:sess_bad")).toBeNull();
  });

  it("recovers only the unknown file after an earlier batch file succeeded", async () => {
    sessionStorage.clear();
    const calls: Array<{ name: string; key: string }> = [];
    const firstUpload = vi.fn(async (form: FormData) => {
      const name = (form.get("file") as File).name;
      calls.push({ name, key: uploadKey(form) });
      if (name === "second.png") throw Object.assign(new Error("Network result unknown"), { retryable: true });
      return { evidenceId: "ev_first", mediaType: "image/png", normalizedBytes: 4, replayed: false };
    });
    const first = render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_batch" capability={capability} submittedEvidence={[]} onUpload={firstUpload} />);
    await userEvent.upload(screen.getByLabelText(chooseImages), [image([1, 2, 3, 4], "first.png"), image([5, 6, 7, 8], "second.png")]);
    await userEvent.type(screen.getByLabelText(explanation), "Two independent image intents.");
    await userEvent.click(screen.getByRole("button", { name: submit }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/unknown/i);
    expect(calls.map((call) => call.name)).toEqual(["first.png", "second.png"]);
    first.unmount();

    const recovered = vi.fn(async (form: FormData) => {
      calls.push({ name: (form.get("file") as File).name, key: uploadKey(form) });
      return { evidenceId: "ev_second", mediaType: "image/png", normalizedBytes: 4, replayed: true };
    });
    render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_batch" capability={capability} submittedEvidence={[]} onUpload={recovered} />);
    await userEvent.upload(screen.getByLabelText(chooseImages), image([5, 6, 7, 8], "second.png"));
    await userEvent.type(screen.getByLabelText(explanation), "Two independent image intents.");
    await userEvent.click(screen.getByRole("button", { name: submit }));
    expect(await screen.findByText(/image evidence uploaded/i)).toBeVisible();
    expect(calls[2].key).toBe(calls[1].key);
  });

  it("keeps two unknown intents in the same session independently recoverable", async () => {
    sessionStorage.clear();
    const unknownKeys: string[] = [];
    const failUnknown = vi.fn(async (form: FormData) => {
      unknownKeys.push(uploadKey(form));
      throw Object.assign(new Error("Network result unknown"), { retryable: true });
    });
    const first = render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_multi" capability={capability} submittedEvidence={[]} onUpload={failUnknown} />);
    await userEvent.upload(screen.getByLabelText(chooseImages), image([1, 1, 1, 1], "one.png"));
    await userEvent.type(screen.getByLabelText(explanation), "First pending intent.");
    await userEvent.click(screen.getByRole("button", { name: submit }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/unknown/i);
    first.unmount();

    const second = render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_multi" capability={capability} submittedEvidence={[]} onUpload={failUnknown} />);
    await userEvent.upload(screen.getByLabelText(chooseImages), image([2, 2, 2, 2], "two.png"));
    await userEvent.type(screen.getByLabelText(explanation), "Second pending intent.");
    await userEvent.click(screen.getByRole("button", { name: submit }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/unknown/i);
    second.unmount();

    const records = pendingRecords();
    expect(records).toHaveLength(2);
    for (const record of records) {
      expect(Object.keys(record.value).sort()).toEqual(["baseKey", "createdAt", "intentFingerprint", "ownerUserId", "schemaVersion", "sessionId"]);
      expect(JSON.stringify(record.value)).not.toMatch(/one\.png|two\.png|First pending|Second pending/);
    }

    const recoveredKeys: string[] = [];
    const recover = vi.fn(async (form: FormData) => {
      recoveredKeys.push(uploadKey(form));
      return { evidenceId: "ev_recovered", mediaType: "image/png", normalizedBytes: 4, replayed: true };
    });
    const retryFirst = render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_multi" capability={capability} submittedEvidence={[]} onUpload={recover} />);
    await userEvent.upload(screen.getByLabelText(chooseImages), image([1, 1, 1, 1], "renamed-one.png"));
    await userEvent.type(screen.getByLabelText(explanation), "First pending intent.");
    await userEvent.click(screen.getByRole("button", { name: submit }));
    expect(await screen.findByText(/image evidence uploaded/i)).toBeVisible();
    retryFirst.unmount();

    render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_multi" capability={capability} submittedEvidence={[]} onUpload={recover} />);
    await userEvent.upload(screen.getByLabelText(chooseImages), image([2, 2, 2, 2], "renamed-two.png"));
    await userEvent.type(screen.getByLabelText(explanation), "Second pending intent.");
    await userEvent.click(screen.getByRole("button", { name: submit }));
    expect(await screen.findByText(/image evidence uploaded/i)).toBeVisible();
    expect(recoveredKeys).toEqual(unknownKeys);
    expect(pendingRecords()).toHaveLength(0);
  });

  it("derives a bounded request key from content rather than filename", async () => {
    sessionStorage.clear();
    const keys: string[] = [];
    const unknown = vi.fn(async (form: FormData) => {
      keys.push(uploadKey(form));
      throw Object.assign(new Error("Network result unknown"), { retryable: true });
    });
    const first = render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_rename" capability={capability} submittedEvidence={[]} onUpload={unknown} />);
    await userEvent.upload(screen.getByLabelText(chooseImages), image([9, 8, 7, 6], "before.png"));
    await userEvent.type(screen.getByLabelText(explanation), "Stable content identity.");
    await userEvent.click(screen.getByRole("button", { name: submit }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/unknown/i);
    first.unmount();

    render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_rename" capability={capability} submittedEvidence={[]} onUpload={async (form) => {
      keys.push(uploadKey(form));
      return { evidenceId: "ev_same", mediaType: "image/png", normalizedBytes: 4, replayed: true };
    }} />);
    await userEvent.upload(screen.getByLabelText(chooseImages), image([9, 8, 7, 6], "after.png"));
    await userEvent.type(screen.getByLabelText(explanation), "Stable content identity.");
    await userEvent.click(screen.getByRole("button", { name: submit }));
    expect(await screen.findByText(/image evidence uploaded/i)).toBeVisible();
    expect(keys[1]).toBe(keys[0]);
    expect(keys[0]).toMatch(/^img_[0-9a-f]{64}$/);
    expect(keys[0]).not.toMatch(/before|after/i);
    expect(keys[0].length).toBeLessThanOrEqual(255);
  });

  it("uses different request keys for equal-name equal-size files with different content", async () => {
    sessionStorage.clear();
    const keys: string[] = [];
    render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_content" capability={capability} submittedEvidence={[]} onUpload={async (form) => {
      keys.push(uploadKey(form));
      return { evidenceId: `ev_${keys.length}`, mediaType: "image/png", normalizedBytes: 4, replayed: false };
    }} />);
    await userEvent.upload(screen.getByLabelText(chooseImages), [image([1, 2, 3, 4], "same.png"), image([4, 3, 2, 1], "same.png")]);
    await userEvent.type(screen.getByLabelText(explanation), "Different image contents.");
    await userEvent.click(screen.getByRole("button", { name: submit }));
    expect(await screen.findByText(/image evidence uploaded/i)).toBeVisible();
    expect(keys).toHaveLength(2);
    expect(keys[1]).not.toBe(keys[0]);
  });

  it("rotates identity after success so a deliberate repeat creates new evidence", async () => {
    sessionStorage.clear();
    const keys: string[] = [];
    render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_repeat" capability={capability} submittedEvidence={[]} onUpload={async (form) => {
      keys.push(uploadKey(form));
      return { evidenceId: `ev_${keys.length}`, mediaType: "image/png", normalizedBytes: 4, replayed: false };
    }} />);
    for (let attempt = 0; attempt < 2; attempt += 1) {
      await userEvent.upload(screen.getByLabelText(chooseImages), png());
      await userEvent.type(screen.getByLabelText(explanation), "A deliberate repeated submission.");
      await userEvent.click(screen.getByRole("button", { name: submit }));
      await waitFor(() => expect(keys).toHaveLength(attempt + 1));
    }
    expect(keys[1]).not.toBe(keys[0]);
  });

  it("rotates identity after deterministic failure so a later action is new", async () => {
    sessionStorage.clear();
    const keys: string[] = [];
    const upload = vi.fn(async (form: FormData) => {
      keys.push(uploadKey(form));
      if (keys.length === 1) throw new ApiError({ status: 413, code: "media_too_large", retryable: false, message: "The selected image is too large." });
      return { evidenceId: "ev_new", mediaType: "image/png", normalizedBytes: 4, replayed: false };
    });
    render(<ImageEvidenceForm ownerUserId="owner_1" sessionId="sess_deterministic" capability={capability} submittedEvidence={[]} onUpload={upload} />);
    await userEvent.upload(screen.getByLabelText(chooseImages), png());
    await userEvent.type(screen.getByLabelText(explanation), "Retry after a definitive rejection.");
    await userEvent.click(screen.getByRole("button", { name: submit }));
    expect(await screen.findByRole("status")).toHaveTextContent(/too large/i);
    await userEvent.click(screen.getByRole("button", { name: submit }));
    expect(await screen.findByText(/image evidence uploaded/i)).toBeVisible();
    expect(keys).toHaveLength(2);
    expect(keys[1]).not.toBe(keys[0]);
  });

  it.each([
    [new File([new Uint8Array([1])], "proof.gif", { type: "image/gif" }), /not supported/i],
    [new File([new Uint8Array(10_485_761)], "huge.png", { type: "image/png" }), /too large/i]
  ])("announces rejected file selection", async (file, expected) => {
    render(<ImageEvidenceForm sessionId="sess_1" capability={capability} submittedEvidence={[]} onUpload={vi.fn()} />);
    await userEvent.upload(screen.getByLabelText(/choose images/i), file, { applyAccept: false });
    expect(await screen.findByRole("alert")).toHaveTextContent(expected);
  });
});
