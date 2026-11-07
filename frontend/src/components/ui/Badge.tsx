import type { ReactNode } from "react";
import { cn } from "@/utils/cn";

type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info" | "muted";

const TONES: Record<Tone, string> = {
  neutral: "bg-bg-elevated border-border text-fg-muted",
  accent: "bg-accent-subtle border-accent/30 text-accent",
  success: "bg-success-subtle border-success/30 text-success",
  warning: "bg-warning-subtle border-warning/30 text-warning",
  danger: "bg-danger-subtle border-danger/30 text-danger",
  info: "bg-accent-subtle/60 border-info/25 text-info",
  muted: "bg-transparent border-border/70 text-fg-subtle",
};

export function Badge({
  tone = "neutral",
  className,
  children,
  dot,
  title,
}: {
  tone?: Tone;
  className?: string;
  children: ReactNode;
  dot?: boolean;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs font-medium leading-4",
        TONES[tone],
        className,
      )}
    >
      {dot && <span aria-hidden className="size-1.5 rounded-full bg-current opacity-90" />}
      {children}
    </span>
  );
}
