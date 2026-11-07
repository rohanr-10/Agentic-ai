import { StrictMode, useEffect } from "react";
import { flushSync } from "react-dom";
import { configureApi } from "@/services/api/client";
import { authStore } from "@/features/auth/authStore";
import { toast } from "@/components/ui/toast";

/**
 * Wires the API client to the in-memory auth store. Kept as a provider so the
 * app has exactly one configuration point; safe under StrictMode double-mount.
 */
export function ApiProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    configureApi({
      baseUrl: import.meta.env.VITE_API_BASE_URL ?? "/api/v1",
      auth: {
        getAccessToken: () => authStore.getAccessToken(),
        getRefreshToken: () => authStore.getRefreshToken(),
        setTokens: (access, refresh) => {
          if (access && refresh) authStore.setTokens(access, refresh);
        },
        onSessionExpired: () => {
          if (authStore.getSnapshot().user) {
            flushSync(() => {
              toast.error("Session expired", "Please sign in again.");
            });
          }
          authStore.clear();
        },
      },
    });
  }, []);

  return <StrictMode>{children}</StrictMode>;
}
