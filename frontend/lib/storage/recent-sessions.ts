export type RecentSession = {
  sessionId: string;
  title: string;
  domain: string;
  visitedAt: string;
};

const key = "focusproof.recentSession";

export function saveRecentSession(session: RecentSession): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(key, JSON.stringify(session));
}

export function loadRecentSession(): RecentSession | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as RecentSession;
    if (!value.sessionId || !value.title || !value.domain || !value.visitedAt) return null;
    return value;
  } catch {
    return null;
  }
}
