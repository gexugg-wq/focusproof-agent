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

async function readBoundedResponse(response: Response): Promise<string | null> {
  const declared = Number(response.headers.get("content-length") ?? "0");
  if (declared > upstreamResponseLimit) return null;
  if (!response.body) return "";
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > upstreamResponseLimit) { await reader.cancel(); return null; }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
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
  const isMedia = request.method === "POST" && path.length === 4 && path[0] === "sessions" && path[2] === "evidence" && path[3] === "image";
  const declaredLength = Number(request.headers.get("content-length") ?? "0");
  if (isMedia && declaredLength > mediaFallbackLimit) return NextResponse.json({ code: "request_too_large", retryable: false }, { status: 413 });
  let body: BodyInit | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    if (isMedia) {
      body = request.body ?? undefined;
      if (!body) {
        const buffer = await request.arrayBuffer();
        if (buffer.byteLength > mediaFallbackLimit) return NextResponse.json({ code: "request_too_large", retryable: false }, { status: 413 });
        body = buffer;
      }
    } else {
      body = await request.text();
    }
  }
  const headers = new Headers({
    "content-type": request.headers.get("content-type") ?? "application/json"
  });
  const authorization = getForwardableBearer(request.headers);
  if (authorization) headers.set("authorization", authorization);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), getProxyTimeoutMs(request.method, path));
  let upstream: Response;
  try {
    const init: NodeStreamRequestInit = {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: controller.signal
    };
    if (body instanceof ReadableStream) init.duplex = "half";
    upstream = await fetch(target, init);
  } catch {
    return NextResponse.json({ code: "backend_unavailable", retryable: true }, { status: 503 });
  } finally {
    clearTimeout(timeout);
  }
  const text = await readBoundedResponse(upstream);
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
}

export const GET = proxy;
export const POST = proxy;
