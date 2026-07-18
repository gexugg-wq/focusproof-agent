const bearerPattern = /^Bearer [A-Za-z0-9._~+/-]+={0,}$/;

export function isForwardableBearer(value: string | null | undefined): value is string {
  return typeof value === "string" && bearerPattern.test(value);
}

export function getForwardableBearer(headers: Headers): string | null {
  const authorization = headers.get("authorization");
  return isForwardableBearer(authorization) ? authorization : null;
}
