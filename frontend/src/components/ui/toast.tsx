/**
 * Toast notifications — single container, no dependencies.
 * Usage: toast.error(title, description) / toast.success(...) / toast.info(...).
 */
import { createRoot, type Root } from "react-dom/client";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { CheckCircle2, Info, TriangleAlert, X, XCircle } from "lucide-react";
import { cn } from "@/utils/cn";

export interface ToastItem {
  id: number;
  kind: "success" | "error" | "warning" | "info";
  title: string;
  description?: string;
  actions?: Array<{ label: string; onClick: () => void; primary?: boolean }>;
}

type Listener = (items: ToastItem[]) => void;

const listeners = new Set<Listener>();
let items: ToastItem[] = [];
let nextId = 1;
let containerRoot: Root | null = null;
const DISMISS_AFTER_MS = 5200;

function emit() {
  for (const listener of listeners) listener([...items]);
}

function push(kind: ToastItem["kind"], title: string, description?: string, actions?: ToastItem["actions"]) {
  const id = nextId++;
  items = [...items, { id, kind, title, description, actions }];
  emit();
  if (kind === "error") {
    // errors persist until dismissed (they usually need user attention)
  } else {
    setTimeout(() => dismiss(id), DISMISS_AFTER_MS);
  }
  return id;
}

function dismiss(id: number) {
  const found = items.some((t) => t.id === id);
  if (!found) return;
  items = items.filter((t) => t.id !== id);
  emit();
}

export const toast = {
  success: (title: string, description?: string) => push("success", title, description),
  error: (title: string, description?: string) => push("error", title, description),
  warning: (title: string, description?: string) => push("warning", title, description),
  info: (title: string, description?: string) => push("info", title, description),
  dismiss,
};

const ICONS = {
  success: CheckCircle2,
  error: XCircle,
  warning: TriangleAlert,
  info: Info,
} as const;

const KIND_STYLES: Record<ToastItem["kind"], string> = {
  success: "text-success",
  error: "text-danger",
  warning: "text-warning",
  info: "text-info",
};

function ToastContainer({ current }: { current: ToastItem[] }) {
  return (
    <div
      aria-live="polite"
      role="status"
      className="pointer-events-none fixed inset-x-0 top-3 z-[80] flex flex-col items-center gap-2 px-3 sm:items-end sm:pr-4"
    >
      {current.map((item) => {
        const Icon = ICONS[item.kind];
        return (
          <div
            key={item.id}
            role="alert"
            className={cn(
              "pointer-events-auto w-full max-w-md rounded-lg border border-border bg-bg-elevated shadow-[0_12px_40px_rgba(0,0,0,0.45)]",
              "flex items-start gap-3 p-3.5 animate-[toast-in_160ms_var(--ease-out-soft)]",
            )}
          >
            <Icon size={18} aria-hidden className={cn("mt-0.5 shrink-0", KIND_STYLES[item.kind])} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-fg">{item.title}</p>
              {item.description && (
                <p className="mt-0.5 text-xs leading-relaxed text-fg-muted">{item.description}</p>
              )}
              {item.actions && item.actions.length > 0 && (
                <div className="mt-2 flex gap-2">
                  {item.actions.map((action) => (
                    <button
                      key={action.label}
                      type="button"
                      onClick={() => {
                        action.onClick();
                        dismiss(item.id);
                      }}
                      className={cn(
                        "rounded-sm px-2.5 py-1 text-xs font-medium transition-colors",
                        action.primary
                          ? "bg-accent text-bg hover:bg-accent-hover"
                          : "border border-border text-fg-muted hover:bg-bg-hover hover:text-fg",
                      )}
                    >
                      {action.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => dismiss(item.id)}
              className="rounded-xs p-1 text-fg-subtle transition-colors hover:text-fg"
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

let mounted = false;

/** Mount once from App; in tests it is a no-op (jsdom render via RTL). */
export function mountToastContainer(host: HTMLElement) {
  if (mounted || containerRoot) return;
  mounted = true;
  containerRoot = createRoot(host);
  const initial: ToastItem[] = [];
  const listener: Listener = (next) => {
    containerRoot?.render(<ToastContainer current={next} />);
  };
  listeners.add(listener);
  containerRoot.render(<ToastContainer current={initial} />);
}

/** React hook variant for component tests. */
export function useToasts(): ToastItem[] {
  const [current, setCurrent] = useState<ToastItem[]>(items);
  useEffect(() => {
    const listener: Listener = (next) => setCurrent(next);
    listeners.add(listener);
    listener([...items]);
    return () => {
      listeners.delete(listener);
    };
  }, []);
  return current;
}

/** Render helper for tests: wraps the container with a static list. */
export function ToastViewport({ current }: { current: ToastItem[] }): ReactNode {
  return <ToastContainer current={current} />;
}
