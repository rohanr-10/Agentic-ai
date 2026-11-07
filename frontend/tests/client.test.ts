import { describe, expect, it } from "vitest";
import {
  apiRequest,
  ApiError,
  configureApi,
  NetworkError,
} from "@/services/api/client";

const TOKEN = "test-access-token";

function makeServer() {
  const state: Record<string, unknown> = {};
  return state;
}

describe("HTTP client", () => {
  it("attaches the bearer token and returns parsed JSON", async () => {
    const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
    configureApi({
      baseUrl: "/api/v1",
      auth: {
        getAccessToken: () => TOKEN,
        getRefreshToken: () => "refresh-token",
        setTokens: () => {},
        onSessionExpired: () => {},
      },
    });
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch;

    const data = await apiRequest<{ ok: boolean }>({ path: "/health/live" });
    expect(data).toEqual({ ok: true });
    expect(calls[0]?.url).toBe("/api/v1/health/live");
    expect((calls[0]?.init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    expect(makeServer()).toEqual({});
  });

  it("surfaces the backend error envelope as ApiError", async () => {
    globalThis.fetch = (async () =>
      new Response(
        JSON.stringify({
          error: { code: "not_found", message: "conversation not found" },
          trace_id: "t-1",
        }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      )) as typeof fetch;

    try {
      await apiRequest({ path: "/conversations/00000000-0000-0000-0000-000000000000" });
      expect.fail("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiError = err as ApiError;
      expect(apiError.status).toBe(404);
      expect(apiError.code).toBe("not_found");
      expect(apiError.traceId).toBe("t-1");
    }
  });

  it("refreshes once on 401 and retries the original request", async () => {
    let hits = 0;
    let refreshHits = 0;
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/refresh")) {
        refreshHits += 1;
        return new Response(
          JSON.stringify({
            access_token: "fresh-token",
            refresh_token: "fresh-refresh",
            token_type: "bearer",
            expires_in: 1800,
            user: { id: "u1", email: "a@b.c", display_name: "A", role: "user", is_active: true, created_at: "2026-01-01T00:00:00Z" },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      hits += 1;
      if (hits === 1) {
        return new Response(JSON.stringify({ error: { code: "session_expired", message: "expired" } }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch;

    const data = await apiRequest<{ ok: boolean }>({ path: "/conversations" });
    expect(data.ok).toBe(true);
    expect(hits).toBe(2);
    expect(refreshHits).toBe(1);
  });

  it("reports network failures as NetworkError", async () => {
    globalThis.fetch = (async () => {
      throw new TypeError("fetch failed");
    }) as typeof fetch;
    await expect(apiRequest({ path: "/models" })).rejects.toBeInstanceOf(NetworkError);
  });

  it("returns null for 204 responses", async () => {
    globalThis.fetch = (async () => new Response(null, { status: 204 })) as typeof fetch;
    const data = await apiRequest({ path: "/auth/logout", method: "POST", body: {} });
    expect(data).toBeNull();
  });
});
