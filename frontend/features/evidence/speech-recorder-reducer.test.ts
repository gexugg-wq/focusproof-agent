import { describe, expect, it } from "vitest";
import { initialSpeechRecorderState, speechRecorderReducer, type SpeechOperationFence } from "./speech-recorder-reducer";

const fence: SpeechOperationFence = { generation: 1, sessionId: "sess_1", composerRevision: 2, selectionStart: 3, selectionEnd: 5 };

describe("speech recorder reducer", () => {
  it("moves through the legal recording lifecycle and ignores stale completion", () => {
    const requesting = speechRecorderReducer(initialSpeechRecorderState, { type: "REQUEST_PERMISSION", fence });
    const recording = speechRecorderReducer(requesting, { type: "PERMISSION_GRANTED", fence });
    const stopping = speechRecorderReducer(recording, { type: "STOP_REQUESTED", fence });
    const transcribing = speechRecorderReducer(stopping, { type: "RECORDING_READY", fence });
    expect([requesting.status, recording.status, stopping.status, transcribing.status]).toEqual([
      "requesting_permission", "recording", "stopping", "transcribing"
    ]);
    expect(speechRecorderReducer(transcribing, { type: "TRANSCRIBED", fence: { ...fence, generation: 2 } })).toEqual(transcribing);
    expect(speechRecorderReducer(transcribing, { type: "TRANSCRIBED", fence: { ...fence, sessionId: "sess_2" } })).toEqual(transcribing);
    expect(speechRecorderReducer(transcribing, { type: "TRANSCRIBED", fence: { ...fence, composerRevision: 3 } })).toEqual(transcribing);
    expect(speechRecorderReducer(transcribing, { type: "TRANSCRIBED", fence }).status).toBe("succeeded");
  });

  it("resets a matching operation after failure, cancellation, or a session change", () => {
    const failed = speechRecorderReducer(initialSpeechRecorderState, { type: "REQUEST_FAILED", fence, message: "Network unavailable." });
    expect(failed).toMatchObject({ status: "failed", message: "Network unavailable." });
    expect(speechRecorderReducer(failed, { type: "RESET", fence }).status).toBe("idle");
    expect(speechRecorderReducer(failed, { type: "SESSION_CHANGED" }).status).toBe("idle");
  });

  it("rejects illegal transitions and every nonmatching cancellation fence", () => {
    const requesting = speechRecorderReducer(initialSpeechRecorderState, { type: "REQUEST_PERMISSION", fence });
    expect(speechRecorderReducer(requesting, { type: "RECORDING_READY", fence })).toEqual(requesting);
    expect(speechRecorderReducer(requesting, { type: "STOP_REQUESTED", fence })).toEqual(requesting);
    expect(speechRecorderReducer(initialSpeechRecorderState, { type: "TRANSCRIBED", fence })).toEqual(initialSpeechRecorderState);
    for (const stale of [
      { ...fence, generation: 2 }, { ...fence, sessionId: "sess_2" }, { ...fence, composerRevision: 3 },
      { ...fence, selectionStart: 4 }, { ...fence, selectionEnd: 6 }
    ]) {
      expect(speechRecorderReducer(requesting, { type: "CANCELLED", fence: stale })).toEqual(requesting);
      expect(speechRecorderReducer(requesting, { type: "RESET", fence: stale })).toEqual(requesting);
    }
    expect(speechRecorderReducer(requesting, { type: "CANCELLED", fence })).toEqual(initialSpeechRecorderState);
  });
});
