"use client";

import React, { useEffect, useReducer, useRef, useState } from "react";
import { Mic, Square, X } from "lucide-react";
import { focusProofApi, isApiError } from "@/lib/api/client";
import type { SpeechTranscriptionCapabilityEnabled, TranscriptionResponse } from "@/lib/api/contracts";
import { initialSpeechRecorderState, speechRecorderReducer, type SpeechOperationFence } from "./speech-recorder-reducer";

type Transcribe = (sessionId: string, file: File, languageHint: "auto" | "zh" | "en", idempotencyKey: string, signal: AbortSignal) => Promise<TranscriptionResponse>;

type SpeechRecorderControlProps = {
  sessionId: string;
  composerRevision: number;
  selectionStart: number;
  selectionEnd: number;
  capability: SpeechTranscriptionCapabilityEnabled;
  onTranscript: (text: string, fence: SpeechOperationFence) => void;
  onBusyChange?: (busy: boolean) => void;
  disabled?: boolean;
  canStart?: () => boolean;
  transcribe?: Transcribe;
};

const mimeCandidates: SpeechTranscriptionCapabilityEnabled["formats"] = ["audio/webm;codecs=opus", "audio/wav", "audio/mpeg"];
const isBusy = (status: string) => ["requesting_permission", "recording", "stopping", "transcribing"].includes(status);
const isAbort = (error: unknown) => error instanceof DOMException && error.name === "AbortError";
const formatDuration = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

function errorMessage(error: unknown): string {
  if (isApiError(error)) {
    const recovery = error.retryable ? "Retry this clip or record a new clip." : "Record a new clip.";
    if (error.code === "transcription_in_progress") return `Transcription is already in progress. ${recovery}`;
    if (error.code === "idempotency_conflict") return `This transcription request conflicts with a prior request. ${recovery}`;
    if (error.code === "transcription_ambiguous") return `The recording is ambiguous. ${recovery}`;
    if (error.code === "transcription_no_speech") return `No speech was detected. ${recovery}`;
    if (error.code === "invalid_audio") return `The recording could not be read. ${recovery}`;
    if (error.code === "audio_too_large" || error.status === 413) return "The recording is too large. Record a shorter clip.";
    if (error.code === "unsupported_audio_format" || error.status === 415) return "This audio format is not supported. Record a new clip.";
    if (error.status === 409) return `The transcription request could not be completed because of a conflict. ${recovery}`;
    if (error.status === 422) return `The recording could not be transcribed. ${recovery}`;
    if (error.status === 429) return `Transcription is temporarily busy. ${recovery}`;
    if (error.status === 503 || error.status === 504) return `Transcription is temporarily unavailable. ${recovery}`;
    if (error.status === 0) return `Network error while transcribing. ${recovery}`;
  }
  if (error instanceof TypeError) return "Network error while transcribing. Record a new clip.";
  return "Transcription failed. Record a new clip.";
}

