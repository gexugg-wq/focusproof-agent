import { createServer } from "node:http";
import { describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { focusProofApi, isApiError } from "@/lib/api/client";
import { ApiError, isAllowedFocusProofRequest, mapApiError, sortEventsBySequence } from "@/lib/api/errors";
import { getProxyTimeoutMs } from "@/lib/api/proxy-timeout";
import { GET, POST } from "@/app/api/focusproof/[...path]/route";

const allowed = [
  ["GET", ["health"]],
  ["POST", ["sessions"]],
  ["GET", ["sessions", "sess_1"]],
  ["POST", ["sessions", "sess_1", "evidence"]],
  ["POST", ["sessions", "sess_1", "evidence", "image"]],
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

  it("allows real review requests to outlive the backend review budget", () => {
    expect(getProxyTimeoutMs("GET", ["health"])).toBe(15_000);
    expect(getProxyTimeoutMs("POST", ["sessions", "sess_1", "review"])).toBeGreaterThan(60_000);
  });

  it("gives image uploads a bounded extended timeout", () => {
    expect(getProxyTimeoutMs("POST", ["sessions", "sess_1", "evidence", "image"])).toBeGreaterThan(15_000);
  });
});

describe("multipart client", () => {
  it("preserves FormData and lets fetch generate the multipart Content-Type", async () => {
    const form = new FormData();
    form.append("file", new File([new Uint8Array([0, 255, 7])], "proof.png", { type: "image/png" }));
    form.append("explanation", "This diagram shows the causal chain.");
    const browserFetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ evidenceId: "ev_image", mediaType: "image/png", normalizedBytes: 3, replayed: false }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", browserFetch);

    await focusProofApi.submitImageEvidence("sess_1", form);

    const [, init] = browserFetch.mock.calls.at(-1)!;
    expect(init?.body).toBe(form);
    expect(new Headers(init?.headers).has("content-type")).toBe(false);
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

  it("BFF forwards multipart bytes and boundary unchanged", async () => {
    const bytes = new Uint8Array([45, 45, 120, 13, 10, 0, 255, 13, 10, 45, 45, 120, 45, 45]);
    const upstreamFetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ evidenceId: "ev_image", mediaType: "image/png", normalizedBytes: 2, replayed: false }), { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", upstreamFetch);
    const request = new NextRequest("http://localhost/api/focusproof/sessions/sess_1/evidence/image", { method: "POST", headers: { "content-type": "multipart/form-data; boundary=x" }, body: bytes });

    const response = await POST(request, { params: Promise.resolve({ path: ["sessions", "sess_1", "evidence", "image"] }) });

    expect(response.status).toBe(200);
    const [, init] = upstreamFetch.mock.calls[0];
    expect(new Headers(init.headers).get("content-type")).toBe("multipart/form-data; boundary=x");
    expect(new Uint8Array(await new Response(init.body).arrayBuffer())).toEqual(bytes);
  });

  it("BFF rejects oversized image fallback bodies before upstream fetch", async () => {
    const upstreamFetch = vi.fn();
    vi.stubGlobal("fetch", upstreamFetch);
    const request = new NextRequest("http://localhost/api/focusproof/sessions/sess_1/evidence/image", { method: "POST", headers: { "content-type": "multipart/form-data; boundary=x", "content-length": String(11 * 1024 * 1024 + 1) }, body: new Uint8Array([1]) });
    const response = await POST(request, { params: Promise.resolve({ path: ["sessions", "sess_1", "evidence", "image"] }) });
    expect(response.status).toBe(413);
    expect(upstreamFetch).not.toHaveBeenCalled();
    await expect(response.json()).resolves.toEqual({ code: "request_too_large", retryable: false });
  });

  it("keeps JSON request forwarding behavior", async () => {
    const upstreamFetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ sessionId: "sess_1", status: "running" }), { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", upstreamFetch);
    const json = JSON.stringify({ domain: "general", title: "T", goal: "G" });
    const request = new NextRequest("http://localhost/api/focusproof/sessions", { method: "POST", headers: { "content-type": "application/json" }, body: json });
    const response = await POST(request, { params: Promise.resolve({ path: ["sessions"] }) });
    expect(response.status).toBe(200);
    const [, init] = upstreamFetch.mock.calls[0];
    expect(await new Response(init.body).text()).toBe(json);
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
  it("streams real multipart bytes through Node fetch with its boundary intact", async () => {
    vi.unstubAllGlobals();
    let received = Buffer.alloc(0);
    let contentType = "";
    const server = createServer((request, response) => {
      contentType = String(request.headers["content-type"] ?? "");
      const chunks: Buffer[] = [];
      request.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
      request.on("end", () => {
        received = Buffer.concat(chunks);
        response.setHeader("content-type", "application/json");
        response.end('{"evidenceId":"ev_real","mediaType":"image/png","normalizedBytes":3,"replayed":false}');
      });
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("missing loopback address");
      vi.stubEnv("FOCUSPROOF_API_BASE_URL", `http://127.0.0.1:${address.port}`);
      const boundary = "focusproof-real-boundary";
      const bytes = new TextEncoder().encode(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="proof.png"\r\nContent-Type: image/png\r\n\r\n\u0000\u00ff\u0007\r\n--${boundary}--\r\n`);
      const response = await POST(new NextRequest("http://localhost/api/focusproof/sessions/sess_1/evidence/image", { method: "POST", headers: { "content-type": `multipart/form-data; boundary=${boundary}` }, body: bytes }), { params: Promise.resolve({ path: ["sessions", "sess_1", "evidence", "image"] }) });
      expect(response.status).toBe(200);
      expect(contentType).toBe(`multipart/form-data; boundary=${boundary}`);
      expect(received).toEqual(Buffer.from(bytes));
    } finally {
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
      vi.unstubAllEnvs();
    }
  });

  it("bounds a chunked upstream response", async () => {
    vi.unstubAllGlobals();
    const server = createServer((_request, response) => {
      response.setHeader("content-type", "application/json");
      response.write('{"padding":"');
      response.write("x".repeat(1024 * 1024 + 1));
      response.end('"}');
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("missing loopback address");
      vi.stubEnv("FOCUSPROOF_API_BASE_URL", `http://127.0.0.1:${address.port}`);
      const response = await GET(new NextRequest("http://localhost/api/focusproof/health"), { params: Promise.resolve({ path: ["health"] }) });
      expect(response.status).toBe(502);
      await expect(response.json()).resolves.toEqual({ code: "upstream_response_too_large", retryable: false });
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
      vi.unstubAllEnvs();
    }
  });

  it("accepts exactly 11 MiB, rejects one byte more, and keeps non-media on the normal policy", async () => {
    const upstreamFetch = vi.fn().mockResolvedValue(new Response("{}", { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", upstreamFetch);
    const mediaPath = { params: Promise.resolve({ path: ["sessions", "sess_1", "evidence", "image"] }) };
    const exact = await POST(new NextRequest("http://localhost/upload", { method: "POST", headers: { "content-type": "multipart/form-data; boundary=x", "content-length": String(11 * 1024 * 1024) } }), mediaPath);
    expect(exact.status).toBe(200);
    const over = await POST(new NextRequest("http://localhost/upload", { method: "POST", headers: { "content-type": "multipart/form-data; boundary=x", "content-length": String(11 * 1024 * 1024 + 1) } }), mediaPath);
    expect(over.status).toBe(413);
    const normal = await POST(new NextRequest("http://localhost/evidence", { method: "POST", headers: { "content-type": "multipart/form-data; boundary=x", "content-length": String(11 * 1024 * 1024 + 1) }, body: new Uint8Array([1]) }), { params: Promise.resolve({ path: ["sessions", "sess_1", "evidence"] }) });
    expect(normal.status).not.toBe(413);
  });

  it("does not forward hop-by-hop or content-length headers", async () => {
    const upstreamFetch = vi.fn().mockResolvedValue(new Response("{}", { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", upstreamFetch);
    await POST(new NextRequest("http://localhost/upload", { method: "POST", headers: { "content-type": "multipart/form-data; boundary=x", connection: "keep-alive", "content-length": "0" } }), { params: Promise.resolve({ path: ["sessions", "sess_1", "evidence", "image"] }) });
    const headers = new Headers(upstreamFetch.mock.calls[0][1].headers);
    expect(headers.has("connection")).toBe(false);
    expect(headers.has("content-length")).toBe(false);
  });

  it("recovers uploaded evidence from the real upstream session response", async () => {
    vi.unstubAllGlobals();
    let evidence: unknown[] = [];
    let uploadFields = "";
    const server = createServer((request, response) => {
      if (request.method === "POST") {
        const chunks: Buffer[] = [];
        request.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
        request.on("end", () => {
          uploadFields = Buffer.concat(chunks).toString("latin1");
          evidence = [{ evidenceId: "ev_persisted", evidenceType: "image", contentHash: "sha256:safe", textContent: "Server restored explanation", sourceUrl: null, metadata: { mediaType: "image/png" } }];
          response.setHeader("content-type", "application/json"); response.end('{"evidenceId":"ev_persisted","mediaType":"image/png","normalizedBytes":3,"replayed":false}');
        });
      } else { response.setHeader("content-type", "application/json"); response.end(JSON.stringify({ sessionId: "sess_1", state: { evidence }, view: {} })); }
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
      const address = server.address(); if (!address || typeof address === "string") throw new Error("address");
      vi.stubEnv("FOCUSPROOF_API_BASE_URL", `http://127.0.0.1:${address.port}`);
      const form = new FormData(); form.append("file", new File([new Uint8Array([0, 255, 7])], "proof.png", { type: "image/png" })); form.append("explanation", "Server restored explanation"); form.append("idempotency_key", "stable-key");
      const upload = await POST(new NextRequest("http://localhost/upload", { method: "POST", body: form }), { params: Promise.resolve({ path: ["sessions", "sess_1", "evidence", "image"] }) });
      expect(upload.status).toBe(200);
      expect(uploadFields).toContain('name="file"'); expect(uploadFields).toContain('name="explanation"'); expect(uploadFields).toContain('name="idempotency_key"'); expect(uploadFields).toContain("stable-key");
      const refreshed = await GET(new NextRequest("http://localhost/session"), { params: Promise.resolve({ path: ["sessions", "sess_1"] }) });
      expect((await refreshed.json()).state.evidence[0].textContent).toBe("Server restored explanation");
      expect(localStorage.length).toBe(0);
    } finally { await new Promise<void>((resolve) => server.close(() => resolve())); vi.unstubAllEnvs(); }
  });

  it("aborts the upstream request at the route timeout", async () => {
    vi.useFakeTimers();
    const upstreamFetch = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    vi.stubGlobal("fetch", upstreamFetch);
    const pending = POST(new NextRequest("http://localhost/upload", { method: "POST", body: new Uint8Array([1]) }), { params: Promise.resolve({ path: ["sessions", "sess_1", "evidence", "image"] }) });
    await vi.advanceTimersByTimeAsync(45_000);
    const response = await pending;
    expect(response.status).toBe(503);
    const call = upstreamFetch.mock.calls[0];
    expect(call).toBeDefined();
    const signal = call?.[1].signal;
    expect(signal?.aborted).toBe(true);
    vi.useRealTimers();
  });

  it("rejects wrong methods and nearby multipart routes", () => {
    expect(isAllowedFocusProofRequest("PUT", ["sessions", "sess_1", "evidence", "image"])).toBe(false);
    expect(isAllowedFocusProofRequest("POST", ["sessions", "sess_1", "evidence", "image", "extra"])).toBe(false);
    expect(isAllowedFocusProofRequest("POST", ["sessions", "sess_1", "image"])).toBe(false);
  });
});
