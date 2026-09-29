// The one place that talks HTTP to the backend. Session is the httpOnly cookie `quack_token`, so every
// call goes with credentials. Errors keep the backend's shape: {"error": {"code", "message"}}.

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (s: unknown): s is string => typeof s === "string" && UUID_RE.test(s);

export class ApiError extends Error {
  constructor(
    /** HTTP status; 0 when the server could not be reached */
    public status: number,
    /** Backend error code (`unauthorized`, `conflict`, `validation_failed`, ...) or `network` */
    public code: string,
    message: string,
    public requestId?: string,
  ) {
    super(message);
  }
}

type Method = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

/** `RequestInit` plus the two knobs callers need from this layer. */
export type ApiInit = RequestInit & {
  /** Keep the 401 to yourself instead of sending the student to the sign-in screen */
  noAuthRedirect?: boolean;
  /** Retry once even though the call is not a GET, or never retry a GET */
  retry?: boolean;
};

/** Auth calls answer 401 as an ordinary outcome ("wrong password", "not signed in") */
const AUTH_PATH = /^\/auth(\/|$)/;
/** A second attempt can plausibly succeed: the server was unreachable or momentarily unavailable */
const RETRYABLE = new Set([0, 502, 503, 504]);
const RETRY_MS = 300;

async function toError(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => null);
  const err = body?.error;
  return new ApiError(
    res.status,
    typeof err?.code === "string" ? err.code : "internal",
    typeof err?.message === "string" ? err.message : res.statusText,
    res.headers.get("X-Request-Id") ?? body?.request_id ?? undefined,
  );
}

/**
 * Sends the student to the sign-in screen with the current screen in `next`, so the way back is the
 * same page. Returns true when the redirect was started — the caller still throws, the page is going.
 */
function redirectToLogin(): boolean {
  if (typeof window === "undefined") return false;
  const { pathname, search, hash } = window.location;
  if (pathname.startsWith("/login")) return false;
  const next = `${pathname}${search}${hash}`;
  window.location.assign(`/login?next=${encodeURIComponent(next)}`);
  return true;
}

async function once(method: Method, path: string, body: unknown, init?: ApiInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      method,
      credentials: "include",
      headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...init?.headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "network", "Server unreachable");
  }
  if (!res.ok) throw await toError(res);
  return res;
}

const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));

export async function request(method: Method, path: string, body?: unknown, init?: ApiInit): Promise<Response> {
  const mayRetry = init?.retry ?? method === "GET";
  try {
    return await once(method, path, body, init);
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;

    if (mayRetry && RETRYABLE.has(e.status) && !init?.signal?.aborted) {
      await sleep(RETRY_MS);
      try {
        return await once(method, path, body, init);
      } catch (again) {
        if (again instanceof ApiError) throw handle(again, path, init);
        throw again;
      }
    }
    throw handle(e, path, init);
  }
}

function handle(e: ApiError, path: string, init?: ApiInit): ApiError {
  if (e.status === 401 && !init?.noAuthRedirect && !AUTH_PATH.test(path)) redirectToLogin();
  return e;
}

async function json<T>(method: Method, path: string, body?: unknown, init?: ApiInit): Promise<T> {
  const res = await request(method, path, body, init);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  get: <T>(path: string, init?: ApiInit) => json<T>("GET", path, undefined, init),
  post: <T>(path: string, body?: unknown, init?: ApiInit) => json<T>("POST", path, body ?? {}, init),
  patch: <T>(path: string, body: unknown, init?: ApiInit) => json<T>("PATCH", path, body, init),
  delete: <T>(path: string, init?: ApiInit) => json<T>("DELETE", path, undefined, init),
};
