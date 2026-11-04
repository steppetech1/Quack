/**
 * The backend's session cookie (`quack_session`) carries a fixed `Domain` (prod: `qvack.online`,
 * shared with `preview.qvack.online`, settings.session_cookie_domain). A browser drops any `Set-Cookie`
 * whose `Domain` is not the request host or one of its parents (RFC 6265 §5.3), so on a local stand
 * (`QUACK_API_URL=http://localhost:…`) the cookie from `/auth/link/verify` and `/auth/google/callback`
 * never lands. Scope it to the host actually serving the response instead — a no-op once `Domain`
 * already matches, i.e. in any real deployment.
 */
export function hostScopedSetCookie(setCookie: string, requestHost: string): string {
  const m = /;\s*domain=([^;]+)/i.exec(setCookie);
  if (!m) return setCookie;
  const domain = m[1].trim().replace(/^\./, "");
  const host = requestHost.split(":")[0];
  if (host === domain || host.endsWith(`.${domain}`)) return setCookie;
  return setCookie.replace(/;\s*domain=[^;]+/i, "");
}

/**
 * Copies every `Set-Cookie` from an upstream fetch Response onto an outgoing one, fixed up by
 * `hostScopedSetCookie`. `google_callback` sets two (the session, and clearing the OIDC state
 * cookie it set in `google_start`) — `Headers.get("set-cookie")` returns only one of them
 * (the Fetch spec excludes `Set-Cookie` from header combining), so this must use
 * `getSetCookie()` and `response.headers.append`, not `.set`, or the second cookie is dropped.
 */
export function forwardSetCookies(upstream: Headers, response: Response, requestHost: string): void {
  for (const setCookie of upstream.getSetCookie()) response.headers.append("set-cookie", hostScopedSetCookie(setCookie, requestHost));
}
