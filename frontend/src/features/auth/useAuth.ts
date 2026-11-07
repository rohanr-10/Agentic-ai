import { useSyncExternalStore } from "react";
import { authStore } from "@/features/auth/authStore";
import type { UserOut } from "@/types/api";

export interface AuthState {
  user: UserOut | null;
  isAuthenticated: boolean;
}

export function useAuth(): AuthState {
  const snapshot = useSyncExternalStore(
    authStore.subscribe,
    authStore.getSnapshot,
    authStore.getSnapshot,
  );
  return { user: snapshot.user, isAuthenticated: snapshot.user !== null };
}
