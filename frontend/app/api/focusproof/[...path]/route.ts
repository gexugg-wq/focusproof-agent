import { NextRequest, NextResponse } from "next/server";
import { isAllowedFocusProofRequest } from "@/lib/api/errors";
import { getProxyTimeoutMs } from "@/lib/api/proxy-timeout";
import { getForwardableBearer } from "@/lib/auth/server";

type RouteContext = {
  params: Promise<{
    path: string[];
  }>;
};
const mediaFallbackLimit = 11 * 1024 * 1024;
const upstreamResponseLimit = 1024 * 1024;
type NodeStreamRequestInit = RequestInit & { duplex?: "half" };

class RequestTooLargeError extends Error {}
class ProxyTimeoutError extends Error {}

function awaitAbortable<T>(operation: () => Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(new ProxyTimeoutError());
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(new ProxyTimeoutError());
    signal.addEventListener("abort", abort, { once: true });
    Promise.resolve()
      .then(() => {
        if (signal.aborted) throw new ProxyTimeoutError();
        return operation();
      })
      .then(resolve, reject)
      .finally(() => signal.removeEventListener("abort", abort));
  });
}

async function readAbortableText(stream: ReadableStream<Uint8Array> | null, signal: AbortSignal): Promise<string> {
  if (!stream) return "";
  const reader = stream.getReader();
  let released = false;
  let cancelPromise: Promise<void> | undefined;
  let rejectAbort!: (error: ProxyTimeoutError) => void;
  const abortRead = new Promise<never>((_, reject) => { rejectAbort = reject; });
  const releaseReader = () => {
    if (released) return;
    released = true;
    try {
      reader.releaseLock();
    } catch {
      return;
    }
  };
  const cancelSource = () => {
    if (!cancelPromise) {
      cancelPromise = reader.cancel(new ProxyTimeoutError()).catch(() => undefined);
      void cancelPromise.finally(releaseReader);
    }
    return cancelPromise;
  };
  const abort = () => {
    rejectAbort(new ProxyTimeoutError());
    void cancelSource();
  };
  if (signal.aborted) abort();
  else signal.addEventListener("abort", abort, { once: true });

  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await Promise.race([reader.read(), abortRead]);
      if (done) break;
      total += value.byteLength;
      chunks.push(value);
    }
  } finally {
    signal.removeEventListener("abort", abort);
    if (signal.aborted) void cancelSource();
    else releaseReader();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder().decode(bytes);
}

type LimitedMediaStream = {
  body: ReadableStream<Uint8Array>;
  drain: () => Promise<void>;
  readonly exceeded: boolean;
};

function limitMediaStream(
  stream: ReadableStream<Uint8Array>,
  onTooLarge: () => void,
  signal: AbortSignal
): LimitedMediaStream {
  const reader = stream.getReader();
  let total = 0;
  let finished = false;
  let exceeded = false;
  let released = false;
  let readTail = Promise.resolve();
  let cancelPromise: Promise<void> | undefined;
  let resolveAbort!: (result: ReadableStreamReadResult<Uint8Array>) => void;
  const abortRead = new Promise<ReadableStreamReadResult<Uint8Array>>((resolve) => { resolveAbort = resolve; });

  const releaseReader = (): void => {
    if (released) return;
    released = true;
    try {
      reader.releaseLock();
    } catch {
      return;
    }
  };
  const cancelSource = (reason?: unknown): void => {
    if (!cancelPromise) {
      cancelPromise = reader.cancel(reason).catch(() => undefined);
      void cancelPromise.finally(releaseReader);
    }
  };
  const finish = (): void => {
    if (finished) return;
    finished = true;
    signal.removeEventListener("abort", abortSource);
    if (!cancelPromise) releaseReader();
  };
  const abortSource = (): void => {
    if (finished) return;
    resolveAbort({ done: true, value: undefined });
    cancelSource(new ProxyTimeoutError());
    finish();
  };
  if (signal.aborted) abortSource();
  else signal.addEventListener("abort", abortSource, { once: true });


  async function readNext(): Promise<ReadableStreamReadResult<Uint8Array>> {
    let release!: () => void;
    const previous = readTail;
    readTail = new Promise<void>((resolve) => { release = resolve; });
    await previous;
    try {
      if (finished || signal.aborted) return { done: true, value: undefined };
      return await Promise.race([reader.read(), abortRead]);
    } finally { release(); }
  }

  function markTooLarge(): void {
    if (!exceeded) {
      exceeded = true;
      onTooLarge();
    }
    cancelSource(new RequestTooLargeError());
    finish();
  }

  const drain = async (): Promise<void> => {
    while (!finished) {
      const { done, value } = await readNext();
      if (done) { finish(); break; }
      total += value.byteLength;
      if (total > mediaFallbackLimit) { markTooLarge(); break; }
    }
  };

  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { done, value } = await readNext();
        if (done) { finish(); controller.close(); return; }
        total += value.byteLength;
        if (total > mediaFallbackLimit) {
          markTooLarge();
          controller.error(new RequestTooLargeError());
          return;
        }
        controller.enqueue(value);
      } catch (error) {
        finish();
        controller.error(error);
      }
    },
    async cancel(reason) {
      if (signal.aborted) { cancelSource(reason); finish(); return; }
      await drain();
    }
  });
  return { body, drain, get exceeded() { return exceeded; } };
}

