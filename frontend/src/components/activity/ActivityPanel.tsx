import { Link } from "react-router";
import { Bell, CheckCircle2, CircleAlert, CircleDot, WifiOff, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useActivity, type ActivityEntry } from "@/features/activity/ActivityProvider";
import { EmptyState } from "@/components/ui/State";
import { formatTime } from "@/utils/format";
import { cn } from "@/utils/cn";

const LEVEL_ICON: Record<NonNullable<ActivityEntry["level"]>, LucideIcon> = {
  info: CircleDot,
  success: CheckCircle2,
  warning: Bell,
  error: CircleAlert,
};

const LEVEL_CLASS: Record<NonNullable<ActivityEntry["level"]>, string> = {
  info: "text-fg-subtle",
  success: "text-success",
  warning: "text-warning",
  error: "text-danger",
};

export default function ActivityPanel({ inDrawer, onClose }: { inDrawer: boolean; onClose?: () => void }) {
  const { feed, connection, pendingCount, setFocusTask } = useActivity();

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
        <div>
          <h2 className="text-sm font-semibold text-fg">Agent activity</h2>
          <p className="text-2xs text-fg-subtle">
            {connection === "open" ? "Live" : connection === "reconnecting" ? "Reconnecting…" : "Offline"} ·{" "}
            {pendingCount} pending approval{pendingCount === 1 ? "" : "s"}
          </p>
        </div>
        {inDrawer ? (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close activity panel"
            className="rounded-xs p-1.5 text-fg-subtle transition-colors hover:bg-bg-hover hover:text-fg"
          >
            <X size={16} aria-hidden />
          </button>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {feed.length === 0 ? (
          <EmptyState
            className="py-16"
            icon={<Bell />}
            title="No activity yet"
            description={
              pendingCount > 0
                ? "There are approvals waiting for your decision."
                : "Events from running tasks and approvals will appear here in real time. Start a task or send a message."
            }
            action={
              pendingCount > 0 ? (
                <Link
                  to="/approvals"
                  className="rounded-sm border border-border px-3 py-1.5 text-xs font-medium text-fg transition-colors hover:bg-bg-hover"
                >
                  Review approvals
                </Link>
              ) : undefined
            }
          />
        ) : (
          <ul className="divide-y divide-border/60" aria-label="Activity feed">
            {feed.map((entry) => {
              const Icon = LEVEL_ICON[entry.level ?? "info"];
              const taskHref = entry.taskId ? `/tasks/${entry.taskId}` : null;
              return (
                <li key={entry.id} className="px-4 py-3">
                  <button
                    type="button"
                    disabled={!taskHref}
                    onClick={() => entry.taskId && setFocusTask(entry.taskId)}
                    className={cn(
                      "flex w-full items-start gap-2.5 text-left transition-colors",
                      taskHref && "hover:bg-bg-hover/50",
                    )}
                  >
                    <Icon size={14} aria-hidden className={cn("mt-0.5 shrink-0", LEVEL_CLASS[entry.level ?? "info"])} />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline justify-between gap-2">
                        <span className="truncate text-sm font-medium text-fg">{entry.title}</span>
                        <span className="shrink-0 text-2xs text-fg-subtle">{formatTime(entry.at)}</span>
                      </span>
                      {entry.detail && (
                        <span className="mt-0.5 block text-xs leading-relaxed text-fg-muted">{entry.detail}</span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {connection === "reconnecting" && (
        <footer className="flex items-center gap-2 border-t border-border px-4 py-2.5 text-2xs text-fg-subtle">
          <WifiOff size={12} aria-hidden /> Reconnecting to live events…
        </footer>
      )}
      {!inDrawer && (
        <footer className="border-t border-border px-4 py-2 text-2xs text-fg-subtle">
          Events are live from the backend. Nothing here is simulated.
        </footer>
      )}
    </div>
  );
}
