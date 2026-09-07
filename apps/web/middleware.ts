import {NextResponse} from "next/server";
import type {NextRequest} from "next/server";

import {SESSION_COOKIE} from "@/lib/session";

/**
 * Send an unauthenticated visitor to sign in rather than to a 401.
 *
 * This is a redirect, not authorization. It checks only that a session cookie
 * is present -- it cannot open the envelope, because the middleware runtime has
 * no node:crypto -- so a forged cookie gets past it and is then refused by the
 * API, which verifies the token properly. Nothing here is the security
 * boundary; it exists so that arriving at /pilot signed out is a login page
 * instead of an error.
 */
export function middleware(request: NextRequest) {
  if (!process.env.OIDC_ISSUER_URL) return NextResponse.next();
  if (request.cookies.has(SESSION_COOKIE)) return NextResponse.next();
  const login = new URL("/login", request.url);
  login.searchParams.set("next", request.nextUrl.pathname + request.nextUrl.search);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/pilot/:path*", "/settings/:path*"],
};
