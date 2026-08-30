import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SpeechRecorderControl } from "./SpeechRecorderControl";
import type { SpeechTranscriptionCapabilityEnabled } from "@/lib/api/contracts";

import { ApiError } from "@/lib/api/errors";
const capability: SpeechTranscriptionCapabilityEnabled = {
  capabilityId: "speech_transcription", schemaVersion: 1, enabled: true,
  formats: ["audio/webm;codecs=opus", "audio/wav", "audio/mpeg"], maxAudioBytes: 11 * 1024 * 1024,
  maxDurationSeconds: 120, languageHintsAccepted: ["auto", "zh", "en"], languageHintEffect: "metadata_only"
};

class FakeRecorder extends EventTarget {
  static last: FakeRecorder | null = null;
  static startArguments: unknown[][] = [];
  static requestDataCalls = 0;
  static isTypeSupported = vi.fn((type: string) => type === "audio/webm;codecs=opus");
  state: RecordingState = "inactive";
  static payload = "audio";
  mimeType: string;
  constructor(_stream: MediaStream, options: MediaRecorderOptions) { super(); this.mimeType = options.mimeType ?? ""; FakeRecorder.last = this; }
  start(...args: unknown[]) { FakeRecorder.startArguments.push(args); this.state = "recording"; }
  requestData() { FakeRecorder.requestDataCalls += 1; }
  stop() {
    if (this.state === "inactive") return;
    this.state = "inactive";
    const event = new Event("dataavailable") as BlobEvent;
    Object.defineProperty(event, "data", { value: new Blob([FakeRecorder.payload], { type: this.mimeType }) });
    this.dispatchEvent(event);
    this.dispatchEvent(new Event("stop"));
  }
}

const makeStream = () => {
  const track = { stop: vi.fn() };
  return { stream: { getTracks: () => [track] } as unknown as MediaStream, track };
};
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
};

