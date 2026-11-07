import { useCallback } from "react";
import { useNavigate } from "react-router";
import { post } from "@/services/api/client";
import { ApiError } from "@/services/api/client";
import { authStore } from "@/features/auth/authStore";
import type { TokenPair, UserOut } from "@/types/api";
import { patch } from "@/services/api/client";

export interface AuthActionError {
  message: string;
  field?: "email" | "password" | "display_name" | "form";
}

/** Normalises backend error payloads into a field-scoped form error. */
function toFormError(err: unknown): AuthActionError {
  if (err instanceof ApiError) {
    if (err.code === "validation_failed" && Array.isArray(err.details)) {
      const first = (err.details as Array<{ loc?: string[]; msg?: string }>)[0];
      const loc = first?.loc?.find((part) => part === "email" || part === "password" || part === "display_name");
      return { message: first?.msg ?? err.message, field: (loc as AuthActionError["field"]) ?? "form" };
    }
    return { message: err.displayMessage, field: "form" };
  }
  return { message: (err as Error)?.message ?? "Unexpected error", field: "form" };
}

export function useAuthActions() {
  const navigate = useNavigate();

  const login = useCallback(
    async (email: string, password: string): Promise<AuthActionError | null> => {
      try {
        const tokens = await post<TokenPair>("/auth/login", { email, password }, { retryOn401: false });
        authStore.applyTokens(tokens);
        return null;
      } catch (err) {
        return toFormError(err);
      }
    },
    [],
  );

  const register = useCallback(
    async (email: string, password: string, displayName: string): Promise<AuthActionError | null> => {
      try {
        const tokens = await post<TokenPair>(
          "/auth/register",
          { email, password, display_name: displayName },
          { retryOn401: false },
        );
        authStore.applyTokens(tokens);
        return null;
      } catch (err) {
        return toFormError(err);
      }
    },
    [],
  );

  const logout = useCallback(async () => {
    try {
      await post("/auth/logout", {}, { retryOn401: false });
    } catch {
      /* server-side state is authoritative; local clear regardless */
    }
    authStore.clear();
    navigate("/login", { replace: true });
  }, [navigate]);

  const updateProfile = useCallback(async (displayName: string): Promise<AuthActionError | null> => {
    try {
      const user = await patch<UserOut>("/auth/me", { display_name: displayName });
      authStore.setUser(user);
      return null;
    } catch (err) {
      return toFormError(err);
    }
  }, []);

  return { login, register, logout, updateProfile };
}
