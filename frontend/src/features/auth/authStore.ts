/**
 * Auth state store — in-memory only (no localStorage/sessionStorage).
 *
 * Rationale: the backend issues bearer tokens with 30-day refresh TTLs and
 * detects refresh-token reuse by revoking ALL sessions. Persisting those
 * tokens in browser storage would expose long-lived credentials to any XSS
 * on the page. The accepted trade-off: sessions do not survive a full page
 * reload; users sign in again (the backend's threat model explicitly prefers
 * this — docs/SECURITY.md). Access-token TTL is 1800 s, so the HTTP client's
 * silent refresh keeps a session alive across long-lived interactions.
 */
import type { TokenPair, UserOut } from "@/types/api";

interface AuthSnapshot {
  accessToken: string | null;
  refreshToken: string | null;
  user: UserOut | null;
  /** Login or last refresh epoch — used by tests/debugging. */
  refreshedAt: number;
}

const listeners = new Set<() => void>();

let state: AuthSnapshot = {
  accessToken: null,
  refreshToken: null,
  user: null,
  refreshedAt: 0,
};

function emit() {
  for (const listener of listeners) listener();
}

export const authStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  getSnapshot(): AuthSnapshot {
    return state;
  },

  applyTokens(tokens: TokenPair): void {
    state = {
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
      user: tokens.user,
      refreshedAt: Date.now(),
    };
    emit();
  },

  setUser(user: UserOut): void {
    state = { ...state, user };
    emit();
  },

  setTokens(access: string | null, refresh: string | null): void {
    state = {
      ...state,
      accessToken: access,
      refreshToken: refresh,
      refreshedAt: Date.now(),
    };
    emit();
  },

  clear(): void {
    state = { accessToken: null, refreshToken: null, user: null, refreshedAt: 0 };
    emit();
  },

  getAccessToken(): string | null {
    return state.accessToken;
  },

  getRefreshToken(): string | null {
    return state.refreshToken;
  },
};
