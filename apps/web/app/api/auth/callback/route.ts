import {NextResponse} from "next/server";

import {exchangeCode, readClaims} from "@/lib/oidc";
import {OAUTH_STATE_COOKIE, SESSION_COOKIE, open, sameState, seal} from "@/lib/session";

type Pending = {state: string; verifier: string; nonce: string; next: string};

/**
 * Finish a sign-in.
 *
 * Everything here is refused rather than repaired. A callback with no pending
 * cookie, a state that does not match, or an ID token minted for a different
 * nonce is not a login that went slightly wrong -- it is somebody else's
 * redirect arriving in this browser.
 */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const failure = (code: string) =>
    NextResponse.redirect(new URL(`/login?error=${code}`, url.origin));

  if (url.searchParams.get("error")) return failure("provider_refused");
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  if (!code || !state) return failure("callback_incomplete");

  const cookie = request.headers
    .get("cookie")
    ?.split("; ")
    .find((entry) => entry.startsWith(`${OAUTH_STATE_COOKIE}=`))
    ?.slice(OAUTH_STATE_COOKIE.length + 1);
  const pending = open<Pending>(cookie);
  if (!pending || !sameState(pending.state, state)) return failure("state_mismatch");

  let tokens;
  try {
    tokens = await exchangeCode(code, pending.verifier);
  } catch {
    return failure("code_exchange_failed");
  }
  const claims = readClaims(tokens.id_token);
  if (claims.nonce !== undefined && claims.nonce !== pending.nonce) {
    return failure("nonce_mismatch");
  }

  const response = NextResponse.redirect(new URL(pending.next, url.origin));
  response.cookies.set(
    SESSION_COOKIE,
    seal({
      // The API verifies the ID token against the provider's key set, so that
      // is what it is sent. An access token here is opaque to it.
      accessToken: tokens.id_token ?? tokens.access_token,
      refreshToken: tokens.refresh_token,
      expiresAt: Math.floor(Date.now() / 1000) + tokens.expires_in,
      email: claims.email,
      name: claims.name,
    }),
    {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: 60 * 60 * 12,
    },
  );
  response.cookies.set(OAUTH_STATE_COOKIE, "", {path: "/", maxAge: 0});
  return response;
}
