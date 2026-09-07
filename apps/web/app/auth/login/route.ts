import {NextResponse} from "next/server";
import {randomBytes} from "node:crypto";

import {appOrigin} from "@/lib/app-origin.mjs";
import {authorizationUrl, isConfigured, pkce} from "@/lib/oidc";
import {safeNext} from "@/lib/safe-next.mjs";
import {OAUTH_STATE_COOKIE, seal} from "@/lib/session";

/**
 * Begin a sign-in.
 *
 * Served from `/auth/*`, not `/api/auth/*`. The reverse proxy sends everything
 * under `/api/` to the API service, so these route handlers lived at a path the
 * public origin never delivered to the web tier -- `/api/auth/callback` answered
 * `{"detail":"Not Found"}` from FastAPI, which would have made the whole flow
 * dead on arrival the moment a provider was configured.
 *
 * The state, the PKCE verifier and the nonce are sealed into one short-lived
 * cookie rather than kept server-side, so the flow survives a restart and needs
 * no shared store. All three are checked on the way back: state proves the
 * redirect belongs to this browser, the verifier proves it belongs to this
 * request, and the nonce proves the ID token was minted for it.
 */
export async function GET(request: Request) {
  if (!isConfigured()) {
    return NextResponse.json({detail: "authentication_not_configured"}, {status: 503});
  }
  const url = new URL(request.url);
  // Resolved against the same parser that will perform the redirect, not
  // pattern-matched. A string test passes `/\evil.com` and `/<tab>/evil.com`,
  // both of which the URL parser then resolves to another origin -- which would
  // make this an open redirector laundering a phishing destination through a
  // trusted host, after a genuine sign-in the victim just watched succeed.
  const next = safeNext(url.searchParams.get("next"), appOrigin(request));

  const state = randomBytes(24).toString("base64url");
  const nonce = randomBytes(24).toString("base64url");
  const {verifier, challenge} = pkce();

  const response = NextResponse.redirect(await authorizationUrl(state, challenge, nonce));
  response.cookies.set(OAUTH_STATE_COOKIE, seal({state, verifier, nonce, next}), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 600,
  });
  return response;
}
