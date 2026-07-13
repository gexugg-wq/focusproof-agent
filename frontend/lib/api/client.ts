import { mapApiError, type ApiError } from "./errors";
import type { CreateSessionInput, FocusProofEvent, RuntimeReviewResult, SessionDetail, SubmitEvidenceRequest, SyncResponse } from "./contracts";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch("/api/focusproof" + path, {
      ...init,
      headers: {
        "content-type": "application/json",
        ...(init?.headers ?? {})
      }
    });
  } catch (error) {
    throw mapApiError(0, { code: "network_error", retryable: true, error: String(error) });
  }
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw mapApiError(response.status, payload);
  }
  return payload as T;
}

export function isApiError(error: unknown): error is ApiError {
  return Boolean(error && typeof error === "object" && "status" in error && "message" in error);
}

export const focusProofApi = {
  health: () => requestJson<{ status: string }>("/health"),
  createSession: (input: CreateSessionInput) => requestJson<{ sessionId: string; status: string }>("/sessions", { method: "POST", body: JSON.stringify(input) }),
  getSession: (sessionId: string) => requestJson<SessionDetail>("/sessions/" + encodeURIComponent(sessionId)),
  submitEvidence: (sessionId: string, input: SubmitEvidenceRequest) => requestJson<SyncResponse>("/sessions/" + encodeURIComponent(sessionId) + "/evidence", { method: "POST", body: JSON.stringify(input) }),
  submitAnswer: (sessionId: string, input: { questionId: string; answer: string }) => requestJson<SyncResponse>("/sessions/" + encodeURIComponent(sessionId) + "/answer", { method: "POST", body: JSON.stringify(input) }),
  requestReview: (sessionId: string) => requestJson<RuntimeReviewResult>("/sessions/" + encodeURIComponent(sessionId) + "/review", { method: "POST", body: JSON.stringify({}) }),
  getEvents: (sessionId: string) => requestJson<{ events: FocusProofEvent[] }>("/sessions/" + encodeURIComponent(sessionId) + "/events")
};
