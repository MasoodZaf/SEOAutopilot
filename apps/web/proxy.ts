import {NextResponse} from "next/server";
import type {NextRequest} from "next/server";

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
 */
export function proxy(request: NextRequest) {
  if (!process.env.OIDC_ISSUER_URL) return NextResponse.next();
  if (request.cookies.has(SESSION_COOKIE)) return NextResponse.next();
  const login = new URL("/login", request.url);
  login.searchParams.set("next", request.nextUrl.pathname + request.nextUrl.search);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/pilot/:path*", "/settings/:path*"],
};
