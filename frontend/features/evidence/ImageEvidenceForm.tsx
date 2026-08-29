"use client";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { ImagePlus, Paperclip, Send, Trash2, Upload } from "lucide-react";
import type { Evidence, ImageEvidenceCapability, ImageEvidenceResponse, SpeechTranscriptionCapabilityEnabled, SubmitEvidenceRequest, SyncResponse } from "@/lib/api/contracts";
import { getSafeErrorMessage, isApiError } from "@/lib/api/client";
import { SpeechRecorderControl } from "./SpeechRecorderControl";

const mib = 1024 * 1024;
const labelForFormat = (format: string) => format.split("/")[1]?.replace("jpeg", "JPEG").toUpperCase() ?? format;

const pendingSchemaVersion = 1;
const pendingTtlMs = 24 * 60 * 60 * 1000;
const pendingNamespace = "focusproof:image-intent:v1";
type PendingIntent = { schemaVersion: number; ownerUserId: string; sessionId: string; intentFingerprint: string; baseKey: string; createdAt: number };
const storageKey = (sessionId: string, fingerprint: string) => `${pendingNamespace}:${sessionId}:${fingerprint}`;
const sha256 = async (value: BufferSource) => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", value))).map((byte) => byte.toString(16).padStart(2, "0")).join("");
const readFile = (file: File) => new Promise<ArrayBuffer>((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result as ArrayBuffer);
  reader.onerror = () => reject(reader.error ?? new Error("Image fingerprint failed"));
  reader.onabort = () => reject(new Error("Image fingerprint aborted"));
  reader.readAsArrayBuffer(file);
});

const intentFingerprint = async (ownerUserId: string, sessionId: string, file: File, explanation: string) => {
  const fileFacts = { contentHash: await sha256(await readFile(file)), mediaType: file.type, size: file.size };
  return sha256(new TextEncoder().encode(JSON.stringify({ ownerUserId, sessionId, explanation, file: fileFacts })));
};
const requestKey = async (baseKey: string, fingerprint: string) => `img_${await sha256(new TextEncoder().encode(`${baseKey}:${fingerprint}`))}`;
const clearPending = (key: string) => { try { sessionStorage.removeItem(key); } catch { return; } };
const readPending = (key: string, ownerUserId: string, sessionId: string, fingerprint: string): PendingIntent | null => {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<PendingIntent>;
    const allowedFields = ["baseKey", "createdAt", "intentFingerprint", "ownerUserId", "schemaVersion", "sessionId"];
    if (Object.keys(value).sort().join(",") !== allowedFields.join(",") || value.schemaVersion !== pendingSchemaVersion || value.ownerUserId !== ownerUserId || value.sessionId !== sessionId || value.intentFingerprint !== fingerprint || typeof value.baseKey !== "string" || typeof value.createdAt !== "number" || Date.now() - value.createdAt > pendingTtlMs || value.createdAt > Date.now()) {
      clearPending(key);
      return null;
    }
    return value as PendingIntent;
  } catch {
    clearPending(key);
    return null;
  }
};
const writePending = (key: string, value: PendingIntent) => {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { return; }
};

type ImageEvidenceFormProps = {
  ownerUserId?: string; sessionId: string; capability: ImageEvidenceCapability;
  speechCapability?: SpeechTranscriptionCapabilityEnabled | null;
  submittedEvidence: Evidence[]; onUpload: (form: FormData) => Promise<ImageEvidenceResponse>;
  unified?: boolean;
  allowAttachments?: boolean;
  onSubmitEvidence?: (payload: SubmitEvidenceRequest) => Promise<Pick<SyncResponse, "syncPending">>;
};

const supportedUrl = (value: string) => {
  try { const url = new URL(value); return (url.protocol === "http:" || url.protocol === "https:") && !/\s/.test(value); }
  catch { return false; }
};

