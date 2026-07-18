import { NextRequest } from "next/server";
import { OidcClient } from "oidc-client-ts";
import * as React from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "@/app/api/focusproof/[...path]/route";
import { Providers } from "@/app/providers";
import * as browserAuth from "@/lib/auth/browser";
import {
  BrowserOidcIdentity,
  buildUserManagerSettings,
  type BrowserOidcManager,
  type BrowserOidcUser
} from "@/lib/auth/browser";
import { getForwardableBearer, isForwardableBearer } from "@/lib/auth/server";

const tokenSentinel = "token-sentinel.header.payload";
const context = (path: string[]) => ({ params: Promise.resolve({ path }) });

function jsonResponse(body: object, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers }
  });
}

class FakeOidcManager implements BrowserOidcManager {
  callbackUser: BrowserOidcUser | null = null;
  currentUser: BrowserOidcUser | null = null;
  callbackError: Error | null = null;
  removeError: Error | null = null;
  clearStateError: Error | null = null;
  redirectCount = 0;
  removeCount = 0;
  clearStaleStateCount = 0;
  callbackUrls: string[] = [];
  private expiredListeners = new Set<() => void>();

  events = {
    addAccessTokenExpired: (listener: () => void) => {
      this.expiredListeners.add(listener);
    },
    removeAccessTokenExpired: (listener: () => void) => {
      this.expiredListeners.delete(listener);
    }
  };

  async getUser(): Promise<BrowserOidcUser | null> {
    return this.currentUser;
  }

  async signinRedirectCallback(url?: string): Promise<BrowserOidcUser> {
    this.callbackUrls.push(url ?? "");
    if (this.callbackError) throw this.callbackError;
    if (!this.callbackUser) throw new Error("missing callback user");
    this.currentUser = this.callbackUser;
    return this.callbackUser;
  }

  async signinRedirect(): Promise<void> {
    this.redirectCount += 1;
  }

  async removeUser(): Promise<void> {
    this.removeCount += 1;
    if (this.removeError) throw this.removeError;
    this.currentUser = null;
  }

  async clearStaleState(): Promise<void> {
    this.clearStaleStateCount += 1;
    if (this.clearStateError) throw this.clearStateError;
  }

  expire(): void {
    for (const listener of this.expiredListeners) listener();
  }
}

