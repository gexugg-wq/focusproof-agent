const defaultTimeoutMs = 15_000;
const reviewTimeoutMs = 75_000;

export function getProxyTimeoutMs(method: string, path: string[]): number {
  const isReview =
    method.toUpperCase() === "POST" &&
    path.length === 3 &&
    path[0] === "sessions" &&
    path[2] === "review";

  return isReview ? reviewTimeoutMs : defaultTimeoutMs;
}