export function ImageEvidenceForm({ ownerUserId = "current-owner", sessionId, capability, speechCapability = null, submittedEvidence, onUpload, unified = false, allowAttachments = true, onSubmitEvidence }: ImageEvidenceFormProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [explanation, setExplanation] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [composerRevision, setComposerRevision] = useState(0);
  const [recorderBusy, setRecorderBusy] = useState(false);
  const [selection, setSelection] = useState({ start: 0, end: 0 });
  const submitting = useRef(false);
  const recorderBusyRef = useRef(false);
  const handleRecorderBusy = useCallback((nextBusy: boolean) => {
    recorderBusyRef.current = nextBusy;
    setRecorderBusy(nextBusy);
  }, []);
  useEffect(() => {
    if (!speechCapability) handleRecorderBusy(false);
  }, [handleRecorderBusy, speechCapability]);
  const images = submittedEvidence.filter((item) => item.evidenceType === "image");
  const remaining = Math.max(0, capability.maxCount - images.length);
  const clearComposer = () => { setExplanation(""); setSelection({ start: 0, end: 0 }); setComposerRevision((current) => current + 1); };
  const choose = (selected: FileList | readonly File[] | null, append = false) => {
    const selectedFiles = Array.from(selected ?? []);
    const unsupported = selectedFiles.find((file) => !capability.formats.includes(file.type));
    const oversized = selectedFiles.find((file) => file.size > capability.maxOriginalBytes);
    if (unsupported || oversized) {
      setFiles([]);
      setFailed(true);
      setMessage(unsupported ? "This image format is not supported." : "The selected image is too large.");
      return;
    }
    const available = Math.max(0, remaining - (append ? files.length : 0));
    setFiles((current) => (append ? [...current, ...selectedFiles.slice(0, available)] : selectedFiles.slice(0, available)));
    setFailed(selectedFiles.length > available);
    setMessage(selectedFiles.length > available ? `You can add ${available} more images.` : "");
  };
  const submit = async (event?: React.FormEvent) => {
    event?.preventDefault();
    if (recorderBusyRef.current || submitting.current || (files.length === 0 && !explanation.trim()) || (files.length > 0 && !explanation.trim())) return;
    submitting.current = true;
    setBusy(true); setMessage(""); setFailed(false);
    const normalizedExplanation = explanation.trim();
    if (files.length === 0 && unified && onSubmitEvidence) {
      try {
        const payload = supportedUrl(normalizedExplanation)
          ? { evidenceType: "url", sourceUrl: normalizedExplanation, textContent: "", metadata: {} }
          : { evidenceType: "text", textContent: normalizedExplanation, metadata: {} };
        const response = await onSubmitEvidence(payload);
        clearComposer();
        setMessage(response.syncPending ? "Evidence saved, waiting for Agent sync." : "Evidence submitted.");
      } catch (error) { setFailed(true); setMessage(getSafeErrorMessage(error)); }
      finally { submitting.current = false; setBusy(false); }
      return;
    }
    clearPending(`${pendingNamespace}:${sessionId}`);
    let activeStorageKey: string | null = null;
    try {
      const pending = [...files];
      while (pending.length > 0) { const file = pending[0];
        const fingerprint = await intentFingerprint(ownerUserId, sessionId, file, normalizedExplanation);
        activeStorageKey = storageKey(sessionId, fingerprint);
        const recovered = readPending(activeStorageKey, ownerUserId, sessionId, fingerprint);
        const baseKey = recovered?.baseKey ?? crypto.randomUUID();
        writePending(activeStorageKey, { schemaVersion: pendingSchemaVersion, ownerUserId, sessionId, intentFingerprint: fingerprint, baseKey, createdAt: recovered?.createdAt ?? Date.now() });
        const form = new FormData();
        form.append("file", file);
        form.append("explanation", normalizedExplanation);
        form.append("idempotency_key", await requestKey(baseKey, fingerprint));
        await onUpload(form);
        clearPending(activeStorageKey);
        activeStorageKey = null;
        pending.shift();
        setFiles([...pending]);
      }
      clearComposer();
      setMessage("Image evidence uploaded.");
    } catch (error) {
      const retryable = isApiError(error) ? error.retryable : true;
      setFailed(retryable);
      if (!retryable && activeStorageKey) clearPending(activeStorageKey);
      setMessage(getSafeErrorMessage(error));
    } finally { submitting.current = false; setBusy(false); }
  };
  if (unified) return <form className="grid gap-3" onSubmit={submit} data-testid="evidence-dropzone"
    onDragOver={(event) => event.preventDefault()}
    onDrop={(event) => { event.preventDefault(); if (allowAttachments) choose(event.dataTransfer.files, true); }}>
    <div className="field">
      <label htmlFor={`${sessionId}-composer`}>Learning evidence</label>
      <textarea id={`${sessionId}-composer`} className="input min-h-32 resize-y" value={explanation}
        placeholder="Write or paste notes, an explanation, or a single URL"
        onChange={(event) => { setExplanation(event.target.value); setComposerRevision((current) => current + 1); }}
        onSelect={(event) => { setSelection({ start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd }); }}
        onPaste={(event) => { const pasted = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith("image/")); if (allowAttachments && pasted.length) { event.preventDefault(); choose(pasted, true); } }} />
    </div>
    {speechCapability ? <div className="flex flex-wrap items-center gap-2"><SpeechRecorderControl sessionId={sessionId} composerRevision={composerRevision} selectionStart={selection.start} selectionEnd={selection.end} capability={speechCapability} disabled={busy} canStart={() => !submitting.current} onBusyChange={handleRecorderBusy} onTranscript={(text, fence) => { if (fence.sessionId !== sessionId || fence.composerRevision !== composerRevision) return; setExplanation((current) => current.slice(0, fence.selectionStart) + text + current.slice(fence.selectionEnd)); setComposerRevision((current) => current + 1); }} /></div> : null}
    {files.length ? <ul aria-label="Images ready to upload" className="grid gap-2">{files.map((file, index) => <li key={`${index}-${file.name}-${file.size}`} className="flex min-w-0 items-center justify-between gap-3 rounded-md border border-line px-3 py-2 text-sm">
      <span className="min-w-0"><span className="block truncate font-medium">{file.name}</span><span className="text-xs text-slate-600">{file.type} · {Math.max(1, Math.ceil(file.size / 1024))} KB</span></span>
      <button type="button" className="btn secondary h-10 w-10 shrink-0 p-0" aria-label={`Remove ${file.name}`} title={`Remove ${file.name}`} disabled={busy} onClick={() => setFiles((items) => items.filter((item) => item !== file))}><Trash2 size={16} aria-hidden /></button>
    </li>)}</ul> : null}
    {allowAttachments ? <div className="flex flex-wrap items-center gap-2">
      <label className="btn secondary cursor-pointer" title="Choose images">
        <Paperclip size={16} aria-hidden />Choose images
        <input className="sr-only" aria-label="Choose images" type="file" multiple accept={capability.formats.join(",")} disabled={busy || remaining === 0} onChange={(event) => { choose(event.currentTarget.files, true); event.currentTarget.value = ""; }} />
      </label>
      <span className="text-xs text-slate-600">Drop or paste images · {capability.maxCount} max</span>
    </div> : null}
    <button className="btn w-fit" type="submit" disabled={busy || recorderBusy || (files.length === 0 && !explanation.trim())}><Send size={16} aria-hidden />{busy ? "Submitting..." : "Submit evidence"}</button>
    {message ? <p role={failed ? "alert" : "status"} aria-live="polite" className={failed ? "text-sm text-red-700" : "text-sm text-slate-700"}>{message}</p> : null}
  </form>;
  return <section className="grid gap-4 border-t border-line pt-4" aria-labelledby={`${sessionId}-image-heading`}>
    <div className="flex items-center gap-2"><ImagePlus size={18} /><h3 id={`${sessionId}-image-heading`} className="font-semibold">Image evidence</h3></div>
    <p className="text-sm text-slate-700">{capability.formats.map(labelForFormat).join(", ")} · {capability.maxCount} images · {capability.maxOriginalBytes / mib} MiB each · {capability.maxNormalizedBytesPerSession / mib} MiB normalized total</p>
    <form className="grid gap-3" onSubmit={submit}>
      <div className="field"><label htmlFor={`${sessionId}-images`}>Choose images</label><input id={`${sessionId}-images`} className="input" type="file" multiple accept={capability.formats.join(",")} disabled={busy || remaining === 0} onChange={(event) => choose(event.currentTarget.files)} /></div>
      {files.length ? <ul aria-label="Images ready to upload" className="grid gap-2">{files.map((file, index) => <li key={`${index}-${file.name}-${file.size}`} className="flex min-w-0 items-center justify-between gap-2 rounded-md border border-line px-3 py-2 text-sm"><span className="truncate">{file.name}</span><button type="button" className="btn secondary shrink-0" aria-label={`Remove ${file.name}`} onClick={() => setFiles((items) => items.filter((item) => item !== file))}><Trash2 size={16} />Remove</button></li>)}</ul> : null}
      <div className="field"><label htmlFor={`${sessionId}-image-explanation`}>Explain what these images show</label><textarea id={`${sessionId}-image-explanation`} className="input min-h-24" required={capability.explanationRequired} value={explanation} onChange={(event) => setExplanation(event.target.value)} /></div>
      <button className="btn w-fit" type="submit" disabled={busy || files.length === 0 || remaining === 0}><Upload size={16} />{busy ? "Uploading..." : "Upload image evidence"}</button>
      {failed && !busy ? <button className="btn secondary w-fit" type="button" onClick={() => void submit()}>Retry upload</button> : null}
      {message ? <p role={failed ? "alert" : "status"} className={failed ? "text-sm text-red-700" : "text-sm text-slate-700"}>{message}</p> : null}
    </form>
    {images.length ? <div><h4 className="font-medium">Uploaded images</h4><ol className="grid gap-2">{images.map((image) => <li className="rounded-md border border-line p-3 text-sm" key={image.evidenceId}><p>{image.textContent || "Image evidence"}</p><p className="text-slate-600">{String(image.metadata.mediaType ?? "image")}</p></li>)}</ol></div> : null}
  </section>;
}
