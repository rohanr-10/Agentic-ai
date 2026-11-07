import { Activity } from "lucide-react";
import { useAuth } from "@/features/auth/useAuth";
import { useActivity } from "@/features/activity/ActivityProvider";
import { initials } from "@/utils/format";

export default function MobileTopBar({
  onOpenNav,
  onOpenActivity,
}: {
  onOpenNav: () => void;
  onOpenActivity: () => void;
}) {
  const { user } = useAuth();
  const activity = useActivity();
  const pending = activity.pendingCount;

  return (
    <header className="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border bg-bg-sidebar px-3">
      <button
        type="button"
        onClick={onOpenNav}
        aria-label="Open navigation"
        className="flex size-8 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg"
      >
        <span aria-hidden className="flex flex-col gap-1">
          <span className="block h-0.5 w-4 rounded bg-current" />
          <span className="block h-0.5 w-4 rounded bg-current" />
          <span className="block h-0.5 w-4 rounded bg-current" />
        </span>
      </button>

      <span className="flex items-center gap-2 text-sm font-semibold text-fg">
        <span className="flex size-5 items-center justify-center rounded bg-accent/15 text-2xs text-accent">
          {initials(user?.display_name ?? "?")}
        </span>
        Agentic
      </span>

      <button
        type="button"
        onClick={onOpenActivity}
        aria-label="Open agent activity"
        className={cnRelative(pending > 0)}
      >
        <Activity size={17} aria-hidden />
        {pending > 0 && (
          <span
            aria-label={`${pending} pending approvals`}
            className="absolute -right-0.5 -top-0.5 flex size-3.5 items-center justify-center rounded-full border border-bg bg-warning text-[9px] font-bold text-bg"
          >
            {pending > 9 ? "9+" : pending}
          </span>
        )}
      </button>
    </header>
  );
}

function cnRelative(hasDot: boolean) {
  return `relative flex size-8 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg${
    hasDot ? "" : ""
  }`;
}
