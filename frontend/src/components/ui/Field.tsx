import { forwardRef, useId } from "react";
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { cn } from "@/utils/cn";

const BASE =
  "w-full bg-bg-input border border-border rounded-md text-sm text-fg placeholder:text-fg-subtle " +
  "transition-colors hover:border-border-strong focus:border-accent focus:outline-none focus-visible:outline-none " +
  "focus:ring-2 focus:ring-accent/25 disabled:opacity-50";

export interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string | null;
  /** Extra classes for the inner <input> (e.g. icon padding). */
  inputClassName?: string;
  /** Decorative icon rendered inside the field on the left. */
  leftIcon?: ReactNode;
}

export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(function TextField(
  { label, hint, error, className, inputClassName, leftIcon, id, ...rest },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? autoId;
  return (
    <div className={cn("w-full", className)}>
      {label && (
        <label htmlFor={inputId} className="mb-1.5 block text-xs font-medium text-fg-muted">
          {label}
        </label>
      )}
      <div className="relative">
        {leftIcon && (
          <span
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-subtle [&>svg]:size-4"
          >
            {leftIcon}
          </span>
        )}
        <input
          ref={ref}
          id={inputId}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined}
          className={cn(
            BASE,
            "h-9 px-3",
            leftIcon && "pl-9",
            inputClassName,
            error && "border-danger focus:border-danger focus:ring-danger/25",
          )}
          {...rest}
        />
      </div>
      {error ? (
        <p id={`${inputId}-error`} className="mt-1.5 text-xs text-danger">
          {error}
        </p>
      ) : hint ? (
        <p id={`${inputId}-hint`} className="mt-1.5 text-xs text-fg-subtle">
          {hint}
        </p>
      ) : null}
    </div>
  );
});

export interface SelectFieldProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  hint?: string;
  options: Array<{ value: string; label: string }>;
}

export const SelectField = forwardRef<HTMLSelectElement, SelectFieldProps>(function SelectField(
  { label, hint, options, className, id, ...rest },
  ref,
) {
  const autoId = useId();
  const selectId = id ?? autoId;
  return (
    <div className={cn("w-full", className)}>
      {label && (
        <label htmlFor={selectId} className="mb-1.5 block text-xs font-medium text-fg-muted">
          {label}
        </label>
      )}
      <select
        ref={ref}
        id={selectId}
        className={cn(BASE, "h-9 cursor-pointer px-3")}
        {...rest}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {hint && <p className="mt-1.5 text-xs text-fg-subtle">{hint}</p>}
    </div>
  );
});

export interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  hint?: string;
  error?: string | null;
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  { label, hint, error, className, id, ...rest },
  ref,
) {
  const autoId = useId();
  const areaId = id ?? autoId;
  return (
    <div className={cn("w-full", className)}>
      {label && (
        <label htmlFor={areaId} className="mb-1.5 block text-xs font-medium text-fg-muted">
          {label}
        </label>
      )}
      <textarea
        ref={ref}
        id={areaId}
        aria-invalid={error ? true : undefined}
        className={cn(
          BASE,
          "min-h-20 px-3 py-2 leading-relaxed",
          error && "border-danger focus:border-danger focus:ring-danger/25",
        )}
        {...rest}
      />
      {error ? (
        <p className="mt-1.5 text-xs text-danger">{error}</p>
      ) : hint ? (
        <p className="mt-1.5 text-xs text-fg-subtle">{hint}</p>
      ) : null}
    </div>
  );
});
