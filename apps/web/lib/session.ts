import "server-only";

import {createHash} from "node:crypto";

import {open as openEnvelope, sameState as compareState, seal as sealEnvelope} from "@/lib/session-envelope.mjs";

/**
 * The browser's half of a signed-in session.
 *
 * The tokens never reach the browser as readable values. They sit in an
 * AES-256-GCM envelope in an httpOnly cookie, so a cross-site script cannot read
 * them, and the authentication tag means a tampered cookie is rejected rather
 * than half-parsed. The key is derived from WEB_SESSION_SECRET, a server-only
 * value: this cookie is a session, not a credential the API trusts on its own.
 */
export type Session = {
  accessToken: string;
  refreshToken?: string;
  /** Seconds since the epoch. */
  expiresAt: number;
  email: string;
  name: string;
};

export {OAUTH_STATE_COOKIE, SESSION_COOKIE} from "@/lib/cookie-names";

function key(): Buffer {
  const secret = process.env.WEB_SESSION_SECRET;
  if (!secret || secret.length < 32) {
    throw new Error("web-session-secret-not-configured");
  }
  return createHash("sha256").update(secret).digest();
}

export function seal(value: unknown): string {
  return sealEnvelope(key(), value);
}

export function open<T>(envelope: string | undefined): T | null {
  return (openEnvelope(key(), envelope) as T | null) ?? null;
}

export function sameState(a: string, b: string): boolean {
  return compareState(a, b);
}

/**
 * A token that expires in the next minute is treated as expired.
 *
 * Refreshing slightly early costs one extra round trip; discovering expiry
 * mid-request costs the user their action.
 */
export function isUsable(session: Session | null): boolean {
  return session !== null && session.expiresAt - 60 > Math.floor(Date.now() / 1000);
}
