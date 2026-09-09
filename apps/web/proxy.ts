import {NextResponse} from "next/server";
import type {NextRequest} from "next/server";

import {appOrigin} from "@/lib/app-origin.mjs";
import {SESSION_COOKIE} from "@/lib/cookie-names";

/**
 * Send an unauthenticated visitor to sign in rather than to a 401.
 *
 * This is a redirect, not authorization. It checks only that a session cookie
 * is present -- it cannot open the envelope, because this runs on the edge
 * runtime and there is no node:crypto there. That is also why the cookie name
 * comes from `lib/cookie-names`: importing `lib/session` would drag the crypto
 * in and fail on the first request rather than at build time.
 *
 * So a forged cookie gets past this and is then refused by the API, which
 * verifies the token properly. Nothing here is the security boundary; it exists
 * so that arriving at /pilot signed out is a login page instead of an error.
 *
 * The redirect target does not come from `request.url` for the same reason the
 * callback's no longer does: behind the proxy that is the container's hostname
 * and internal port. This code path had never run in production -- the guard
 * below returned early while no issuer was configured -- so it went live
 * already broken the moment sign-in was switched on. `app-origin.mjs` imports
 * nothing from node, so it is safe on the edge runtime; if the environment is
 * not visible there it reads the proxy's forwarding headers instead.
 */
export function proxy(request: NextRequest) {
  if (!process.env.OIDC_ISSUER_URL) return NextResponse.next();
  if (request.cookies.has(SESSION_COOKIE)) return NextResponse.next();
  const login = new URL("/login", appOrigin(request));
  login.searchParams.set("next", request.nextUrl.pathname + request.nextUrl.search);
  return NextResponse.redirect(login);
}

export const config = {
  // /onboarding belongs here too: it is where a signed-in person with no
  // workspace is sent, so it is reached by exactly the people who have just
  // authenticated -- and by anyone who types it. Without it, arriving signed
  // out asks the API a question with no credential and renders the 401 as a
  // crash instead of a login page.
  matcher: ["/pilot/:path*", "/settings/:path*", "/onboarding"],
};
