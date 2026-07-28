"use client";

import {
  InMemoryWebStorage,
  UserManager,
  WebStorageStateStore,
  type UserManagerSettings
} from "oidc-client-ts";

const transactionPrefix = "focusproof.oidc.";

export type PublicOidcConfig = {
  authority: string;
  clientId: string;
  audience: string;
  redirectUri: string;
};

export type BrowserOidcUser = {
  access_token: string;
  expired?: boolean;
  state?: unknown;
};

export type BrowserOidcInitialization = {
  authenticated: boolean;
  returnTo?: string;
};

export type BrowserOidcManager = {
  events: {
    addAccessTokenExpired(listener: () => void): void | (() => void);
    removeAccessTokenExpired(listener: () => void): void;
  };
  getUser(): Promise<BrowserOidcUser | null>;
  signinRedirectCallback(url?: string): Promise<BrowserOidcUser>;
  signinRedirect(args?: { state?: { returnTo: string } }): Promise<void>;
  removeUser(): Promise<void>;
  clearStaleState(): Promise<void>;
};

function requiredExact(value: string | undefined, label: string): string {
  if (!value || value !== value.trim()) throw new Error(`Invalid public OIDC ${label}`);
  return value;
}

export function readPublicOidcConfig(): PublicOidcConfig | null {
  const values = [
    process.env.NEXT_PUBLIC_OIDC_ISSUER,
    process.env.NEXT_PUBLIC_OIDC_CLIENT_ID,
    process.env.NEXT_PUBLIC_OIDC_AUDIENCE,
    process.env.NEXT_PUBLIC_OIDC_REDIRECT_URI
  ];
  if (values.every((value) => value === undefined || value === "")) return null;

  const authority = requiredExact(values[0], "issuer");
  const clientId = requiredExact(values[1], "client id");
  const audience = requiredExact(values[2], "audience");
  const redirectUri = requiredExact(values[3], "redirect URI");
  const authorityUrl = new URL(authority);
  const redirectUrl = new URL(redirectUri);
  if (authorityUrl.protocol !== "https:") throw new Error("Invalid public OIDC issuer");
  if (redirectUrl.origin !== window.location.origin) {
    throw new Error("OIDC redirect URI must be same-origin");
  }
  return { authority, clientId, audience, redirectUri };
}

export function buildUserManagerSettings(config: PublicOidcConfig): UserManagerSettings {
  const userMemory = new InMemoryWebStorage();
  return {
    authority: config.authority,
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    response_type: "code",
    scope: "openid",
    disablePKCE: false,
    automaticSilentRenew: false,
    monitorSession: false,
    loadUserInfo: false,
    revokeTokensOnSignout: false,
    includeIdTokenInSilentSignout: false,
    userStore: new WebStorageStateStore({ prefix: "focusproof.oidc.user.", store: userMemory }),
    stateStore: new WebStorageStateStore({ prefix: transactionPrefix, store: window.sessionStorage }),
    extraQueryParams: { audience: config.audience }
  };
}

function clearTransactionState(): void {
  for (let index = window.sessionStorage.length - 1; index >= 0; index -= 1) {
    const key = window.sessionStorage.key(index);
    if (key?.startsWith(transactionPrefix)) window.sessionStorage.removeItem(key);
  }
}

function cleanCallbackUrl(): void {
  window.history.replaceState({}, document.title, window.location.pathname + window.location.hash);
}

async function clearManagerState(manager: BrowserOidcManager): Promise<boolean> {
  const results = await Promise.allSettled([manager.removeUser(), manager.clearStaleState()]);
  clearTransactionState();
  return results.every((result) => result.status === "fulfilled");
}

function isSigninCallback(url: URL): boolean {
  return url.searchParams.has("state") &&
    (url.searchParams.has("code") || url.searchParams.has("error"));
}

function safeInternalReturnTo(value: unknown): string | undefined {
  if (
    typeof value !== "string" ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    /%(?![0-9a-fA-F]{2})/.test(value)
  ) {
    return undefined;
  }
  try {
    const target = new URL(value, window.location.origin);
    if (target.origin !== window.location.origin || !target.pathname.startsWith("/")) {
      return undefined;
    }
    return target.pathname + target.search + target.hash;
  } catch {
    return undefined;
  }
}

