// Environment parsing, kept pure so it can be unit-tested.

/**
 * Mocks are on unless the flag is explicitly off. docker-compose and render.yaml use
 * "1"/"0", the original scaffold used "true"/"false"; both are accepted.
 */
export function parseUseMocks(raw: string | undefined): boolean {
  if (raw === undefined) return true;
  return !["0", "false", "no", "off"].includes(raw.trim().toLowerCase());
}

/**
 * docker-compose and render.yaml set VITE_API_BASE_URL to the bare origin, while the
 * routes live under /api/v1. Accept either form.
 */
export function apiBase(raw: string | undefined): string {
  const base = (raw ?? "").trim() || "http://localhost:8000";
  const trimmed = base.replace(/\/+$/, "");
  return trimmed.endsWith("/api/v1") ? trimmed : `${trimmed}/api/v1`;
}
