import { NextResponse } from "next/server";
import { hostScopedSetCookie } from "@/lib/api/cookie-domain";

const API_URL = process.env.QUACK_API_URL ?? "http://localhost:8000";

/** Proxies `GET /auth/google/callback`; see `lib/api/cookie-domain.ts` for why. */
export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const upstream = await fetch(`${API_URL}/auth/google/callback${url.search}`, { redirect: "manual" });
  const body = await upstream.arrayBuffer();
  const contentType = upstream.headers.get("content-type");
  const response = new NextResponse(body, { status: upstream.status, headers: contentType ? { "content-type": contentType } : undefined });
  if (upstream.status >= 300 && upstream.status < 400) {
    const location = upstream.headers.get("location");
    if (location) response.headers.set("location", location);
  }
  const setCookie = upstream.headers.get("set-cookie");
  if (setCookie) response.headers.set("set-cookie", hostScopedSetCookie(setCookie, url.host));
  return response;
}