describe("BFF bearer forwarding boundary", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("forwards exactly one syntactically valid Bearer value unchanged", async () => {
    const upstreamFetch = vi.fn().mockResolvedValue(jsonResponse({ sessionId: "sess_1" }));
    vi.stubGlobal("fetch", upstreamFetch);
    const authorization = `Bearer ${tokenSentinel}`;

    await POST(
      new NextRequest("http://localhost/api/focusproof/sessions", {
        method: "POST",
        headers: { Authorization: authorization, "content-type": "application/json" },
        body: "{}"
      }),
      context(["sessions"])
    );

    const forwarded = new Headers(upstreamFetch.mock.calls[0][1].headers);
    expect(forwarded.get("authorization")).toBe(authorization);
  });

  it.each([
    ["missing", undefined],
    ["empty token", "Bearer "],
    ["wrong scheme", "Basic abc"],
    ["multiple schemes", "Bearer abc Basic def"],
    ["combined", "Bearer abc, Bearer def"],
    ["extra whitespace", "Bearer  abc"],
    ["tab separator", "Bearer\tabc"],
    ["embedded CRLF", "Bearer abc\r\nX-User-Id: attacker"]
  ])("does not forward %s authorization", (_label, authorization) => {
    const headers = new Headers();
    if (authorization !== undefined && !authorization.includes("\r")) {
      headers.set("Authorization", authorization);
    }
    if (authorization?.includes("\r")) {
      expect(isForwardableBearer(authorization)).toBe(false);
    } else {
      expect(getForwardableBearer(headers)).toBeNull();
    }
  });

  it("rejects duplicate headers after Fetch combines them", () => {
    const headers = new Headers();
    headers.append("Authorization", "Bearer first");
    headers.append("Authorization", "Bearer second");
    expect(getForwardableBearer(headers)).toBeNull();
  });

  it("does not derive identity from body, query, cookie, or proxy headers", async () => {
    const upstreamFetch = vi.fn().mockResolvedValue(
      jsonResponse(
        { code: "invalid_token", retryable: false },
        401,
        { "WWW-Authenticate": "Bearer" }
      )
    );
    vi.stubGlobal("fetch", upstreamFetch);

    const response = await POST(
      new NextRequest(
        "http://localhost/api/focusproof/sessions?access_token=query-token&owner=attacker",
        {
          method: "POST",
          headers: {
            cookie: "access_token=cookie-token",
            "content-type": "application/json",
            "x-forwarded-user": "attacker",
            "x-user-id": "attacker"
          },
          body: JSON.stringify({ owner: "attacker", access_token: "body-token" })
        }
      ),
      context(["sessions"])
    );

    const [target, init] = upstreamFetch.mock.calls[0];
    expect(String(target)).not.toContain("access_token");
    const forwarded = new Headers(init.headers);
    expect(forwarded.get("authorization")).toBeNull();
    expect(forwarded.get("cookie")).toBeNull();
    expect(forwarded.get("x-forwarded-user")).toBeNull();
    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({ code: "invalid_token", retryable: false });
    expect(response.headers.get("www-authenticate")).toBe("Bearer");
  });

  it.each([
    [401, { code: "invalid_token", retryable: false }],
    [403, { code: "forbidden", retryable: false }],
    [404, { code: "not_found", retryable: false }]
  ])("preserves backend %s safe JSON", async (status, body) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(body, status, status === 401 ? { "WWW-Authenticate": "Bearer" } : {})
      )
    );
    const response = await GET(
      new NextRequest("http://localhost/api/focusproof/sessions/sess_1"),
      context(["sessions", "sess_1"])
    );
    expect(response.status).toBe(status);
    await expect(response.json()).resolves.toEqual(body);
    expect(response.headers.get("www-authenticate")).toBe(status === 401 ? "Bearer" : null);
  });

  it("blocks debug paths before reading or forwarding credentials", async () => {
    const upstreamFetch = vi.fn();
    vi.stubGlobal("fetch", upstreamFetch);
    const response = await GET(
      new NextRequest("http://localhost/api/focusproof/debug/openhands/llm-status", {
        headers: { Authorization: `Bearer ${tokenSentinel}` }
      }),
      context(["debug", "openhands", "llm-status"])
    );
    expect(response.status).toBe(403);
    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("never logs a forwarded token", async () => {
    const logSpies = ["log", "info", "warn", "error"].map((name) =>
      vi.spyOn(console, name as "log").mockImplementation(() => undefined)
    );
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ status: "ok" })));
    await GET(
      new NextRequest("http://localhost/api/focusproof/health", {
        headers: { Authorization: `Bearer ${tokenSentinel}` }
      }),
      context(["health"])
    );
    expect(logSpies.flatMap((spy) => spy.mock.calls).join(" ")).not.toContain(tokenSentinel);
  });
});

