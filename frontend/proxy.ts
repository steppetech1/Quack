import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * The home page `/` is the app itself: without a session the first API call answers 401 and the client sends the
 * visitor to the log-in screen, so the landing (`/landing`) was never seen. A visitor with no `quack_session` cookie is
 * sent to the landing instead; its "Log in" / "Get started" buttons lead to `/login` and `/signup`.
 *
 * Only a check that the cookie is there, not that it is valid: an expired cookie still opens the app, the API answers
 * 401 and the client goes to `/login` as before. Temporary redirect (307): the same address is the app after sign-in.
 * Mocks (`NEXT_PUBLIC_QUACK_API` is not `1`) have no sign-in, so `/` stays the app there. So does `npm run dev:stub`:
 * the stub never asks for a login and sets no cookie, `dev/run.mjs` turns the redirect off with `QUACK_GUEST_LANDING=0`.
 */
export function proxy(request: NextRequest) {
  if (process.env.NEXT_PUBLIC_QUACK_API !== "1" || process.env.QUACK_GUEST_LANDING === "0") return NextResponse.next();
  if (request.cookies.has("quack_session")) return NextResponse.next();
  return NextResponse.redirect(new URL("/landing", request.url));
}

export const config = {
  matcher: "/",
};
