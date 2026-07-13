import type { FocusProofEvent } from "./contracts";

export class ApiError extends Error {
  status: number;
  code: string;
  retryable: boolean;

  constructor({ status, code, retryable, message }: { status: number; code: string; retryable: boolean; message: string }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

const sessionIdPattern = /^sess_[A-Za-z0-9_:-]+$/;

export function isAllowedFocusProofRequest(method: string, path: readonly string[]): boolean {
  if (path.some((part) => part.includes("..") || part.includes("/") || part.length === 0)) {
    return false;
  }
  const normalized = method.toUpperCase();
  if (normalized === "GET" && path.length === 1 && path[0] === "health") return true;
  if (normalized === "POST" && path.length === 1 && path[0] === "sessions") return true;
  if (path.length < 2 || path[0] !== "sessions" || !sessionIdPattern.test(path[1])) return false;
  if (normalized === "GET" && path.length === 2) return true;
  if (path.length !== 3) return false;
  if (normalized === "POST" && ["evidence", "answer", "review"].includes(path[2])) return true;
  if (normalized === "GET" && ["events", "reviews"].includes(path[2])) return true;
  return false;
}

export function mapApiError(status: number, payload: unknown): ApiError {
  const data = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const code = typeof data.code === "string" ? data.code : status === 0 ? "network_error" : "request_failed";
  const retryable = data.retryable === true || status === 409 || status === 503 || status === 0;
  if (status === 409 || code === "session_busy") {
    return new ApiError({ status, code: "session_busy", retryable: true, message: "Session processing is still in progress. Please retry shortly." });
  }
  if (status === 503 || code === "backend_unavailable" || code === "runtime_unavailable") {
    return new ApiError({ status: status || 503, code, retryable: true, message: "Agent Runtime current unavailable. Page data has been preserved." });
  }
  if (status === 403 || status === 404) {
    return new ApiError({ status, code, retryable: false, message: "This session is not accessible." });
  }
  if (status === 0 || code === "network_error") {
    return new ApiError({ status, code: "network_error", retryable: true, message: "Connection failed. Nothing was submitted." });
  }
  return new ApiError({ status, code, retryable, message: "FocusProof request failed. Please retry." });
}

export function sortEventsBySequence(events: FocusProofEvent[]): FocusProofEvent[] {
  return [...events].sort((left, right) => left.sequence - right.sequence);
}
