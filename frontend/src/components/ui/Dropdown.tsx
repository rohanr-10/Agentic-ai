import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { cn } from "@/utils/cn";

export interface DropdownProps {
  trigger: (props: { open: boolean; "aria-expanded": boolean; onClick: () => void; ref: React.Ref<HTMLButtonElement> }) => ReactNode;
  children: ReactNode | ((close: () => void) => ReactNode);
  align?: "start" | "end";
  /** Approximately the widest menu item in px; keeps the trigger layout stable. */
  contentWidth?: number;
}

/** Small popover menu with outside-click + Escape close and focus return. */
export function Dropdown({ trigger, children, align = "start" }: DropdownProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      {trigger({
        open,
        "aria-expanded": open,
        onClick: () => setOpen((v) => !v),
        ref: triggerRef,
      })}
      {open && (
        <div
          role="menu"
          className={cn(
            "absolute z-40 mt-1.5 min-w-52 rounded-lg border border-border bg-bg-elevated p-1.5 shadow-[0_16px_48px_rgba(0,0,0,0.45)]",
            "animate-[menu-in_120ms_var(--ease-out-soft)]",
            align === "end" ? "right-0" : "left-0",
          )}
        >
          {typeof children === "function" ? children(() => setOpen(false)) : children}
        </div>
      )}
    </div>
  );
}

export function MenuItem({
  icon,
  label,
  onSelect,
  danger,
  disabled,
  hint,
}: {
  icon?: ReactNode;
  label: ReactNode;
  onSelect?: () => void;
  danger?: boolean;
  disabled?: boolean;
  hint?: string;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onSelect}
      title={hint}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-sm px-2.5 py-1.5 text-left text-sm transition-colors",
        "disabled:pointer-events-none disabled:opacity-40",
        danger
          ? "text-danger/90 hover:bg-danger-subtle hover:text-danger"
          : "text-fg-muted hover:bg-accent-subtle hover:text-fg",
      )}
    >
      {icon && <span className="shrink-0 opacity-80 [&>svg]:size-4">{icon}</span>}
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </button>
  );
}

export function MenuSeparator() {
  return <div role="separator" className="my-1 h-px bg-border" />;
}
