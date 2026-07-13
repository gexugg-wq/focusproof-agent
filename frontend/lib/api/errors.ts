import type { FocusProofEvent } from "./contracts";

export type ApiError = {
  status: number;
  code: string;
  message: string;
  retryable: boolean;
};

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
    return { status, code: "session_busy", retryable: true, message: "The session is still processing. Please retry shortly." };
  }
  if (status === 503) {
    return { status, code, retryable: true, message: "The FocusProof runtime is unavailable. Your page state is preserved." };
  }
  if (status === 403 || status === 404) {
    return { status, code, retryable: false, message: "This session is not accessible." };
  }
  if (status === 0) {
    return { status, code, retryable: true, message: "Connection failed. Nothing was submitted." };
  }
  return { status, code, retryable, message: "FocusProof request failed. Please retry." };
}

export function sortEventsBySequence(events: FocusProofEvent[]): FocusProofEvent[] {
  return [...events].sort((left, right) => left.sequence - right.sequence);
}
