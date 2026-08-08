import { NextRequest, NextResponse } from "next/server";
import { isAllowedFocusProofRequest } from "@/lib/api/errors";
import { getProxyTimeoutMs } from "@/lib/api/proxy-timeout";
import { getForwardableBearer } from "@/lib/auth/server";

type RouteContext = {
  params: Promise<{
    path: string[];
  }>;
};

async function proxy(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  const { path = [] } = await context.params;
  if (!isAllowedFocusProofRequest(request.method, path)) {
    return NextResponse.json({ code: "forbidden_proxy_path", retryable: false }, { status: 403 });
  }
  const baseUrl = process.env.FOCUSPROOF_API_BASE_URL ?? "http://127.0.0.1:8000";
  const target = baseUrl.replace(/\/$/, "") + "/" + path.map(encodeURIComponent).join("/");
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.text();
  const headers = new Headers({
    "content-type": request.headers.get("content-type") ?? "application/json"
  });
  const authorization = getForwardableBearer(request.headers);
  if (authorization) headers.set("authorization", authorization);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), getProxyTimeoutMs(request.method, path));
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: controller.signal
    });
  } catch {
    return NextResponse.json({ code: "backend_unavailable", retryable: true }, { status: 503 });
  } finally {
    clearTimeout(timeout);
  }
  const text = await upstream.text();
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
