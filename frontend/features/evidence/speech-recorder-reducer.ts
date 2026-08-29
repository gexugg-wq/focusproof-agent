export type SpeechRecorderStatus = "idle" | "requesting_permission" | "recording" | "stopping" | "transcribing" | "succeeded" | "failed";

export type SpeechOperationFence = {
  generation: number;
  sessionId: string;
  composerRevision: number;
  selectionStart: number;
  selectionEnd: number;
};

export type SpeechRecorderState = {
  status: SpeechRecorderStatus;
  fence?: SpeechOperationFence;
  message?: string;
};

export const initialSpeechRecorderState: SpeechRecorderState = { status: "idle" };

type SpeechRecorderAction =
  | { type: "REQUEST_PERMISSION"; fence: SpeechOperationFence }
  | { type: "PERMISSION_GRANTED"; fence: SpeechOperationFence }
  | { type: "STOP_REQUESTED"; fence: SpeechOperationFence }
  | { type: "RECORDING_READY"; fence: SpeechOperationFence }
  | { type: "TRANSCRIBED"; fence: SpeechOperationFence }
  | { type: "REQUEST_FAILED"; fence: SpeechOperationFence; message: string }
  | { type: "RESET"; fence: SpeechOperationFence }
  | { type: "CANCELLED"; fence: SpeechOperationFence }
  | { type: "SESSION_CHANGED" };

const sameFence = (left: SpeechOperationFence | undefined, right: SpeechOperationFence) =>
  left?.generation === right.generation
  && left.sessionId === right.sessionId
  && left.composerRevision === right.composerRevision
  && left.selectionStart === right.selectionStart
  && left.selectionEnd === right.selectionEnd;

const transition = (state: SpeechRecorderState, status: SpeechRecorderStatus, fence: SpeechOperationFence, message?: string): SpeechRecorderState =>
  sameFence(state.fence, fence) ? { status, fence, message } : state;

export function speechRecorderReducer(state: SpeechRecorderState, action: SpeechRecorderAction): SpeechRecorderState {
  if (action.type === "SESSION_CHANGED") return initialSpeechRecorderState;
  if (action.type === "REQUEST_PERMISSION") {
    return state.status === "idle" || state.status === "succeeded" || state.status === "failed"
      ? { status: "requesting_permission", fence: action.fence }
      : state;
  }
  if (action.type === "PERMISSION_GRANTED") {
    return state.status === "requesting_permission" ? transition(state, "recording", action.fence) : state;
  }
  if (action.type === "STOP_REQUESTED") {
    return state.status === "recording" ? transition(state, "stopping", action.fence) : state;
  }
  if (action.type === "RECORDING_READY") {
    return state.status === "stopping" ? transition(state, "transcribing", action.fence) : state;
  }
  if (action.type === "TRANSCRIBED") {
    return state.status === "transcribing" ? transition(state, "succeeded", action.fence) : state;
  }
  if (action.type === "REQUEST_FAILED") {
    return state.status === "idle" || sameFence(state.fence, action.fence)
      ? { status: "failed", fence: action.fence, message: action.message }
      : state;
  }
  if (action.type === "RESET" || action.type === "CANCELLED") {
    return sameFence(state.fence, action.fence) ? initialSpeechRecorderState : state;
  }
  return state;
}
