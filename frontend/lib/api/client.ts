import { ApiError, mapApiError } from "./errors";
import type { CreateSessionInput, FocusProofEvent, ImageEvidenceResponse, ReviewProjection, RuntimeReviewResult, SessionDetail, SubmitEvidenceRequest, SyncResponse, TranscriptionResponse } from "./contracts";
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

async function requestMultipart<T>(path: string, body: FormData, init?: Omit<RequestInit, "body" | "method">): Promise<T> {
  const errorMapping = { inferRetryableFromStatus: !path.endsWith("/transcriptions") };
  let response: Response;
  try {
    response = await fetchWithOidcAccessToken("/api/focusproof" + path, { ...init, method: "POST", body });
  } catch (error) {
    throw mapApiError(0, { code: "network_error", retryable: true, error: String(error) }, errorMapping);
  }
  const payload = await parseResponsePayload(response);
  if (!response.ok) throw mapApiError(response.status, payload, errorMapping);
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
  submitImageEvidence: (sessionId: string, form: FormData) => requestMultipart<ImageEvidenceResponse>("/sessions/" + encodeURIComponent(sessionId) + "/evidence/image", form),
  transcribe: (sessionId: string, file: File, languageHint: "auto" | "zh" | "en", idempotencyKey: string, signal: AbortSignal) => {
    const form = new FormData();
    form.append("file", file);
    form.append("languageHint", languageHint);
    return requestMultipart<TranscriptionResponse>("/sessions/" + encodeURIComponent(sessionId) + "/transcriptions", form, { headers: { "Idempotency-Key": idempotencyKey }, signal });
  },
  submitAnswer: (sessionId: string, input: { questionId: string; answer: string }) => requestJson<SyncResponse>("/sessions/" + encodeURIComponent(sessionId) + "/answer", { method: "POST", body: JSON.stringify(input) }),
  requestReview: (sessionId: string) => requestJson<RuntimeReviewResult>("/sessions/" + encodeURIComponent(sessionId) + "/review", { method: "POST", body: JSON.stringify({}) }),
  getEvents: (sessionId: string) => requestJson<{ events: FocusProofEvent[] }>("/sessions/" + encodeURIComponent(sessionId) + "/events"),
  getReviews: (sessionId: string) => requestJson<{ reviews: ReviewProjection[] }>("/sessions/" + encodeURIComponent(sessionId) + "/reviews")
};
