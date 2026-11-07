import { Compass } from "lucide-react";

export default function NotFoundPage() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
      <div className="flex size-12 items-center justify-center rounded-xl border border-border bg-bg-elevated text-fg-subtle">
        <Compass size={22} aria-hidden />
      </div>
      <div>
        <h1 className="text-base font-semibold text-fg">Page not found</h1>
        <p className="mt-1 text-sm text-fg-muted">
          The view you requested does not exist. It may have been moved.
        </p>
      </div>
      <a
        href="/chat"
        className="rounded-md border border-border px-4 py-2 text-sm font-medium text-fg transition-colors hover:bg-bg-hover"
      >
        Back to workspace
      </a>
    </div>
  );
}
