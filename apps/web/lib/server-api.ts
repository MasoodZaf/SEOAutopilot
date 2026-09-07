import "server-only";

import {cookies} from "next/headers";

import {isConfigured, refresh} from "@/lib/oidc";
import {SESSION_COOKIE, Session, isUsable, open, seal} from "@/lib/session";

const apiBaseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";
const pilotToken = process.env.LOCAL_PILOT_AUTH_TOKEN;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
  ) {
    super(code);
  }
}

/**
 * The credential this request should carry to the API.
 *
 * The web tier used to inject one shared operator token for every visitor,
 * which made "anyone who reaches /pilot" and "an owner of every site" the same
 * sentence. It now forwards the signed-in person's own token, so the API
 * resolves their membership and their role.
 *
 * The pilot token survives as a development fallback and only when no provider
 * is configured, so a local stack still runs without an identity provider. It
 * cannot be reached on a deployment that has one.
 */
async function credential(): Promise<string> {
  if (isConfigured()) {
    const store = await cookies();
    const session = open<Session>(store.get(SESSION_COOKIE)?.value);
    if (session === null) throw new ApiError(401, "session-expired");
    if (isUsable(session)) return session.accessToken;
    if (session.refreshToken) {
      try {
        const renewed = await refresh(session.refreshToken);
        const next: Session = {
          ...session,
          accessToken: renewed.id_token ?? renewed.access_token,
          refreshToken: renewed.refresh_token ?? session.refreshToken,
          expiresAt: Math.floor(Date.now() / 1000) + renewed.expires_in,
        };
        // A Server Component cannot set a cookie, so the renewed session is used
        // for this request and re-minted on the next one. Getting that wrong
        // throws at runtime in Next; the cost here is one refresh per request
        // for a session in its last minute, which is bounded and brief.
        store.set?.(SESSION_COOKIE, seal(next), {
          httpOnly: true,
          sameSite: "lax",
          secure: process.env.NODE_ENV === "production",
          path: "/",
          maxAge: 60 * 60 * 12,
        });
        return next.accessToken;
      } catch {
        throw new ApiError(401, "session-expired");
      }
    }
    throw new ApiError(401, "session-expired");
  }
  if (!pilotToken || pilotToken.length < 32) {
    throw new ApiError(503, "pilot-session-not-configured");
  }
  return pilotToken;
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await credential();
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let code = `api-error-${response.status}`;
    try {
      const body = (await response.json()) as {detail?: string};
      if (typeof body.detail === "string") code = body.detail;
    } catch {
      // Retain the bounded status-derived code.
    }
    throw new ApiError(response.status, code);
  }
  return (await response.json()) as T;
}

/** Who is signed in, for the header. Null when nobody is. */
export async function currentSession(): Promise<Session | null> {
  if (!isConfigured()) return null;
  const store = await cookies();
  return open<Session>(store.get(SESSION_COOKIE)?.value);
}
