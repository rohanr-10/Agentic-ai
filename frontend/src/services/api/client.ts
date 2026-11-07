/**
 * Typed HTTP client for the backend API.
 *
 * - Injected auth callbacks (registered once by the auth provider) — the
 *   client has no React dependency and no import cycle.
 * - Uniform error handling: every non-2xx becomes an `ApiError` carrying the
 *   backend's error envelope (code/message/details/trace_id). Nothing is
 *   silently swallowed.
 * - Single-flight 401 recovery: on 401 (except from /auth/refresh itself) the
 *   client rotates the refresh token once, retries the original request, and
 *   reports session expiry if rotation fails.
 * - Timeouts via AbortController; the caller's external signal is respected.
 */
import type { ErrorEnvelope } from "@/types/api";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly traceId: string | null;

  constructor(status: number, code: string, message: string, details?: unknown, traceId?: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.traceId = traceId ?? null;
  }

  /** Human sentence for toasts; falls back to generic text per status. */
  get displayMessage(): string {
    return this.message || `Request failed (${this.status})`;
  }
}

export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NetworkError";
  }
}

interface AuthCallbacks {
  getAccessToken: () => string | null;
  getRefreshToken: () => string | null;
  setTokens: (access: string | null, refresh: string | null) => void;
  onSessionExpired: () => void;
}

let auth: AuthCallbacks | null = null;
let baseUrl = "/api/v1";

export function configureApi(options: {
  baseUrl?: string;
  auth: AuthCallbacks;
}): void {
  if (options.baseUrl) baseUrl = options.baseUrl.replace(/\/+$/, "");
  auth = options.auth;
}

export function apiBaseUrl(): string {
  return baseUrl;
}

const DEFAULT_TIMEOUT_MS = 30_000;

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  path: string; // relative to baseUrl; must start with "/"
  query?: Record<string, string | number | boolean | undefined | null>;
  body?: unknown; // JSON-serialised; FormData passthrough
  formData?: FormData;
  timeoutMs?: number;
  signal?: AbortSignal;
  /** Internal: marks the refresh call to avoid recursion. */
  isRefresh?: boolean;
  retryOn401?: boolean;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  let url = `${baseUrl}${path}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) continue;
      params.append(key, String(value));
    }
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }
  return url;
}

async function parseErrorEnvelope(response: Response): Promise<{ code: string; message: string; details: unknown; traceId: string | null }> {
  try {
    const body = (await response.json()) as Partial<ErrorEnvelope> & { detail?: string };
    if (body && typeof body === "object" && body.error) {
      return {
        code: body.error.code ?? `http_${response.status}`,
        message: body.error.message ?? response.statusText,
        details: body.error.details,
        traceId: body.trace_id ?? null,
      };
    }
    // FastAPI/Starlette fallbacks (e.g. proxy errors)
    return {
      code: `http_${response.status}`,
      message: body?.detail ?? response.statusText,
      details: null,
      traceId: null,
    };
  } catch {
    return {
      code: `http_${response.status}`,
      message: response.statusText || "non-JSON error response",
      details: null,
      traceId: null,
    };
  }
}

let refreshInFlight: Promise<string | null> | null = null;

/** Rotate the refresh token once; resolves with a fresh access token or null. */
async function tryTokenRefresh(): Promise<string | null> {
  if (!auth) return null;
  const refresh = auth.getRefreshToken();
  if (!refresh) return null;
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const response = await fetch(`${baseUrl}/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refresh }),
        });
        if (!response.ok) return null;
        const data = (await response.json()) as {
          access_token: string;
          refresh_token: string;
        };
        auth?.setTokens(data.access_token, data.refresh_token);
        return data.access_token;
      } catch {
        return null;
      } finally {
        refreshInFlight = null;
      }
    })();
  }
  return refreshInFlight;
}

/** Core request routine. Returns parsed JSON (or null for 204). */
export async function apiRequest<T = unknown>(options: RequestOptions): Promise<T> {
  const { method = "GET" } = options;
  const url = buildUrl(options.path, options.query);

  const doFetch = async (accessToken: string | null): Promise<Response> => {
    const headers: Record<string, string> = {};
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    if (options.body !== undefined && !options.formData) {
      headers["Content-Type"] = "application/json";
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new DOMException("timeout", "TimeoutError")), options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
    const external = options.signal;
    const onExternalAbort = () => controller.abort(external?.reason);
    if (external) {
      if (external.aborted) controller.abort(external.reason);
      external.addEventListener("abort", onExternalAbort, { once: true });
    }
    try {
      return await fetch(url, {
        method,
        headers,
        body: options.formData ?? (options.body !== undefined ? JSON.stringify(options.body) : undefined),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
      external?.removeEventListener("abort", onExternalAbort);
    }
  };

  const token = auth?.getAccessToken() ?? null;
  let response: Response;
  try {
    response = await doFetch(token);
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err; // caller-initiated abort or timeout propagates as AbortError
    }
    throw new NetworkError(`cannot reach the server (${(err as Error).message})`);
  }

  // ---- 401 recovery -------------------------------------------------------
  if (
    response.status === 401 &&
    options.retryOn401 !== false &&
    !options.isRefresh &&
    auth
  ) {
    const fresh = await tryTokenRefresh();
    if (fresh) {
      response = await doFetch(fresh);
    } else {
      auth.setTokens(null, null);
      auth.onSessionExpired();
      throw new ApiError(401, "session_expired", "Your session has expired. Please sign in again.");
    }
  }

  if (!response.ok) {
    const parsed = await parseErrorEnvelope(response);
    throw new ApiError(response.status, parsed.code, parsed.message, parsed.details, parsed.traceId);
  }

  if (response.status === 204) return null as T;
  const text = await response.text();
  if (!text) return null as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(502, "invalid_response", "the server response was not valid JSON");
  }
}

/** Typed helpers used by the service modules. */
export const get = <T>(path: string, query?: RequestOptions["query"], signal?: AbortSignal) =>
  apiRequest<T>({ path, query, signal, method: "GET" });
export const post = <T>(path: string, body?: unknown, options?: Partial<RequestOptions>) =>
  apiRequest<T>({ ...options, path, body, method: "POST" });
export const patch = <T>(path: string, body?: unknown, signal?: AbortSignal) =>
  apiRequest<T>({ path, body, signal, method: "PATCH" });
export const del = <T>(path: string, signal?: AbortSignal) =>
  apiRequest<T>({ path, signal, method: "DELETE" });
