import { describe, expect, it } from "vitest";
import { isAllowedFocusProofRequest, mapApiError, sortEventsBySequence } from "@/lib/api/errors";

const allowed = [
  ["GET", ["health"]],
  ["POST", ["sessions"]],
  ["GET", ["sessions", "sess_1"]],
  ["POST", ["sessions", "sess_1", "evidence"]],
  ["POST", ["sessions", "sess_1", "answer"]],
  ["POST", ["sessions", "sess_1", "review"]],
  ["GET", ["sessions", "sess_1", "events"]],
  ["GET", ["sessions", "sess_1", "reviews"]]
] as const;

describe("FocusProof BFF policy", () => {
  it.each(allowed)("allows %s %j", (method, path) => {
    expect(isAllowedFocusProofRequest(method, path)).toBe(true);
  });

  it("blocks debug routes and open forwarding", () => {
    expect(isAllowedFocusProofRequest("GET", ["debug", "openhands", "env-status"])).toBe(false);
    expect(isAllowedFocusProofRequest("GET", ["sessions", "sess_1", "../../debug"])).toBe(false);
    expect(isAllowedFocusProofRequest("POST", ["sessions", "sess_1", "proof"])).toBe(false);
  });
});

describe("API errors", () => {
  it("maps retryable runtime and busy errors to neutral messages", () => {
    expect(mapApiError(409, { code: "session_busy", retryable: true }).message).toContain("processing");
    expect(mapApiError(503, { code: "runtime_unavailable", retryable: true }).retryable).toBe(true);
  });

  it("maps access errors without pretending success", () => {
    expect(mapApiError(404, { detail: "Session not found" }).message).toContain("not accessible");
  });
});

describe("event sorting", () => {
  it("sorts Build Log events by sequence", () => {
    const events = [
      { id: "evt_2", sessionId: "sess_1", type: "review.completed", sequence: 2, createdAt: "now", actor: "agent", payload: {} },
      { id: "evt_1", sessionId: "sess_1", type: "session.created", sequence: 1, createdAt: "now", actor: "system", payload: {} }
    ];
    expect(sortEventsBySequence(events).map((event) => event.id)).toEqual(["evt_1", "evt_2"]);
  });
});
