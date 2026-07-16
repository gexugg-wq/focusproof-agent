import { describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { focusProofApi, isApiError } from "@/lib/api/client";
import { ApiError, isAllowedFocusProofRequest, mapApiError, sortEventsBySequence } from "@/lib/api/errors";
import { GET } from "@/app/api/focusproof/[...path]/route";

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
  it("maps session_busy conflicts as temporary and retryable", () => {
    const error = mapApiError(409, { code: "session_busy", retryable: true });
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toBeInstanceOf(Error);
    expect(error.code).toBe("session_busy");
    expect(error.message).toContain("Session processing");
    expect(error.retryable).toBe(true);
  });

  it("maps session_finalized conflicts as permanent with a clear message", () => {
    const error = mapApiError(409, { code: "session_finalized", retryable: false });

    expect(error).toMatchObject({
      code: "session_finalized",
      retryable: false,
      message: "This session is complete. New facts cannot be submitted."
    });
  });

  it("keeps unknown conflicts generic and honors explicit retryability", () => {
    const permanent = mapApiError(409, { code: "unknown_conflict", retryable: false });
    const temporary = mapApiError(409, { code: "unknown_conflict", retryable: true });

    expect(permanent).toMatchObject({
      code: "unknown_conflict",
      retryable: false,
      message: "FocusProof request failed. Please retry."
    });
    expect(temporary).toMatchObject({
      code: "unknown_conflict",
      retryable: true,
      message: "FocusProof request failed. Please retry."
    });
  });

  it("maps access errors without pretending success", () => {
    expect(mapApiError(404, { detail: "Session not found" }).message).toContain("not accessible");
  });

  it("does not expose SyntaxError when an upstream returns HTML 500", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>nope</html>", { status: 500, headers: { "content-type": "text/html" } })));
    await expect(focusProofApi.health()).rejects.toMatchObject({ code: "request_failed", status: 500, retryable: false });
    await expect(focusProofApi.health()).rejects.not.toThrow(/SyntaxError/);
  });

  it("maps network failures to safe ApiError instances", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    await expect(focusProofApi.health()).rejects.toMatchObject({ code: "network_error", retryable: true });
  });

  it("BFF returns structured 503 when FastAPI is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("connect refused")));
    const response = await GET(new NextRequest("http://localhost/api/focusproof/health"), { params: Promise.resolve({ path: ["health"] }) });
    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({ code: "backend_unavailable", retryable: true });
  });

  it("BFF preserves non-JSON upstream failures as safe JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<h1>boom</h1>", { status: 500, headers: { "content-type": "text/html" } })));
    const response = await GET(new NextRequest("http://localhost/api/focusproof/health"), { params: Promise.resolve({ path: ["health"] }) });
    expect(response.status).toBe(500);
    await expect(response.json()).resolves.toEqual({ code: "upstream_non_json", retryable: false });
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
