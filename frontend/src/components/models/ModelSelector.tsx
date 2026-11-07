import { Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, CircleAlert, Cpu } from "lucide-react";import type { ModelConfigOut } from "@/types/api";
import { get } from "@/services/api/client";
import { useModels } from "@/features/queries";
import { Dropdown } from "@/components/ui/Dropdown";
import { cn } from "@/utils/cn";

export type ModelChoice = { id: string | null; label: string; adapter: string };

/**
 * Model picker driven entirely by GET /models (never hardcoded). Disabled or
 * unhealthy models are listed but not selectable, with the reason shown.
 * Health is probed lazily per open model while the dropdown is open.
 */
export function ModelSelector({
  selectedId,
  onSelect,
  size = "sm",
  compact = false,
}: {
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  size?: "xs" | "sm";
  compact?: boolean;
}) {
  const models = useModels();
  const enabled = (models.data ?? []).filter((m) => m.is_enabled);
  const selected = enabled.find((m) => m.id === selectedId) ?? null;
  const label = selected
    ? selected.display_name
    : selectedId === null
      ? "Default model"
      : "Model unavailable";

  return (
    <Dropdown
      align="end"
      trigger={({ onClick, ref, "aria-expanded": expanded }) => (
        <button
          ref={ref}
          type="button"
          onClick={onClick}
          aria-expanded={expanded}
          aria-label="Select model"
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md border border-border bg-bg-input text-fg transition-colors hover:border-border-strong",
            size === "xs" ? "h-7 px-2.5 text-2xs" : "h-8 px-3 text-xs",
            compact && "max-w-44",
          )}
        >
          <Cpu size={13} className="shrink-0 text-accent" aria-hidden />
          <span className="min-w-0 truncate font-medium">{label}</span>
          <ChevronDown size={12} className="shrink-0 text-fg-subtle" aria-hidden />
        </button>
      )}
    >
      {(close) => (
        <div className="min-w-72">
          <p className="px-2.5 py-2 text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
            Model for this conversation
          </p>
          {models.isLoading && <p className="px-2.5 py-2 text-xs text-fg-subtle">Loading models…</p>}
          {models.isError && (
            <p className="flex items-center gap-2 px-2.5 py-2 text-xs text-danger">
              <CircleAlert size={13} aria-hidden /> Couldn’t load models
            </p>
          )}
          {models.data && (
            <>
              <button
                type="button"
                onClick={() => {
                  onSelect(null);
                  close();
                }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-sm px-2.5 py-1.5 text-left text-sm transition-colors hover:bg-accent-subtle hover:text-fg",
                  selectedId === null ? "text-fg" : "text-fg-muted",
                )}
              >
                <span className="flex-1">
                  <span className="block font-medium">Default model</span>
                  <span className="block text-2xs text-fg-subtle">
                    uses the backend default (marked on this list)
                  </span>
                </span>
                {selectedId === null && <Check size={14} className="text-accent" aria-hidden />}
              </button>
              {enabled.map((model) => (
                <ModelOption
                  key={model.id}
                  model={model}
                  selected={model.id === selectedId}
                  onPick={() => {
                    onSelect(model.id);
                    close();
                  }}
                />
              ))}
              {(models.data ?? []).some((m) => !m.is_enabled) && (
                <>
                  <p className="mt-1 border-t border-border px-2.5 pt-2 pb-1 text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
                    Disabled
                  </p>
                  {(models.data ?? [])
                    .filter((m) => !m.is_enabled)
                    .map((model) => (
                      <div
                        key={model.id}
                        className="flex items-center gap-2 rounded-sm px-2.5 py-1.5 text-sm text-fg-subtle"
                        title="This model is disabled on the backend"
                      >
                        <span className="flex-1 truncate">{model.display_name}</span>
                        <span className="text-2xs">disabled</span>
                      </div>
                    ))}
                </>
              )}
            </>
          )}
        </div>
      )}
    </Dropdown>
  );
}

function ModelOption({
  model,
  selected,
  onPick,
}: {
  model: ModelConfigOut;
  selected: boolean;
  onPick: () => void;
}) {
  const health = useQuery({
    queryKey: ["models", model.id, "health"],
    queryFn: () => get<{ healthy: boolean; detail: string }>(`/models/${model.id}/health`),
    staleTime: 15_000,
    retry: false,
  });
  const healthy = health.data?.healthy !== false; // unknown counts as OK
  const capabilities = [
    model.capabilities.tool_calls ? "tools" : null,
    model.capabilities.streaming ? "streaming" : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Fragment>
      <button
        type="button"
        onClick={onPick}
        className={cn(
          "flex w-full items-center gap-2 rounded-sm px-2.5 py-1.5 text-left text-sm transition-colors hover:bg-accent-subtle hover:text-fg",
          selected ? "text-fg" : "text-fg-muted",
        )}
        title={health.data ? health.data.detail : model.name}
      >
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate font-medium">{model.display_name}</span>
            {model.is_default && (
              <span className="shrink-0 rounded-full border border-accent/30 bg-accent-subtle px-1.5 text-[10px] leading-4 text-accent">
                default
              </span>
            )}
          </span>
          <span className="block truncate text-2xs text-fg-subtle">
            {capabilities || model.adapter_type} · ctx {formatCtx(model.context_window)}
          </span>
        </span>
        {!healthy && (
          <span className="shrink-0 text-2xs text-warning" title={health.data?.detail ?? "health unknown"}>
            degr.
          </span>
        )}
        {selected && healthy && <Check size={14} className="shrink-0 text-accent" aria-hidden />}
      </button>
    </Fragment>
  );
}

function formatCtx(ctx: number): string {
  return ctx >= 1024 ? `${Math.round(ctx / 1024)}K` : String(ctx);
}
