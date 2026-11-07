import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { TriangleAlert } from "lucide-react";

interface State {
  error: Error | null;
}

/** Router-level error element: render errors and route load failures. */
export default class ErrorBoundary extends Component<{ children?: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("route error:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-dvh flex-col items-center justify-center gap-4 bg-bg px-6 text-center">
          <div className="flex size-12 items-center justify-center rounded-xl border border-warning/30 bg-warning-subtle text-warning">
            <TriangleAlert size={22} aria-hidden />
          </div>
          <div>
            <h1 className="text-base font-semibold text-fg">Something went wrong</h1>
            <p className="mt-1 max-w-md text-sm leading-relaxed text-fg-muted">
              {this.state.error.message || "An unexpected error occurred while loading this view."}
            </p>
          </div>
          <button
            type="button"
            onClick={() => window.location.assign("/chat")}
            className="rounded-md border border-border px-4 py-2 text-sm font-medium text-fg transition-colors hover:bg-bg-hover"
          >
            Back to workspace
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
