import { useCallback, useMemo, useSyncExternalStore } from "react";

/**
 * Tailwind-driven media query hook.
 * Implemented with useSyncExternalStore (the React-recommended primitive for
 * browser external state) — no effects, no cascading renders.
 */
export function useMediaQuery(query: string): boolean {
  const mediaQueryList = useMemo(() => {
    if (typeof window === "undefined") return null;
    return window.matchMedia(query);
  }, [query]);

  const subscribe = useCallback(
    (onChange: () => void) => {
      if (!mediaQueryList) return () => {};
      mediaQueryList.addEventListener("change", onChange);
      return () => mediaQueryList.removeEventListener("change", onChange);
    },
    [mediaQueryList],
  );

  const getSnapshot = useCallback(() => mediaQueryList?.matches ?? false, [mediaQueryList]);

  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}

export const useIsDesktop = () => useMediaQuery("(min-width: 1024px)");
export const useIsTablet = () => useMediaQuery("(min-width: 768px) and (max-width: 1023px)");