function callbackReturnTo(state: unknown): string | undefined {
  if (!state || typeof state !== "object" || Array.isArray(state)) return undefined;
  return safeInternalReturnTo((state as { returnTo?: unknown }).returnTo);
}

function mergeRequestHeaders(input: RequestInfo | URL, init?: RequestInit): Headers {
  const headers = new Headers(input instanceof Request ? input.headers : undefined);
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value));
  headers.delete("authorization");
  return headers;
}

export class BrowserOidcIdentity {
  private accessToken: string | null = null;
  private readonly expiredListener = (): void => {
    this.accessToken = null;
    clearTransactionState();
    void this.manager.removeUser().catch(() => undefined);
  };

  constructor(private readonly manager: BrowserOidcManager) {
    manager.events.addAccessTokenExpired(this.expiredListener);
  }

  async initialize(currentUrl: string): Promise<BrowserOidcInitialization> {
    const url = new URL(currentUrl, window.location.origin);
    if (isSigninCallback(url)) {
      try {
        const user = await this.manager.signinRedirectCallback(url.href);
        this.accessToken = user.expired ? null : user.access_token;
        const returnTo = this.accessToken ? callbackReturnTo(user.state) : undefined;
        clearTransactionState();
        cleanCallbackUrl();
        return returnTo
          ? { authenticated: true, returnTo }
          : { authenticated: this.accessToken !== null };
      } catch {
        this.accessToken = null;
        await clearManagerState(this.manager);
        cleanCallbackUrl();
        throw new Error("OIDC callback rejected");
      }
    }

    const user = await this.manager.getUser();
    this.accessToken = user && !user.expired ? user.access_token : null;
    return { authenticated: this.accessToken !== null };
  }

  async signIn(): Promise<void> {
    const returnTo = window.location.pathname + window.location.search + window.location.hash;
    await this.manager.signinRedirect({ state: { returnTo } });
  }

  async logout(): Promise<void> {
    this.accessToken = null;
    const cleared = await clearManagerState(this.manager);
    cleanCallbackUrl();
    if (!cleared) throw new Error("OIDC logout failed");
  }

  async fetch(
    input: RequestInfo | URL,
    init?: RequestInit,
    fetchImpl: typeof fetch = globalThis.fetch
  ): Promise<Response> {
    const inputUrl = input instanceof Request ? input.url : input.toString();
    const url = new URL(inputUrl, window.location.origin);
    const headers = mergeRequestHeaders(input, init);
    const isFocusProofBff =
      url.origin === window.location.origin &&
      (url.pathname === "/api/focusproof" || url.pathname.startsWith("/api/focusproof/"));
    if (isFocusProofBff && this.accessToken) {
      headers.set("authorization", `Bearer ${this.accessToken}`);
    }
    return fetchImpl(input, { ...init, headers });
  }
}

let browserIdentity: BrowserOidcIdentity | null | undefined;

function getBrowserIdentity(): BrowserOidcIdentity | null {
  if (browserIdentity !== undefined) return browserIdentity;
  const config = readPublicOidcConfig();
  browserIdentity = config
    ? new BrowserOidcIdentity(new UserManager(buildUserManagerSettings(config)))
    : null;
  return browserIdentity;
}

export async function initializeBrowserOidc(): Promise<BrowserOidcInitialization> {
  const identity = getBrowserIdentity();
  if (!identity) return { authenticated: true };
  const initialization = await identity.initialize(window.location.href);
  if (initialization.authenticated) return initialization;
  await identity.signIn();
  return initialization;
}

export async function logoutBrowserOidc(): Promise<void> {
  await getBrowserIdentity()?.logout();
}

export async function fetchWithOidcAccessToken(
  input: RequestInfo | URL,
  init?: RequestInit
): Promise<Response> {
  const identity = getBrowserIdentity();
  if (identity) return identity.fetch(input, init);
  const headers = mergeRequestHeaders(input, init);
  return globalThis.fetch(input, { ...init, headers });
}
