import { NextResponse } from "next/server";
import { forwardSetCookies } from "@/lib/api/cookie-domain";

const API_URL = process.env.QUACK_API_URL ?? "http://localhost:8000";

/** Proxies `GET /auth/link/verify`: same as the `next.config.ts` rewrite, except the session cookie's
 * `Domain` is scoped to the request host when it would otherwise not match (see `cookie-domain.ts`). */
export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const cookie = request.headers.get("cookie");
  const upstream = await fetch(`${API_URL}/auth/link/verify${url.search}`, {
    redirect: "manual",
    headers: cookie ? { cookie } : undefined,
  });
  const body = await upstream.arrayBuffer();
  const contentType = upstream.headers.get("content-type");
  const response = new NextResponse(body, { status: upstream.status, headers: contentType ? { "content-type": contentType } : undefined });
  if (upstream.status >= 300 && upstream.status < 400) {
    const location = upstream.headers.get("location");
    if (location) response.headers.set("location", location);
  }
  forwardSetCookies(upstream.headers, response, url.host);
  return response;
}

/** Proxies `POST /auth/link/verify`: the form of the page that `GET` gives. `GET` only shows the page
 * (mail scanners open links), the token is spent here; the body and the cookie travel as in `GET`. */
export async function POST(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const contentType = request.headers.get("content-type");
  const upstream = await fetch(`${API_URL}/auth/link/verify`, {
    method: "POST",
    headers: contentType ? { "content-type": contentType } : undefined,
    body: await request.text(),
    redirect: "manual",
  });
  const body = await upstream.arrayBuffer();
  const upstreamType = upstream.headers.get("content-type");
  const response = new NextResponse(body, { status: upstream.status, headers: upstreamType ? { "content-type": upstreamType } : undefined });
  if (upstream.status >= 300 && upstream.status < 400) {
    const location = upstream.headers.get("location");
    if (location) response.headers.set("location", location);
  }
  forwardSetCookies(upstream.headers, response, url.host);
  return response;
}
