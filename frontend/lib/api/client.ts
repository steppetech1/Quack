import type { components, paths } from "./schema";

/** Backend models, generated from `lib/api/openapi.json` (`npm run api:types`) */
export type S = components["schemas"];

/** Real backend instead of mocks. Build-time switch: `NEXT_PUBLIC_QUACK_API=1` in `.env.local`. */
export const API_MODE = process.env.NEXT_PUBLIC_QUACK_API === "1";

/** Requests go through the Next.js proxy (`next.config.ts` rewrites `/api/*` to the backend), so no CORS */
const BASE = "/api";

/** A direct backend URL for a resource fetched by the browser itself (an `<iframe src>`, for example),
 * through the same proxy and cookie auth as every other request. */
export const apiUrl = (path: string) => BASE + path;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

/** Every write on the backend is idempotent by key */
export const idem = () => crypto.randomUUID();

/** The session is the `quack_session` cookie (HttpOnly, same origin through the proxy): no token in JS */
function headers(json: boolean): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  return h;
}

/** No cookie or an expired one sends the user to the login screen */
function onUnauthorized() {
  if (typeof window !== "undefined" && !/^\/(login|signup)/.test(window.location.pathname)) {
    // full reload on purpose: no router here, and all in-memory data belongs to the old session
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

export async function api<T>(method: "GET" | "POST" | "PATCH" | "PUT" | "DELETE", path: string, body?: unknown): Promise<T> {
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
type WriteMethod = "post" | "patch" | "put" | "delete";
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

/** A file to the backend (multipart): the browser sets the boundary, so no Content-Type here */
export async function upload<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(BASE + path, { method: "POST", body: form });
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

export const get = <T>(path: string) => api<T>("GET", path);
export const post = <P extends WritePath<"post">>(path: P, ...[body]: BodyArg<P, "post">) =>
  api<ResponseOf<P, "post">>("POST", path, body ?? {});
export const patch = <P extends WritePath<"patch">>(path: P, ...[body]: BodyArg<P, "patch">) =>
  api<ResponseOf<P, "patch">>("PATCH", path, body);
export const put = <P extends WritePath<"put">>(path: P, ...[body]: BodyArg<P, "put">) =>
  api<ResponseOf<P, "put">>("PUT", path, body);
export const del = <P extends WritePath<"delete">>(path: P, ...[body]: BodyArg<P, "delete">) =>
  api<ResponseOf<P, "delete">>("DELETE", path, body);

export const qs = (params: Record<string, string | number | undefined | null>) => {
  const p = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return p.length ? `?${new URLSearchParams(p.map(([k, v]) => [k, String(v)]))}` : "";
};

export type SseEvent = { event: string; data: Record<string, unknown> };

/** SSE over fetch: a POST body (actions) can't go through EventSource */
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
      // Only the parse is guarded: an exception from the handler (for example the `error` frame turned into an ApiError)
      // must reach the caller as it is, not be caught here and replayed as a frame with raw data and no error code
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(data.join("\n"));
      } catch {
        parsed = { raw: data.join("\n") };
      }
      onEvent({ event, data: parsed });
    }
  }
}

/** A file from the backend (an export) saved through the browser; the name comes from `Content-Disposition`.
 * Fetched, not linked: a failure (no cards yet → 404) must be a message, not a JSON file in the downloads. */
export async function download(path: string, fallbackName: string): Promise<void> {
  const res = await fetch(BASE + path);
  if (!res.ok) throw await toError(res);
  const head = res.headers.get("Content-Disposition") ?? "";
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(head)?.[1];
  const plain = /filename="([^"]+)"/i.exec(head)?.[1];
  let name = plain ?? fallbackName;
  if (utf8) {
    try {
      name = decodeURIComponent(utf8);
    } catch {
      // keep the ASCII name
    }
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
