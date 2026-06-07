import type { User } from "./types";

// The single HTTP entry point. It implements the Unit 1 session+CSRF contract so
// no component has to think about cookies or status codes:
//
//   - credentials:"include" on every request -> the session + csrftoken cookies
//     ride along (same-origin in dev via the Vite proxy).
//   - on writes (POST/PATCH/DELETE), read the JS-readable `csrftoken` cookie and
//     echo it in the X-CSRF-Token header (what Unit 1's require_csrf checks).
//   - 401 anywhere -> notify the auth layer (recenters the app on the login page).
//   - 403 on a write -> the CSRF token is likely stale; refresh it via /auth/me
//     (which re-issues the cookie) and retry the write ONCE.

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";
const CSRF_COOKIE = "csrftoken";
const CSRF_HEADER = "X-CSRF-Token";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

// The auth layer registers this so a 401 from any call clears the session and
// sends the user back to login, without each caller handling it.
let unauthorizedHandler: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

/** Read a (non-HttpOnly) cookie value by name, or null. */
export function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : null;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  /** Internal: guards the single post-403 refresh-and-retry. */
  retried?: boolean;
}

export async function apiFetch<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = (opts.method ?? "GET").toUpperCase();
  const headers = new Headers();
  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(opts.body);
  }
  if (UNSAFE_METHODS.has(method)) {
    const token = readCookie(CSRF_COOKIE);
    if (token) headers.set(CSRF_HEADER, token);
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body,
    credentials: "include",
    signal: opts.signal,
  });

  if (res.status === 401) {
    unauthorizedHandler?.();
    throw new ApiError(401, "not authenticated");
  }

  // Stale/missing CSRF token on a write: re-issue it via /auth/me (a safe GET
  // that resets the csrftoken cookie) and retry once. If /me itself is 401, the
  // block above funnels the user to login.
  if (res.status === 403 && UNSAFE_METHODS.has(method) && !opts.retried) {
    await apiFetch<User>("/auth/me");
    return apiFetch<T>(path, { ...opts, retried: true });
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : res.statusText;
    throw new ApiError(res.status, detail);
  }
  return data as T;
}