describe("browser OIDC identity boundary", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    document.cookie = "focusproof_test=; Max-Age=0; path=/";
    window.history.replaceState({}, "", "/learning");
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not mount business children before browser identity initialization completes", async () => {
    vi.stubGlobal("React", React);
    let finishInitialization: (ready: boolean) => void = () => undefined;
    const initialization = new Promise<boolean>((resolve) => {
      finishInitialization = resolve;
    });
    vi.spyOn(browserAuth, "initializeBrowserOidc").mockReturnValue(initialization);

    render(React.createElement(Providers, null, React.createElement("div", null, "protected workspace")));
    expect(screen.queryByText("protected workspace")).not.toBeInTheDocument();

    finishInitialization(true);
    expect(await screen.findByText("protected workspace")).toBeInTheDocument();
  });

  it("configures provider-neutral code flow, PKCE, no renewal, and split stores", () => {
    const settings = buildUserManagerSettings({
      authority: "https://issuer.example.test/tenant",
      clientId: "focusproof-spa",
      audience: "focusproof-api",
      redirectUri: "http://localhost/learning"
    });

    expect(settings).toMatchObject({
      authority: "https://issuer.example.test/tenant",
      client_id: "focusproof-spa",
      redirect_uri: "http://localhost/learning",
      response_type: "code",
      automaticSilentRenew: false,
      loadUserInfo: false,
      extraQueryParams: { audience: "focusproof-api" }
    });
    expect(settings.userStore).not.toBe(settings.stateStore);
  });

  it("accepts a validated callback into memory and cleans callback URL and transaction state", async () => {
    const manager = new FakeOidcManager();
    manager.callbackUser = { access_token: tokenSentinel, expired: false };
    sessionStorage.setItem("focusproof.oidc.transaction", JSON.stringify({ state: "expected" }));
    window.history.replaceState({}, "", "/learning?code=code-value&state=expected");
    const identity = new BrowserOidcIdentity(manager);

    await identity.initialize(window.location.href);

    expect(manager.callbackUrls).toEqual([
      `${window.location.origin}/learning?code=code-value&state=expected`
    ]);
    expect(window.location.href).toBe(`${window.location.origin}/learning`);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(document.cookie).not.toContain(tokenSentinel);
    expect(window.location.href).not.toContain(tokenSentinel);
  });

  it("clears callback state and tokens when state or nonce validation fails", async () => {
    const manager = new FakeOidcManager();
    manager.callbackError = new Error("state or nonce mismatch");
    sessionStorage.setItem("focusproof.oidc.transaction", JSON.stringify({ state: "expected" }));
    window.history.replaceState({}, "", "/learning?code=code-value&state=attacker");
    const identity = new BrowserOidcIdentity(manager);

    await expect(identity.initialize(window.location.href)).rejects.toThrow("OIDC callback rejected");
    expect(window.location.href).toBe(`${window.location.origin}/learning`);
    expect(sessionStorage.length).toBe(0);
    expect(manager.removeCount).toBe(1);
  });

  it("cleans callback URL and transaction state even when SDK cleanup rejects", async () => {
    const manager = new FakeOidcManager();
    manager.callbackError = new Error("state mismatch");
    manager.removeError = new Error("remove failed");
    manager.clearStateError = new Error("clear failed");
    sessionStorage.setItem("focusproof.oidc.transaction", "no-token-here");
    window.history.replaceState({}, "", "/learning?error=access_denied&state=attacker");
    const identity = new BrowserOidcIdentity(manager);

    await expect(identity.initialize(window.location.href)).rejects.toThrow("OIDC callback rejected");
    expect(window.location.href).toBe(`${window.location.origin}/learning`);
    expect(sessionStorage.length).toBe(0);
  });

  it("uses only its in-memory access token for same-origin FocusProof requests", async () => {
    const manager = new FakeOidcManager();
    manager.currentUser = { access_token: tokenSentinel, expired: false };
    const identity = new BrowserOidcIdentity(manager);
    await identity.initialize("http://localhost/learning");
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));

    await identity.fetch(
      "/api/focusproof/sessions",
      { headers: { Authorization: "Bearer caller-supplied", "x-request-id": "request-1" } },
      fetchImpl
    );

    const headers = new Headers(fetchImpl.mock.calls[0][1].headers);
    expect(headers.get("authorization")).toBe(`Bearer ${tokenSentinel}`);
    expect(headers.get("x-request-id")).toBe("request-1");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it.each([
    "https://api.example.test/api/focusproof/sessions",
    "/api/not-focusproof",
    "https://issuer.example.test/authorize"
  ])("never attaches a token outside the same-origin FocusProof BFF: %s", async (target) => {
    const manager = new FakeOidcManager();
    manager.currentUser = { access_token: tokenSentinel, expired: false };
    const identity = new BrowserOidcIdentity(manager);
    await identity.initialize("http://localhost/learning");
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));

    await identity.fetch(target, { headers: { Authorization: "Bearer caller-supplied" } }, fetchImpl);

    const headers = new Headers(fetchImpl.mock.calls[0][1].headers);
    expect(headers.get("authorization")).toBeNull();
  });

  it("clears memory and transaction state on expiry and logout", async () => {
    const manager = new FakeOidcManager();
    manager.currentUser = { access_token: tokenSentinel, expired: false };
    const identity = new BrowserOidcIdentity(manager);
    await identity.initialize("http://localhost/learning");
    sessionStorage.setItem("focusproof.oidc.transaction", "no-token-here");
    manager.expire();

    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    await identity.fetch("/api/focusproof/health", undefined, fetchImpl);
    expect(new Headers(fetchImpl.mock.calls[0][1].headers).get("authorization")).toBeNull();

    manager.currentUser = { access_token: tokenSentinel, expired: false };
    await identity.initialize("http://localhost/learning");
    await identity.logout();
    expect(manager.removeCount).toBeGreaterThan(0);
    expect(manager.clearStaleStateCount).toBeGreaterThan(0);
    expect(sessionStorage.length).toBe(0);
    expect(JSON.stringify(localStorage)).not.toContain(tokenSentinel);
  });

  it("clears local logout state even when SDK cleanup rejects", async () => {
    const manager = new FakeOidcManager();
    manager.currentUser = { access_token: tokenSentinel, expired: false };
    const identity = new BrowserOidcIdentity(manager);
    await identity.initialize("http://localhost/learning");
    manager.removeError = new Error("remove failed");
    manager.clearStateError = new Error("clear failed");
    sessionStorage.setItem("focusproof.oidc.transaction", "no-token-here");

    await expect(identity.logout()).rejects.toThrow("OIDC logout failed");
    expect(sessionStorage.length).toBe(0);
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    await identity.fetch("/api/focusproof/health", undefined, fetchImpl);
    expect(new Headers(fetchImpl.mock.calls[0][1].headers).get("authorization")).toBeNull();
  });

  it("uses the official OIDC client to generate PKCE and reject mismatched state and nonce", async () => {
    const authority = "https://issuer.example.test/tenant";
    const settings = buildUserManagerSettings({
      authority,
      clientId: "focusproof-spa",
      audience: "focusproof-api",
      redirectUri: `${window.location.origin}/learning`
    });
    const client = new OidcClient({
      ...settings,
      metadata: {
        issuer: authority,
        authorization_endpoint: `${authority}/authorize`,
        token_endpoint: `${authority}/token`
      }
    });
    const request = await client.createSigninRequest({ nonce: "expected-nonce" });
    const authorizationUrl = new URL(request.url);
    const state = authorizationUrl.searchParams.get("state");

    expect(authorizationUrl.searchParams.get("response_type")).toBe("code");
    expect(authorizationUrl.searchParams.get("code_challenge_method")).toBe("S256");
    expect(authorizationUrl.searchParams.get("code_challenge")).toBeTruthy();
    expect(authorizationUrl.searchParams.get("nonce")).toBe("expected-nonce");
    await expect(
      client.processSigninResponse(`${window.location.origin}/learning?code=code&state=wrong`)
    ).rejects.toThrow(/matching state/i);

    const now = Math.floor(Date.now() / 1000);
    const encode = (value: object) => Buffer.from(JSON.stringify(value)).toString("base64url");
    const idToken = `${encode({ alg: "none", typ: "JWT" })}.${encode({
      iss: authority,
      aud: "focusproof-spa",
      sub: "subject-a",
      iat: now,
      exp: now + 300,
      nonce: "wrong-nonce"
    })}.signature`;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          access_token: tokenSentinel,
          token_type: "Bearer",
          expires_in: 300,
          id_token: idToken,
          scope: "openid"
        })
      )
    );

    await expect(
      client.processSigninResponse(
        `${window.location.origin}/learning?code=code&state=${encodeURIComponent(state ?? "")}`
      )
    ).rejects.toThrow(/nonce/i);
    expect(JSON.stringify(sessionStorage)).not.toContain(tokenSentinel);
  });
});
