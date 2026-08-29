import { webcrypto } from "node:crypto";
import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { EvidencePanel } from "@/features/evidence/EvidencePanel";
import type { ImageEvidenceCapability, SpeechTranscriptionCapabilityEnabled } from "@/lib/api/contracts";

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

const speechCapability: SpeechTranscriptionCapabilityEnabled = {
  capabilityId: "speech_transcription", schemaVersion: 1, enabled: true,
  formats: ["audio/webm;codecs=opus"], maxAudioBytes: 11 * 1024 * 1024,
  maxDurationSeconds: 120, languageHintsAccepted: ["auto"], languageHintEffect: "metadata_only"
};
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
};
class RaceRecorder extends EventTarget {
  static isTypeSupported = () => true;
  state: RecordingState = "inactive";
  readonly mimeType: string;
  constructor(_stream: MediaStream, options: MediaRecorderOptions) {
    super();
    this.mimeType = options.mimeType ?? "audio/webm;codecs=opus";
  }
  start() { this.state = "recording"; }
  stop() {
    if (this.state === "inactive") return;
    this.state = "inactive";
    const event = new Event("dataavailable") as BlobEvent;
    Object.defineProperty(event, "data", { value: new Blob(["audio"], { type: this.mimeType }) });
    this.dispatchEvent(event);
    this.dispatchEvent(new Event("stop"));
  }
}
const installRaceRecorder = (getUserMedia: ReturnType<typeof vi.fn>) => {
  vi.stubGlobal("MediaRecorder", RaceRecorder);
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
};

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


  it("keeps one composer, disables Submit while recording, and inserts raw transcript without auto-submit", async () => {
    const track = { stop: vi.fn() };
    class Recorder extends EventTarget {
      static isTypeSupported = () => true;
      state: RecordingState = "inactive";
      constructor(_stream: MediaStream, _options: MediaRecorderOptions) { super(); }
      start() { this.state = "recording"; }
      stop() {
        this.state = "inactive";
        const event = new Event("dataavailable") as BlobEvent;
        Object.defineProperty(event, "data", { value: new Blob(["audio"], { type: "audio/webm;codecs=opus" }) });
        this.dispatchEvent(event);
        this.dispatchEvent(new Event("stop"));
      }
    }
    vi.stubGlobal("MediaRecorder", Recorder);
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [track] }) } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ requestId: "req_1", transcript: "  raw transcript\n", provider: "dashscope", model: "qwen3-asr-flash" }), { headers: { "content-type": "application/json" } })));
    const submit = vi.fn().mockResolvedValue({ syncPending: false });
    render(<EvidencePanel {...props({ onSubmitEvidence: submit, speechCapability: { capabilityId: "speech_transcription", schemaVersion: 1, enabled: true, formats: ["audio/webm;codecs=opus"], maxAudioBytes: 11 * 1024 * 1024, maxDurationSeconds: 120, languageHintsAccepted: ["auto"], languageHintEffect: "metadata_only" } })} />);
    const textarea = screen.getByLabelText(/learning evidence/i) as HTMLTextAreaElement;
    await userEvent.type(textarea, "prefix suffix");
    textarea.setSelectionRange(7, 7);
    fireEvent.select(textarea, { target: { selectionStart: 7, selectionEnd: 7 } });
    expect(screen.getAllByRole("button", { name: /submit evidence/i })).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));
    expect(screen.getByRole("button", { name: /submit evidence/i })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /stop recording/i }));
    await waitFor(() => expect(textarea).toHaveValue("prefix   raw transcript\nsuffix"));
    expect(submit).not.toHaveBeenCalled();
  });

  it("blocks microphone start while evidence submission is pending", async () => {
    const submission = deferred<{ syncPending: boolean }>();
    const submit = vi.fn().mockReturnValue(submission.promise);
    const track = { stop: vi.fn() };
    const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [track] });
    installRaceRecorder(getUserMedia);
    render(<EvidencePanel {...props({ onSubmitEvidence: submit, speechCapability })} />);

    await userEvent.type(screen.getByLabelText(/learning evidence/i), "Evidence being submitted.");
    await userEvent.click(screen.getByRole("button", { name: /submit evidence/i }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));

    const start = screen.getByRole("button", { name: /start recording/i });
    expect(start).toBeDisabled();
    fireEvent.click(start);
    expect(getUserMedia).not.toHaveBeenCalled();

    await act(async () => { submission.resolve({ syncPending: false }); });
  });

  it("synchronously rejects microphone start when evidence submit wins the same tick", async () => {
    const submission = deferred<{ syncPending: boolean }>();
    const submit = vi.fn().mockReturnValue(submission.promise);
    const getUserMedia = vi.fn();
    installRaceRecorder(getUserMedia);
    render(<EvidencePanel {...props({ onSubmitEvidence: submit, speechCapability })} />);
    await userEvent.type(screen.getByLabelText(/learning evidence/i), "Submit wins this race.");

    const form = screen.getByTestId("evidence-dropzone");
    const start = screen.getByRole("button", { name: /start recording/i });
    act(() => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      (start as HTMLButtonElement).click();
    });

    expect(submit).toHaveBeenCalledTimes(1);
    expect(getUserMedia).not.toHaveBeenCalled();
    await act(async () => { submission.resolve({ syncPending: false }); });
  });

  it("synchronously rejects programmatic evidence submit when recording start wins the same tick", async () => {
    const microphone = deferred<MediaStream>();
    const getUserMedia = vi.fn().mockReturnValue(microphone.promise);
    installRaceRecorder(getUserMedia);
    const submit = vi.fn().mockResolvedValue({ syncPending: false });
    const view = render(<EvidencePanel {...props({ onSubmitEvidence: submit, speechCapability })} />);
    await userEvent.type(screen.getByLabelText(/learning evidence/i), "Keep this draft.");

    const start = screen.getByRole("button", { name: /start recording/i });
    const form = screen.getByTestId("evidence-dropzone");
    act(() => {
      (start as HTMLButtonElement).click();
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(submit).not.toHaveBeenCalled();
    const track = { stop: vi.fn() };
    await act(async () => { microphone.resolve({ getTracks: () => [track] } as unknown as MediaStream); });
    await waitFor(() => expect(screen.getByRole("button", { name: /stop recording/i })).toBeVisible());
    view.unmount();
    expect(track.stop).toHaveBeenCalledTimes(1);
  });

  it("releases the parent recorder lock when speech capability is revoked", async () => {
    const track = { stop: vi.fn() };
    const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [track] });
    installRaceRecorder(getUserMedia);
    const componentProps = props({ speechCapability });
    const view = render(<EvidencePanel {...componentProps} />);
    await userEvent.type(screen.getByLabelText(/learning evidence/i), "Draft remains editable.");
    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));
    expect(screen.getByRole("button", { name: /submit evidence/i })).toBeDisabled();

    view.rerender(<EvidencePanel {...componentProps} speechCapability={null} />);

    await waitFor(() => expect(screen.getByRole("button", { name: /submit evidence/i })).toBeEnabled());
    expect(screen.queryByRole("button", { name: /start recording/i })).not.toBeInTheDocument();
    expect(track.stop).toHaveBeenCalledTimes(1);
  });
});