export function SpeechRecorderControl({
  sessionId, composerRevision, selectionStart, selectionEnd, capability, onTranscript, onBusyChange,
  disabled = false, canStart = () => true, transcribe = focusProofApi.transcribe
}: SpeechRecorderControlProps) {
  const [state, dispatch] = useReducer(speechRecorderReducer, initialSpeechRecorderState);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const mounted = useRef(false);
  const latest = useRef({ sessionId, composerRevision });
  const generation = useRef(0);
  const activeFence = useRef<SpeechOperationFence | null>(null);
  const startPending = useRef(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const stopTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const elapsedTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const aborter = useRef<AbortController | null>(null);
  const retainedFile = useRef<File | null>(null);
  const previousSession = useRef(sessionId);
  const maximumSeconds = Math.min(capability.maxDurationSeconds, 120);

  latest.current = { sessionId, composerRevision };

  const clearTimers = () => {
    if (stopTimer.current !== null) clearTimeout(stopTimer.current);
    if (elapsedTimer.current !== null) clearInterval(elapsedTimer.current);
    stopTimer.current = null;
    elapsedTimer.current = null;
  };
  const releaseTracks = () => {
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
  };
  const clearRetainedFile = () => {
    retainedFile.current = null;
  };
  const isCurrent = (fence: SpeechOperationFence) =>
    mounted.current
    && activeFence.current?.generation === fence.generation
    && latest.current.sessionId === fence.sessionId
    && latest.current.composerRevision === fence.composerRevision;

  const cancelActive = () => {
    const fence = activeFence.current;
    activeFence.current = null;
    startPending.current = false;
    clearTimers();
    aborter.current?.abort();
    aborter.current = null;
    const instance = recorder.current;
    recorder.current = null;
    if (instance?.state === "recording") instance.stop();
    releaseTracks();
    clearRetainedFile();
    if (mounted.current) {
      setElapsedSeconds(0);
      if (fence) dispatch({ type: "CANCELLED", fence });
    }
  };

  const sendForTranscription = async (file: File, fence: SpeechOperationFence) => {
    const controller = new AbortController();
    aborter.current = controller;
    try {
      const response = await transcribe(fence.sessionId, file, "auto", crypto.randomUUID(), controller.signal);
      if (!isCurrent(fence)) return;
      clearRetainedFile();
      onTranscript(response.transcript, fence);
      dispatch({ type: "TRANSCRIBED", fence });
      activeFence.current = null;
    } catch (error) {
      if (!isCurrent(fence) || isAbort(error)) return;
      if (!isApiError(error) || !error.retryable) clearRetainedFile();
      dispatch({ type: "REQUEST_FAILED", fence, message: errorMessage(error) });
      activeFence.current = null;
    } finally {
      if (aborter.current === controller) aborter.current = null;
    }
  };

  const retry = () => {
    const file = retainedFile.current;
    if (!file || state.status !== "failed" || disabled || !canStart() || startPending.current || isBusy(state.status)) return;
    onBusyChange?.(true);
    const fence: SpeechOperationFence = {
      generation: generation.current + 1,
      sessionId,
      composerRevision,
      selectionStart,
      selectionEnd
    };
    generation.current = fence.generation;
    activeFence.current = fence;
    startPending.current = true;
    dispatch({ type: "RETRY_REQUESTED", fence });
    void sendForTranscription(file, fence).finally(() => {
      startPending.current = false;
    });
  };

  const stop = (fenceOverride?: SpeechOperationFence) => {
    const fence = fenceOverride ?? activeFence.current;
    if (!fence || activeFence.current?.generation !== fence.generation || recorder.current?.state !== "recording") return;
    clearTimers();
    if (mounted.current) dispatch({ type: "STOP_REQUESTED", fence });
    recorder.current.stop();
  };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      cancelActive();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (previousSession.current === sessionId) return;
    previousSession.current = sessionId;
    cancelActive();
    dispatch({ type: "SESSION_CHANGED" });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    const fence = activeFence.current;
    if (fence && fence.composerRevision !== composerRevision) {
      cancelActive();
    }
  // The fence is deliberately checked only when the composer revision changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [composerRevision]);

  useEffect(() => {
    onBusyChange?.(isBusy(state.status));
  }, [onBusyChange, state.status]);

  const start = async () => {
    if (disabled || !canStart() || startPending.current || isBusy(state.status)) return;
    clearRetainedFile();
    onBusyChange?.(true);
    const fence: SpeechOperationFence = {
      generation: generation.current + 1,
      sessionId,
      composerRevision,
      selectionStart,
      selectionEnd
    };
    generation.current = fence.generation;
    activeFence.current = fence;
    startPending.current = true;
    dispatch({ type: "REQUEST_PERMISSION", fence });
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      startPending.current = false;
      activeFence.current = null;
      dispatch({ type: "REQUEST_FAILED", fence, message: "Voice recording is not supported in this browser." });
      return;
    }
    try {
      const acquired = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!isCurrent(fence)) {
        acquired.getTracks().forEach((track) => track.stop());
        return;
      }
      const mimeType = typeof MediaRecorder.isTypeSupported === "function"
        ? mimeCandidates.find((candidate) => capability.formats.includes(candidate) && MediaRecorder.isTypeSupported(candidate))
        : undefined;
      if (!mimeType) {
        acquired.getTracks().forEach((track) => track.stop());
        startPending.current = false;
        activeFence.current = null;
        dispatch({ type: "REQUEST_FAILED", fence, message: "Voice recording is not supported in this browser." });
        return;
      }
      stream.current = acquired;
      const chunks: BlobPart[] = [];
      const instance = new MediaRecorder(acquired, { mimeType });
      recorder.current = instance;
      instance.addEventListener("dataavailable", (event: BlobEvent) => {
        if (event.data.size > 0) chunks.push(event.data);
      });
      instance.addEventListener("stop", () => {
        recorder.current = null;
        releaseTracks();
        if (!isCurrent(fence)) {
          chunks.length = 0;
          return;
        }
        const blob = new Blob(chunks, { type: mimeType });
        chunks.length = 0;
        if (blob.size === 0) {
          dispatch({ type: "REQUEST_FAILED", fence, message: "No audio was captured. Record again." });
          activeFence.current = null;
          return;
        }
        const file = new File([blob], "focusproof-recording", { type: mimeType });
        retainedFile.current = file;
        dispatch({ type: "RECORDING_READY", fence });
        void sendForTranscription(file, fence);
      });
      instance.start();
      startPending.current = false;
      setElapsedSeconds(0);
      dispatch({ type: "PERMISSION_GRANTED", fence });
      elapsedTimer.current = setInterval(() => {
        if (!isCurrent(fence)) {
          clearTimers();
          return;
        }
        setElapsedSeconds((value) => Math.min(value + 1, maximumSeconds));
      }, 1_000);
      stopTimer.current = setTimeout(() => stop(fence), maximumSeconds * 1_000);
    } catch (error) {
      if (!isCurrent(fence)) return;
      startPending.current = false;
      clearTimers();
      recorder.current = null;
      releaseTracks();
      activeFence.current = null;
      const message = error instanceof DOMException && error.name === "NotAllowedError"
        ? "Microphone permission was denied. Allow microphone access to record."
        : error instanceof DOMException && error.name === "NotFoundError"
          ? "No microphone is available."
          : "Unable to start microphone recording.";
      dispatch({ type: "REQUEST_FAILED", fence, message });
    }
  };

  const busy = isBusy(state.status);
  return <div className="flex items-center gap-2" aria-live="polite">
    {state.status === "recording"
      ? <button className="btn secondary h-10 w-10 p-0" type="button" aria-label="Stop recording" title="Stop recording" onClick={() => stop()}><Square size={16} aria-hidden /></button>
      : <button className="btn secondary h-10 w-10 p-0" type="button" aria-label="Start recording" title="Start recording" disabled={disabled || busy} onClick={() => void start()}><Mic size={16} aria-hidden /></button>}
    {busy ? <span className="text-sm text-slate-600">{state.status === "transcribing" ? "Transcribing..." : state.status === "recording" ? `Recording... ${formatDuration(elapsedSeconds)} / ${formatDuration(maximumSeconds)}` : "Preparing microphone..."}</span> : null}
    {busy ? <button className="btn secondary h-10 w-10 p-0" type="button" aria-label="Cancel recording" title="Cancel recording" onClick={cancelActive}><X size={16} aria-hidden /></button> : null}
    {state.status === "failed" && retainedFile.current ? <button className="btn secondary" type="button" onClick={retry}>Retry transcription</button> : null}
    {state.status === "failed" && state.message ? <p className="text-sm text-red-700" role="alert">{state.message}</p> : null}
  </div>;
}
