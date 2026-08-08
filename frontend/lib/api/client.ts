import { ApiError, mapApiError } from "./errors";
import type { CreateSessionInput, FocusProofEvent, ReviewProjection, RuntimeReviewResult, SessionDetail, SubmitEvidenceRequest, SyncResponse } from "./contracts";
import { fetchWithOidcAccessToken } from "@/lib/auth/browser";

async function parseResponsePayload(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  const contentType = response.headers.get("content-type") ?? "";
  try {
    return JSON.parse(text);
  } catch {
    if (!contentType.includes("application/json")) {
      return { code: response.ok ? "non_json_response" : "request_failed", rawText: text.slice(0, 120) };
    }
    return { code: "invalid_json_response" };
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetchWithOidcAccessToken("/api/focusproof" + path, {
      ...init,
      headers: {
        "content-type": "application/json",
        ...(init?.headers ?? {})
      }
    });
  } catch (error) {
    throw mapApiError(0, { code: "network_error", retryable: true, error: String(error) });
  }
  const payload = await parseResponsePayload(response);
  if (!response.ok) {
    throw mapApiError(response.status, payload);
  }
  return payload as T;
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function getSafeErrorMessage(error: unknown): string {
  if (isApiError(error) || error instanceof Error) return error.message;
  return "FocusProof request failed. Please retry.";
}

export const focusProofApi = {
  health: () => requestJson<{ status: string }>("/health"),
  createSession: (input: CreateSessionInput) => requestJson<{ sessionId: string; status: string }>("/sessions", { method: "POST", body: JSON.stringify(input) }),
  getSession: (sessionId: string) => requestJson<SessionDetail>("/sessions/" + encodeURIComponent(sessionId)),
  submitEvidence: (sessionId: string, input: SubmitEvidenceRequest) => requestJson<SyncResponse>("/sessions/" + encodeURIComponent(sessionId) + "/evidence", { method: "POST", body: JSON.stringify(input) }),
  submitAnswer: (sessionId: string, input: { questionId: string; answer: string }) => requestJson<SyncResponse>("/sessions/" + encodeURIComponent(sessionId) + "/answer", { method: "POST", body: JSON.stringify(input) }),
  requestReview: (sessionId: string) => requestJson<RuntimeReviewResult>("/sessions/" + encodeURIComponent(sessionId) + "/review", { method: "POST", body: JSON.stringify({}) }),
  getEvents: (sessionId: string) => requestJson<{ events: FocusProofEvent[] }>("/sessions/" + encodeURIComponent(sessionId) + "/events"),
  getReviews: (sessionId: string) => requestJson<{ reviews: ReviewProjection[] }>("/sessions/" + encodeURIComponent(sessionId) + "/reviews")
};