describe("SpeechRecorderControl", () => {
  let current = makeStream();
  beforeEach(() => {
    current = makeStream();
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    FakeRecorder.payload = "audio";
    FakeRecorder.startArguments = [];
    FakeRecorder.requestDataCalls = 0;
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn().mockImplementation(() => Promise.resolve(current.stream)) } });
  });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("records, stops, transcribes once, and releases microphone tracks", async () => {
    const transcript = vi.fn();
    const transcribe = vi.fn().mockResolvedValue({ transcript: "  raw transcript\n" });
    render(<SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={2} selectionEnd={2} capability={capability} onTranscript={transcript} transcribe={transcribe} />);
    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));
    await userEvent.click(screen.getByRole("button", { name: /stop recording/i }));
    await waitFor(() => expect(transcript).toHaveBeenCalledWith("  raw transcript\n", expect.objectContaining({ sessionId: "sess_1", selectionStart: 2 })));
    expect(transcribe).toHaveBeenCalledTimes(1);
    expect(current.track.stop).toHaveBeenCalledTimes(1);
  });

  it("records one complete WebM chunk without timeslices or requestData", async () => {
    render(<SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={vi.fn()} transcribe={vi.fn().mockResolvedValue({ transcript: "raw" })} />);

    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));
    await userEvent.click(screen.getByRole("button", { name: /stop recording/i }));

    expect(FakeRecorder.startArguments).toEqual([[]]);
    expect(FakeRecorder.requestDataCalls).toBe(0);
  });

  it("shows a stable permission error without attempting transcription", async () => {
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn().mockRejectedValue(new DOMException("denied", "NotAllowedError")) } });
    const transcribe = vi.fn();
    render(<SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={vi.fn()} transcribe={transcribe} />);
    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/microphone permission/i);
    expect(transcribe).not.toHaveBeenCalled();
  });

  it("suppresses a late transcript after revision change and cleans up on unmount", async () => {
    const pending = deferred<{ transcript: string }>();
    const transcript = vi.fn();
    const transcribe = vi.fn().mockReturnValue(pending.promise);
    const { rerender, unmount } = render(<SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={transcript} transcribe={transcribe} />);
    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));
    await userEvent.click(screen.getByRole("button", { name: /stop recording/i }));
    rerender(<SpeechRecorderControl sessionId="sess_1" composerRevision={1} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={transcript} transcribe={transcribe} />);
    await act(async () => pending.resolve({ transcript: "late" }));
    expect(transcript).not.toHaveBeenCalled();
    unmount();
    expect(current.track.stop).toHaveBeenCalledTimes(1);
  });

  it("auto-stops at the 120 second recording ceiling", async () => {
    vi.useFakeTimers();
    const transcribe = vi.fn().mockResolvedValue({ transcript: "timer transcript" });
    render(<SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={vi.fn()} transcribe={transcribe} />);
    await act(async () => { screen.getByRole("button", { name: /start recording/i }).click(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(FakeRecorder.last?.state).toBe("inactive");
    expect(transcribe).toHaveBeenCalledTimes(1);
  });

  it("accepts only one getUserMedia request during repeated start clicks", async () => {
    const pending = deferred<MediaStream>();
    const getUserMedia = vi.fn().mockReturnValue(pending.promise);
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    render(<SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={vi.fn()} transcribe={vi.fn()} />);
    const start = screen.getByRole("button", { name: /start recording/i });
    await act(async () => {
      start.click();
      start.click();
    });
    expect(getUserMedia).toHaveBeenCalledTimes(1);
  });

  it("shows elapsed recording time and the visible 120 second ceiling", async () => {
    vi.useFakeTimers();
    render(<SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={vi.fn()} transcribe={vi.fn()} />);
    await act(async () => { screen.getByRole("button", { name: /start recording/i }).click(); await Promise.resolve(); });
    expect(screen.getByText(/0:00 \/ 2:00/)).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(screen.getByText(/0:01 \/ 2:00/)).toBeVisible();
  });

  it("stops a late microphone stream after unmount without transcribing or writing a transcript", async () => {
    const pending = deferred<MediaStream>();
    const getUserMedia = vi.fn().mockReturnValue(pending.promise);
    const transcribe = vi.fn();
    const transcript = vi.fn();
    const busy = vi.fn();
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    const { unmount } = render(
      <SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={transcript} onBusyChange={busy} transcribe={transcribe} />
    );

    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));
    const busyCallsBeforeUnmount = busy.mock.calls.length;
    unmount();
    const late = makeStream();
    await act(async () => { pending.resolve(late.stream); });

    expect(late.track.stop).toHaveBeenCalledTimes(1);
    expect(transcribe).not.toHaveBeenCalled();
    expect(transcript).not.toHaveBeenCalled();
    expect(busy.mock.calls.length).toBe(busyCallsBeforeUnmount);
  });

  it("releases a recording when composer revision changes and allows a new recording", async () => {
    vi.useFakeTimers();
    const transcribe = vi.fn();
    const transcript = vi.fn();
    const { rerender } = render(
      <SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={transcript} transcribe={transcribe} />
    );
    await act(async () => {
      screen.getByRole("button", { name: /start recording/i }).click();
      await Promise.resolve();
    });

    rerender(
      <SpeechRecorderControl sessionId="sess_1" composerRevision={1} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={transcript} transcribe={transcribe} />
    );
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });

    expect(current.track.stop).toHaveBeenCalledTimes(1);
    expect(transcribe).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /start recording/i })).toBeEnabled();
  });
  it.each([
    [409, /already in progress/i],
    [413, /too large/i],
    [415, /format is not supported/i],
    [422, /could not be transcribed/i],
    [429, /temporarily busy/i],
    [503, /temporarily unavailable/i],
    [504, /temporarily unavailable/i],
    [0, /network error/i],
  ])("maps transcription failure status %s without emitting a transcript", async (status, message) => {
    const transcribe = vi.fn().mockRejectedValue(
      new ApiError({ status, code: "transcription_failed", retryable: true, message: "raw server error" })
    );
    const transcript = vi.fn();
    render(
      <SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={transcript} transcribe={transcribe} />
    );

    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));
    await userEvent.click(screen.getByRole("button", { name: /stop recording/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(transcript).not.toHaveBeenCalled();
    expect(current.track.stop).toHaveBeenCalledTimes(1);
  });
  it("rejects an empty recording without transcribing and releases its track", async () => {
    FakeRecorder.payload = "";
    const transcribe = vi.fn();
    render(
      <SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={vi.fn()} transcribe={transcribe} />
    );

    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));
    await userEvent.click(screen.getByRole("button", { name: /stop recording/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/no audio was captured/i);
    expect(transcribe).not.toHaveBeenCalled();
    expect(current.track.stop).toHaveBeenCalledTimes(1);
  });
  it("cancels a transcription, accepts a new recording, and ignores the late first response", async () => {
    const first = deferred<{ transcript: string }>();
    const second = deferred<{ transcript: string }>();
    const firstStream = makeStream();
    const secondStream = makeStream();
    const getUserMedia = vi.fn()
      .mockResolvedValueOnce(firstStream.stream)
      .mockResolvedValueOnce(secondStream.stream);
    const transcribe = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const transcript = vi.fn();
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    render(<SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={transcript} transcribe={transcribe} />);

    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));
    await userEvent.click(screen.getByRole("button", { name: /stop recording/i }));
    await userEvent.click(screen.getByRole("button", { name: /cancel recording/i }));
    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));
    await userEvent.click(screen.getByRole("button", { name: /stop recording/i }));
    await act(async () => { second.resolve({ transcript: "second raw" }); });
    await act(async () => { first.resolve({ transcript: "first raw" }); });

    expect(transcribe).toHaveBeenCalledTimes(2);
    expect(transcript).toHaveBeenCalledTimes(1);
    expect(transcript).toHaveBeenCalledWith("second raw", expect.any(Object));
    expect(firstStream.track.stop).toHaveBeenCalledTimes(1);
    expect(secondStream.track.stop).toHaveBeenCalledTimes(1);
  });

  it("shows a no-microphone message without calling transcription", async () => {
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn().mockRejectedValue(new DOMException("none", "NotFoundError")) } });
    const transcribe = vi.fn();
    render(<SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={vi.fn()} transcribe={transcribe} />);
    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/no microphone is available/i);
    expect(transcribe).not.toHaveBeenCalled();
  });

  it("rejects an unsupported recorder MIME and releases the acquired track", async () => {
    FakeRecorder.isTypeSupported.mockReturnValue(false);
    const transcribe = vi.fn();
    render(<SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={vi.fn()} transcribe={transcribe} />);
    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not supported/i);
    expect(transcribe).not.toHaveBeenCalled();
    expect(current.track.stop).toHaveBeenCalledTimes(1);
  });

  it("releases the acquired stream when the MediaRecorder constructor throws", async () => {
    class ConstructorThrows {
      static isTypeSupported = () => true;
      constructor(_stream: MediaStream, _options: MediaRecorderOptions) {
        throw new Error("constructor failed");
      }
    }
    vi.stubGlobal("MediaRecorder", ConstructorThrows);
    const transcribe = vi.fn();
    render(
      <SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={vi.fn()} transcribe={transcribe} />
    );

    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/unable to start microphone recording/i);
    expect(current.track.stop).toHaveBeenCalledTimes(1);
    expect(transcribe).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /start recording/i })).toBeEnabled();
  });

  it("releases the acquired stream and permits a fresh start when MediaRecorder.start throws", async () => {
    FakeRecorder.isTypeSupported.mockImplementation((type: string) => type === "audio/webm;codecs=opus");
    class StartThrows extends FakeRecorder {
      start() { throw new Error("start failed"); }
    }
    vi.stubGlobal("MediaRecorder", StartThrows);
    const transcribe = vi.fn();
    render(
      <SpeechRecorderControl sessionId="sess_1" composerRevision={0} selectionStart={0} selectionEnd={0} capability={capability} onTranscript={vi.fn()} transcribe={transcribe} />
    );

    await userEvent.click(screen.getByRole("button", { name: /start recording/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/unable to start microphone recording/i);
    expect(current.track.stop).toHaveBeenCalledTimes(1);
    expect(transcribe).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /start recording/i })).toBeEnabled();
  });
});
