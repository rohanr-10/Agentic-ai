import { forwardRef } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/utils/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type Size = "xs" | "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-bg font-medium hover:bg-accent-hover active:bg-accent/85 shadow-[0_1px_0_rgba(255,255,255,0.06)_inset]",
  secondary: "bg-bg-elevated border border-border text-fg hover:bg-bg-hover hover:border-border-strong",
  ghost: "text-fg-muted hover:bg-bg-hover hover:text-fg",
  danger: "bg-danger text-bg font-medium hover:brightness-110",
  outline: "border border-border text-fg hover:bg-bg-hover hover:border-border-strong",
};

const SIZES: Record<Size, string> = {
  xs: "h-6 px-2 text-2xs gap-1 rounded-xs",
  sm: "h-8 px-3 text-xs gap-1.5 rounded-sm",
  md: "h-9 px-3.5 text-sm gap-2 rounded-md",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  children?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "sm", loading = false, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex select-none items-center justify-center whitespace-nowrap transition-colors duration-100",
        "disabled:pointer-events-none disabled:opacity-45",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {loading && <Loader2 size={size === "md" ? 15 : 13} className="animate-spin" aria-hidden />}
      {children}
    </button>
  );
});
