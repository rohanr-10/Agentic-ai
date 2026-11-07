import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Outlet, useLocation } from "react-router";
import { cn } from "@/utils/cn";
import { useIsDesktop } from "@/hooks/useMediaQuery";
import Sidebar from "@/components/navigation/Sidebar";
import ActivityPanel from "@/components/activity/ActivityPanel";
import MobileTopBar from "@/components/layout/MobileTopBar";
import { ActivityProvider } from "@/features/activity/ActivityProvider";

const COLLAPSE_PREF_KEY = "agp.sidebar.collapsed";

function readCollapsedPreference(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_PREF_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * Three-region workspace shell.
 *  - Desktop (≥1024px): persistent sidebar + outlet + activity panel.
 *  - Tablet (768–1023px): overlay sidebar (toggle), activity as drawer.
 *  - Mobile (<768px): drawer navigation, activity drawer, top bar.
 *
 * Overlays close through their own interaction handlers (nav clicks, Escape,
 * backdrop taps); no route-change effect is needed.
 */
export default function AppLayout() {
  const isDesktop = useIsDesktop();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readCollapsedPreference);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [activityMobileOpen, setActivityMobileOpen] = useState(false);
  const location = useLocation();
  void location; // reserved: route-aware layout tweaks

  // Persist the collapse preference (external system write).
  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSE_PREF_KEY, sidebarCollapsed ? "1" : "0");
    } catch {
      /* storage unavailable (private mode) — preference is simply not persisted */
    }
  }, [sidebarCollapsed]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileNavOpen(false);
        setActivityMobileOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <ActivityProvider
      activityAvailable={activityOpen || activityMobileOpen}
      onToggleActivity={() => {
        if (isDesktop) setActivityOpen((v) => !v);
        else setActivityMobileOpen((v) => !v);
      }}
      activityOpen={activityOpen || activityMobileOpen}
    >
      <div className="flex h-dvh overflow-hidden bg-bg text-fg">
        {/* Desktop / tablet persistent sidebar (overlay below lg) */}
        <div
          className={cn(
            "h-full shrink-0 border-r border-border bg-bg-sidebar transition-[width] duration-200",
            "max-lg:fixed max-lg:inset-y-0 max-lg:left-0 max-lg:z-50 max-lg:shadow-[0_0_60px_rgba(0,0,0,0.5)]",
            !isDesktop &&
              !mobileNavOpen &&
              "max-lg:pointer-events-none max-lg:-translate-x-full max-lg:opacity-0",
          )}
          aria-hidden={!isDesktop && !mobileNavOpen}
        >
          <Sidebar
            collapsed={sidebarCollapsed && isDesktop}
            onToggleCollapse={() => setSidebarCollapsed((v) => !v)}
            onNavigate={() => setMobileNavOpen(false)}
          />
        </div>

        {mobileNavOpen && (
          <div
            className="fixed inset-0 z-40 bg-black/50 lg:hidden"
            onClick={() => setMobileNavOpen(false)}
            aria-hidden
          />
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          {!isDesktop && (
            <MobileTopBar
              onOpenNav={() => setMobileNavOpen(true)}
              onOpenActivity={() => setActivityMobileOpen(true)}
            />
          )}
          <div className="flex min-h-0 flex-1">
            <main className="min-w-0 flex-1 overflow-y-auto" id="main-content">
              <Outlet />
            </main>
            {/* Desktop activity panel */}
            <aside
              className={cn(
                "hidden h-full shrink-0 border-l border-border bg-bg-panel transition-[width] duration-200 lg:block",
                activityOpen ? "w-[340px]" : "w-0 border-l-0",
              )}
              aria-hidden={!activityOpen}
            >
              {activityOpen && <ActivityPanel inDrawer={false} />}
            </aside>
          </div>
        </div>

        {/* Mobile / tablet activity drawer */}
        {activityMobileOpen && (
          <>
            <div
              className="fixed inset-0 z-50 bg-black/50 lg:hidden"
              onClick={() => setActivityMobileOpen(false)}
              aria-hidden
            />
            <div
              role="dialog"
              aria-label="Agent activity"
              className="fixed inset-y-0 right-0 z-50 w-[87%] max-w-sm border-l border-border bg-bg-panel shadow-2xl lg:hidden"
            >
              <ActivityPanel inDrawer onClose={() => setActivityMobileOpen(false)} />
            </div>
          </>
        )}

        {/* Desktop activity toggle (floats at the panel edge) */}
        <button
          type="button"
          onClick={() => setActivityOpen((v) => !v)}
          aria-label={activityOpen ? "Hide agent activity" : "Show agent activity"}
          aria-pressed={activityOpen}
          title={activityOpen ? "Hide agent activity" : "Show agent activity"}
          className="absolute right-3 top-[68px] z-30 hidden size-8 items-center justify-center rounded-lg border border-border bg-bg-elevated/90 text-fg-muted shadow-md backdrop-blur transition-colors hover:bg-bg-hover hover:text-fg lg:flex"
        >
          <span
            aria-hidden
            className="relative flex h-3.5 w-2.5 flex-col justify-between"
          >
            <span className="h-1 w-full rounded-sm bg-current opacity-80" />
            <span className="h-1 w-full rounded-sm bg-current opacity-60" />
            <span className="h-1 w-full rounded-sm bg-current opacity-80" />
          </span>
        </button>
      </div>
    </ActivityProvider>
  );
}

export function PageShell({
  title,
  actions,
  children,
  className,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex h-full min-h-0 flex-col", className)}>
      <header className="flex items-center justify-between gap-3 border-b border-border bg-bg-panel/60 px-4 py-3 backdrop-blur-sm sm:px-6">
        <h1 className="text-base font-semibold tracking-tight text-fg">{title}</h1>
        <div className="flex items-center gap-2">{actions}</div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </div>
  );
}
