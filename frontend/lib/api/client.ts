import type { components, paths } from "./schema";

/** Backend models, generated from `lib/api/openapi.json` (`npm run api:types`) */
export type S = components["schemas"];

/** Real backend instead of mocks. Build-time switch: `NEXT_PUBLIC_QUACK_API=1` in `.env.local`. */
export const API_MODE = process.env.NEXT_PUBLIC_QUACK_API === "1";

/** Requests go through the Next.js proxy (`next.config.ts` rewrites `/api/*` to the backend), so no CORS */
const BASE = "/api";
const TOKEN_KEY = "quack.token";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // private mode: the session lives until the tab closes
  }
}

/** Every write on the backend is idempotent by key */
export const idem = () => crypto.randomUUID();

function headers(json: boolean): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  const token = getToken();
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

/** A missing or expired token sends the user to the login screen */
function onUnauthorized() {
  setToken(null);
  if (typeof window !== "undefined" && !/^\/(login|signup)/.test(window.location.pathname)) {
    // full reload on purpose: no router here, and all in-memory data belongs to the old token
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/login");
  }
}

async function toError(res: Response): Promise<ApiError> {
  let code = "HTTP_ERROR";
  let message = res.statusText;
  try {
    const body = await res.json();
    code = body?.error?.code ?? code;
    message = body?.error?.message ?? message;
  } catch {
    // not JSON
  }
  if (res.status === 401) onUnauthorized();
  return new ApiError(res.status, code, message);
}

export async function api<T>(method: "GET" | "POST" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: headers(body !== undefined),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw await toError(res);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

/*
 * Write calls are typed by the OpenAPI contract: the call's path (`/v1/topics/${id}/…`) is matched
 * against the schema's path templates (`/v1/topics/{topic_id}/…`), and the body and the response
 * come from that operation. A body that doesn't fit the backend is a compile error, not a 422.
 */
type WriteMethod = "post" | "patch" | "delete";
/** `/v1/topics/{topic_id}/x` → `/v1/topics/${string}/x` */
type Template<P extends string> = P extends `${infer A}{${string}}${infer B}` ? `${A}${string}${Template<B>}` : P;
type Op<P extends string, M extends WriteMethod> = {
  // `${string}` also matches "/", so a shorter template can match too: it only counts if it has this method
  [K in keyof paths]: P extends Template<K> ? Exclude<paths[K][M], undefined> : never;
}[keyof paths];
/** Paths that have this method on the backend */
export type WritePath<M extends WriteMethod> = {
  [K in keyof paths]: paths[K][M] extends undefined | never ? never : Template<K>;
}[keyof paths];
type Json<C> = C extends { content: { "application/json": infer B } } ? B : undefined;
export type BodyOf<P extends string, M extends WriteMethod> =
  Op<P, M> extends { requestBody: infer R } ? Json<R> : Op<P, M> extends { requestBody?: infer R } ? Json<R> | undefined : undefined;
export type ResponseOf<P extends string, M extends WriteMethod> =
  Op<P, M> extends { responses: infer R }
    ? R extends { 200: infer C } ? Json<C> : R extends { 201: infer C } ? Json<C> : R extends { 202: infer C } ? Json<C> : undefined
    : never;
/** The body argument is optional only where the endpoint takes none */
type BodyArg<P extends string, M extends WriteMethod> = undefined extends BodyOf<P, M> ? [body?: BodyOf<P, M>] : [body: BodyOf<P, M>];

export const get = <T>(path: string) => api<T>("GET", path);
export const post = <P extends WritePath<"post">>(path: P, ...[body]: BodyArg<P, "post">) =>
  api<ResponseOf<P, "post">>("POST", path, body ?? {});
export const patch = <P extends WritePath<"patch">>(path: P, ...[body]: BodyArg<P, "patch">) =>
  api<ResponseOf<P, "patch">>("PATCH", path, body);
export const del = <P extends WritePath<"delete">>(path: P, ...[body]: BodyArg<P, "delete">) =>
  api<ResponseOf<P, "delete">>("DELETE", path, body);

export const qs = (params: Record<string, string | number | undefined | null>) => {
  const p = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return p.length ? `?${new URLSearchParams(p.map(([k, v]) => [k, String(v)]))}` : "";
};

export type SseEvent = { event: string; data: Record<string, unknown> };

/** SSE over fetch: the backend needs the Authorization header, which EventSource can't send */
export function sse<P extends WritePath<"post">>(
  method: "POST",
  path: P,
  body: BodyOf<P, "post">,
  onEvent: (e: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void>;
export function sse(method: "GET", path: string, body: undefined, onEvent: (e: SseEvent) => void, signal?: AbortSignal): Promise<void>;
export async function sse(
  method: "GET" | "POST",
  path: string,
  body: unknown,
  onEvent: (e: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(BASE + path, {
    method,
    headers: { ...headers(body !== undefined), Accept: "text/event-stream" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw await toError(res);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value.replace(/\r\n/g, "\n");
    let cut: number;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith(":")) continue; // keepalive
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (!data.length) continue;
      try {
        onEvent({ event, data: JSON.parse(data.join("\n")) });
      } catch {
        onEvent({ event, data: { raw: data.join("\n") } });
      }
    }
  }
}
