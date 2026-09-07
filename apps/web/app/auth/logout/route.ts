import {NextResponse} from "next/server";

import {appOrigin} from "@/lib/app-origin.mjs";
import {SESSION_COOKIE} from "@/lib/session";

export async function POST(request: Request) {
  const response = NextResponse.redirect(new URL("/", appOrigin(request)), {status: 303});
  response.cookies.set(SESSION_COOKIE, "", {path: "/", maxAge: 0});
  return response;
}
