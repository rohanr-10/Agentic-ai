import type { ReactNode } from "react";
import { cn } from "@/utils/cn";

export function Spinner({ size = 16, className }: { size?: number; className?: string }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      style={{ width: size, height: size }}
      className={cn("inline-block animate-spin rounded-full border-2 border-border-strong border-t-accent", className)}
    />
  );
}

export function PageLoader({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex flex-col items-center justify-center gap-3 p-10 text-fg-subtle">
      <Spinner size={22} />
      <span className="text-xs">{label}</span>
    </div>
  );
}

export function InlineLoader({ label }: { label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-xs text-fg-subtle">
      <Spinner size={12} />
      {label}
    </span>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center px-6 py-12 text-center", className)}>
      {icon && (
        <div className="mb-3 flex size-11 items-center justify-center rounded-xl border border-border bg-bg-elevated text-fg-subtle [&>svg]:size-5">
          {icon}
        </div>
      )}
      <p className="text-sm font-medium text-fg">{title}</p>
      {description && <p className="mt-1 max-w-sm text-xs leading-relaxed text-fg-subtle">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  message,
  onRetry,
  className,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div role="alert" className={cn("flex flex-col items-center justify-center px-6 py-12 text-center", className)}>
      <div className="mb-3 flex size-11 items-center justify-center rounded-xl border border-danger/30 bg-danger-subtle text-danger">
        !
      </div>
      <p className="text-sm font-medium text-fg">{title}</p>
      {message && <p className="mt-1 max-w-md text-xs leading-relaxed text-fg-muted">{message}</p>}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 rounded-sm border border-border px-3 py-1.5 text-xs font-medium text-fg transition-colors hover:bg-bg-hover"
        >
          Try again
        </button>
      )}
    </div>
  );
}