async function readBoundedResponse(response: Response, signal: AbortSignal): Promise<string | null> {
  const declared = Number(response.headers.get("content-length") ?? "0");
  if (declared > upstreamResponseLimit) {
    void response.body?.cancel().catch(() => undefined);
    return null;
  }
  if (!response.body) return "";
  const reader = response.body.getReader();
  const cancelOnAbort = () => { void reader.cancel(new ProxyTimeoutError()); };
  const chunks: Uint8Array[] = [];
  let total = 0;
  signal.addEventListener("abort", cancelOnAbort, { once: true });
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (signal.aborted) throw new ProxyTimeoutError();
      if (done) break;
      total += value.byteLength;
      if (total > upstreamResponseLimit) { await reader.cancel(); return null; }
      chunks.push(value);
    }
  } finally {
    signal.removeEventListener("abort", cancelOnAbort);
    reader.releaseLock();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder().decode(bytes);
}

async function proxy(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  const { path = [] } = await context.params;
  if (!isAllowedFocusProofRequest(request.method, path)) {
    return NextResponse.json({ code: "forbidden_proxy_path", retryable: false }, { status: 403 });
  }
  const baseUrl = process.env.FOCUSPROOF_API_BASE_URL ?? "http://127.0.0.1:8000";
  const target = baseUrl.replace(/\/$/, "") + "/" + path.map(encodeURIComponent).join("/");
  const isImage = request.method === "POST" && path.length === 4 && path[0] === "sessions" && path[2] === "evidence" && path[3] === "image";
  const isTranscription = request.method === "POST" && path.length === 3 && path[0] === "sessions" && path[2] === "transcriptions";
  const isMedia = isImage || isTranscription;
  const controller = new AbortController();
  const abortFromInbound = () => controller.abort(request.signal.reason);
  if (request.signal.aborted) abortFromInbound();
  else request.signal.addEventListener("abort", abortFromInbound, { once: true });
  try {
    const declaredLength = Number(request.headers.get("content-length") ?? "0");
  if (isMedia && declaredLength > mediaFallbackLimit) return NextResponse.json({ code: "request_too_large", retryable: false }, { status: 413 });
  let body: BodyInit | undefined;
  let limitedBody: LimitedMediaStream | undefined;
  let mediaLimitExceeded = false;
  try {
    if (request.method !== "GET" && request.method !== "HEAD") {
      if (isMedia) {
        limitedBody = request.body ? limitMediaStream(request.body, () => { mediaLimitExceeded = true; }, controller.signal) : undefined;
        body = limitedBody?.body;
        if (!body) {
          const buffer = await awaitAbortable(() => request.arrayBuffer(), controller.signal);
          if (buffer.byteLength > mediaFallbackLimit) return NextResponse.json({ code: "request_too_large", retryable: false }, { status: 413 });
          body = buffer;
        }
      } else {
        body = await readAbortableText(request.body, controller.signal);
      }
    }
  } catch (error) {
    if (controller.signal.aborted) return NextResponse.json({ code: "backend_unavailable", retryable: true }, { status: 503 });
    throw error;
  }
  const headers = new Headers({
    "content-type": request.headers.get("content-type") ?? "application/json"
  });
  const authorization = getForwardableBearer(request.headers);
  if (authorization) headers.set("authorization", authorization);
  if (isTranscription && request.headers.has("idempotency-key")) headers.set("idempotency-key", request.headers.get("idempotency-key")!);
  const timeout = setTimeout(() => controller.abort(), getProxyTimeoutMs(request.method, path));
  let upstream: Response;
  let text: string | null;
  try {
    const init: NodeStreamRequestInit = {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: controller.signal
    };
    if (body instanceof ReadableStream) init.duplex = "half";
    upstream = await awaitAbortable(() => fetch(target, init), controller.signal);
    if (limitedBody) await limitedBody.drain();
    if (limitedBody?.exceeded) return NextResponse.json({ code: "request_too_large", retryable: false }, { status: 413 });
    if (controller.signal.aborted) throw new ProxyTimeoutError();
    text = await readBoundedResponse(upstream, controller.signal);
  } catch {
    if (mediaLimitExceeded) return NextResponse.json({ code: "request_too_large", retryable: false }, { status: 413 });
    return NextResponse.json({ code: "backend_unavailable", retryable: true }, { status: 503 });
  } finally {
    clearTimeout(timeout);
  }
  if (text === null) return NextResponse.json({ code: "upstream_response_too_large", retryable: false }, { status: 502 });
  const contentType = upstream.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    if (!upstream.ok) {
      return NextResponse.json({ code: "upstream_non_json", retryable: false }, { status: upstream.status });
    }
    return NextResponse.json({ code: "upstream_non_json", retryable: false }, { status: 502 });
  }
  const responseHeaders = new Headers({ "content-type": "application/json" });
  if (upstream.status === 401 && upstream.headers.get("www-authenticate") === "Bearer") {
    responseHeaders.set("www-authenticate", "Bearer");
  }
  return new NextResponse(text || "null", {
    status: upstream.status,
    headers: responseHeaders
  });
  } finally {
    request.signal.removeEventListener("abort", abortFromInbound);
  }
}

export const GET = proxy;
export const POST = proxy;
