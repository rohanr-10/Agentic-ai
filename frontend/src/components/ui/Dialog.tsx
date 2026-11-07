import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/utils/cn";

export interface DialogProps {
  open: boolean;
  onClose: (reason: "backdrop" | "escape" | "button") => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
}

const SIZES = { sm: "max-w-md", md: "max-w-xl", lg: "max-w-3xl" } as const;

export function Dialog({ open, onClose, title, description, children, footer, size = "md" }: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose("escape");
      }
      if (event.key === "Tab" && panelRef.current) {
        // keep focus inside the dialog
        const focusables = panelRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (focusables.length === 0) return;
        const first = focusables[0]!;
        const last = focusables[focusables.length - 1]!;
        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        } else if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // focus the panel itself so immediate Esc works and focus starts inside
    const raf = requestAnimationFrame(() => {
      const target = panelRef.current?.querySelector<HTMLElement>("[data-autofocus]") ?? panelRef.current;
      target?.focus();
    });
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = previousOverflow;
      cancelAnimationFrame(raf);
    };
  }, [open, onClose]);

  if (!open) return null;
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-6"
      role="presentation"
    >
      <div
        className="absolute inset-0 bg-black/55 animate-[fade-in_120ms_ease-out]"
        onClick={() => onClose("backdrop")}
        aria-hidden
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={cn(
          "relative z-10 w-full rounded-t-xl border border-border bg-bg-panel shadow-[0_24px_80px_rgba(0,0,0,0.5)]",
          "outline-none animate-[dialog-in_180ms_var(--ease-out-soft)] sm:rounded-xl",
          SIZES[size],
        )}
      >
        <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-fg">{title}</h2>
            {description && <p className="mt-0.5 text-xs text-fg-muted">{description}</p>}
          </div>
          <button
            type="button"
            aria-label="Close dialog"
            onClick={() => onClose("button")}
            className="rounded-xs p-1 text-fg-subtle transition-colors hover:bg-bg-hover hover:text-fg"
          >
            <X size={16} />
          </button>
        </header>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-border px-5 py-3.5">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}
