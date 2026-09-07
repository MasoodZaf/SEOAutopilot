import {NextResponse} from "next/server";

import {SESSION_COOKIE} from "@/lib/session";

export async function POST(request: Request) {
  const response = NextResponse.redirect(new URL("/", new URL(request.url).origin), {status: 303});
  response.cookies.set(SESSION_COOKIE, "", {path: "/", maxAge: 0});
  return response;
}
