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
        // A Server Component cannot set a cookie. `set` is *present* on the
        // store and throws when called there, so `?.` guarded nothing: the
        // throw was caught below and reported as `session-expired` — for a
        // refresh that had just succeeded. Every page render past the token's
        // first hour failed that way, while server actions kept working,
        // because those may set cookies.
        //
        // Persisting is an optimisation, so it fails on its own. Losing it
        // costs one refresh per render; treating it as a failed sign-in costs
        // the session.
        try {
          store.set(SESSION_COOKIE, seal(next), {
            httpOnly: true,
            sameSite: "lax",
            secure: process.env.NODE_ENV === "production",
            path: "/",
            maxAge: 60 * 60 * 12,
          });
        } catch {
          // Rendering, not acting. The token below is still the renewed one.
        }
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
  // A 204 has no body, and `response.json()` on an empty one throws
  // `SyntaxError`. That is not an ApiError, so every caller's catch reported it
  // as `unexpected-error`: removing a member and revoking an invitation both
  // succeeded on the API and told the operator they had failed. The same shape
  // of defect as the button that worked once and then silently did nothing --
  // the request landed, the report of it did not.
  if (response.status === 204 || response.headers.get("content-length") === "0") {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** Who is signed in, for the header. Null when nobody is. */
export async function currentSession(): Promise<Session | null> {
  if (!isConfigured()) return null;
  const store = await cookies();
  return open<Session>(store.get(SESSION_COOKIE)?.value);
}

/**
 * The workspaces this person is in, or an empty list when they are in none.
 *
 * `/v1/tenants` is one of two routes that authenticate without demanding a
 * membership, so this is the one question a brand-new signed-in user can ask
 * and get an answer to rather than a 403.
 */
export type TenantMembership = {
  tenant_id: string;
  slug: string;
  name: string;
  role: string;
};

export async function currentTenants(): Promise<TenantMembership[]> {
  const body = await apiJson<{data: TenantMembership[]}>("/v1/tenants");
  return body.data;
}

/**
 * Send somebody with no workspace to make one, instead of showing them a 403.
 *
 * Every tenant-scoped route answers `no_tenant_membership` to a person who has
 * signed in and been invited to nothing. That is the ordinary first minute of a
 * new account, not an error, and the only useful thing to do with it is offer
 * the page that fixes it. Anything else is rethrown untouched.
 */
export function isMissingTenant(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403 && error.code === "no_tenant_membership";
}
