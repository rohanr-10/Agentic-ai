import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Play, ShieldCheck, Wrench } from "lucide-react";
import { get, post, type ApiError } from "@/services/api/client";
import type { ToolInfo, ToolInvokeOut } from "@/types/api";
import { PageShell } from "@/components/layout/AppLayout";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState, ErrorState, PageLoader } from "@/components/ui/State";
import { toast } from "@/components/ui/toast";
import { formatDuration } from "@/utils/format";

export default function ToolsPage() {
  const tools = useQuery({
    queryKey: ["tools"],
    queryFn: () => get<ToolInfo[]>("/tools"),
    staleTime: 30_000,
  });
  const [invokeTarget, setInvokeTarget] = useState<ToolInfo | null>(null);
  const [inputJson, setInputJson] = useState("");
  const [lastResult, setLastResult] = useState<ToolInvokeOut | null>(null);

  const invoke = useMutation({
    mutationFn: async () => {
      const parsed = JSON.parse(inputJson || "{}") as Record<string, unknown>;
      return post<ToolInvokeOut>(`/tools/${invokeTarget?.key}/execute`, { input: parsed });
    },
    onSuccess: (result) => {
      setLastResult(result);
      toast.success("Executed", `${result.tool} in ${formatDuration(result.duration_ms)}`);
    },
    onError: (err) => {
      const apiError = err as ApiError;
      toast.error("Execution refused", apiError.message);
      setLastResult(null);
    },
  });

  return (
    <PageShell title="Tools">
      <p className="border-b border-border/60 px-4 py-3 text-sm leading-relaxed text-fg-muted sm:px-6">
        The backend's registered tools and their policies. Only chat-safe tools can run directly
        here; sensitive and sandboxed tools run exclusively inside approved task steps.
      </p>

      {tools.isLoading ? (
        <PageLoader label="Loading tools…" />
      ) : tools.isError ? (
        <ErrorState
          title="Couldn't load tools"
          message={(tools.error as Error).message}
          onRetry={() => void tools.refetch()}
        />
      ) : (tools.data ?? []).length === 0 ? (
        <EmptyState className="pt-16" icon={<Wrench />} title="No tools registered" />
      ) : (
        <ul className="divide-y divide-border/60" aria-label="Tools">
          {(tools.data ?? []).map((tool) => {
            const directAllowed =
              !tool.requires_approval &&
              (tool.key === "document.extract" || tool.key === "data.analyze_csv" || tool.key === "http.request");
            return (
              <li key={tool.key} className="px-4 py-4 sm:px-6">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-fg">
                      {tool.name}
                      <span className="font-mono text-2xs text-fg-subtle">{tool.key}</span>
                      {!tool.is_enabled && <Badge tone="muted">disabled</Badge>}
                      {tool.allowed_in_chat ? (
                        <Badge tone="accent">chat-capable</Badge>
                      ) : (
                        <Badge tone="muted">task-only</Badge>
                      )}
                      {tool.requires_approval && <Badge tone="warning">approval-gated</Badge>}
                    </p>
                    <p className="mt-1 max-w-2xl text-xs leading-relaxed text-fg-muted">{tool.description}</p>
                    <p className="mt-1 flex flex-wrap items-center gap-x-2 text-2xs text-fg-subtle">
                      <span>{tool.category}</span>
                      <span aria-hidden>·</span>
                      <span>timeout {tool.default_timeout_s}s (max {tool.max_timeout_s}s)</span>
                      <span aria-hidden>·</span>
                      <span className="font-mono">{tool.permissions.join(" ") || "—"}</span>
                    </p>
                  </div>
                  {directAllowed && tool.is_enabled && (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => {
                        setInvokeTarget(tool);
                        setInputJson(prettifySchemaExample(tool));
                        setLastResult(null);
                      }}
                    >
                      <Play size={13} aria-hidden />
                      Run
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <Dialog
        open={invokeTarget !== null}
        onClose={() => setInvokeTarget(null)}
        title={`Run ${invokeTarget?.name ?? ""}`}
        description={
          invokeTarget
            ? `Direct execution through the backend tool endpoint. Input is schema-validated server-side.`
            : undefined
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setInvokeTarget(null)}>
              Close
            </Button>
            <Button
              variant="primary"
              loading={invoke.isPending}
              onClick={() => {
                try {
                  JSON.parse(inputJson || "{}");
                  invoke.mutate();
                } catch {
                  toast.error("Invalid JSON", "Check the input syntax.");
                }
              }}
            >
              Execute
            </Button>
          </>
        }
      >
        {invokeTarget && (
          <div className="space-y-4">
            <div>
              <p className="mb-1.5 text-xs font-medium text-fg-muted">Input (JSON)</p>
              <textarea
                value={inputJson}
                onChange={(e) => setInputJson(e.target.value)}
                spellCheck={false}
                aria-label="Tool input JSON"
                className="min-h-32 w-full rounded-md border border-border bg-[#0d0f13] p-3 font-mono text-xs leading-relaxed text-fg focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25"
              />
            </div>
            {lastResult && (
              <div>
                <p className="mb-1.5 flex items-center gap-2 text-xs font-medium text-fg-muted">
                  <ShieldCheck size={13} className="text-success" aria-hidden />
                  Result · {formatDuration(lastResult.duration_ms)} · execution {lastResult.tool_execution_id.slice(0, 8)}…
                </p>
                <pre className="max-h-64 overflow-auto rounded-md border border-border bg-[#0d0f13] p-3 font-mono text-2xs leading-relaxed text-fg-muted">
                  {JSON.stringify(lastResult.output, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )}
      </Dialog>
    </PageShell>
  );
}

function prettifySchemaExample(tool: ToolInfo): string {
  const properties = (tool.input_schema.properties ?? {}) as Record<string, { type?: string; description?: string }>;
  const example: Record<string, unknown> = {};
  for (const [key, spec] of Object.entries(properties)) {
    if (spec.type === "string") {
      example[key] = tool.key === "http.request" && key === "url" ? "https://example.com" : `text for ${key}`;
    } else if (spec.type === "number" || spec.type === "integer") {
      example[key] = 10;
    } else if (spec.type === "boolean") {
      example[key] = true;
    } else if (spec.type === "array") {
      example[key] = [];
    } else {
      example[key] = null;
    }
  }
  return JSON.stringify(example, null, 2);
}
