import { NextRequest, NextResponse } from "next/server";
import { isAllowedFocusProofRequest } from "@/lib/api/errors";

type RouteContext = {
  params: {
    path: string[];
  };
};

async function proxy(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  const path = context.params.path ?? [];
  if (!isAllowedFocusProofRequest(request.method, path)) {
    return NextResponse.json({ code: "forbidden_proxy_path", retryable: false }, { status: 403 });
  }
  const baseUrl = process.env.FOCUSPROOF_API_BASE_URL ?? "http://127.0.0.1:8000";
  const target = baseUrl.replace(/\/$/, "") + "/" + path.map(encodeURIComponent).join("/");
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.text();
  const upstream = await fetch(target, {
    method: request.method,
    headers: { "content-type": request.headers.get("content-type") ?? "application/json" },
    body,
    cache: "no-store"
  });
  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" }
  });
}

export const GET = proxy;
export const POST = proxy;
