import {NextResponse} from "next/server";
import {randomBytes} from "node:crypto";

import {authorizationUrl, isConfigured, pkce} from "@/lib/oidc";
import {OAUTH_STATE_COOKIE, seal} from "@/lib/session";

/**
 * Begin a sign-in.
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
  const requested = new URL(request.url).searchParams.get("next") ?? "/pilot";
  // Only same-site paths. An absolute URL here would make this an open
  // redirector that launders a phishing destination through a trusted host.
  const next = requested.startsWith("/") && !requested.startsWith("//") ? requested : "/pilot";

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
