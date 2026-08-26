const defaultTimeoutMs = 15_000;
const reviewTimeoutMs = 75_000;
const mediaTimeoutMs = 45_000;

export function getProxyTimeoutMs(method: string, path: string[]): number {
  const isReview =
    method.toUpperCase() === "POST" &&
    path.length === 3 &&
    path[0] === "sessions" &&
    path[2] === "review";

  const isMedia = method.toUpperCase() === "POST" && path.length === 4 && path[0] === "sessions" && path[2] === "evidence" && path[3] === "image";
  return isReview ? reviewTimeoutMs : isMedia ? mediaTimeoutMs : defaultTimeoutMs;
}
