import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CircleAlert,
  Cpu,
  Database,
  Plus,
  RefreshCw,
  Settings2,
  Wrench,
} from "lucide-react";
import { get, post, patch, type ApiError } from "@/services/api/client";
import type { ModelConfigOut, ModelHealthOut } from "@/types/api";
import { PageShell } from "@/components/layout/AppLayout";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField, TextField } from "@/components/ui/Field";
import { EmptyState, ErrorState, PageLoader } from "@/components/ui/State";
import { toast } from "@/components/ui/toast";
import { useAuth } from "@/features/auth/useAuth";
import { useModels } from "@/features/queries";
import { formatDateTime } from "@/utils/format";

export default function ModelsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const models = useModels();
  const queryClient = useQueryClient();
  const [manageOpen, setManageOpen] = useState(false);
  const [editing, setEditing] = useState<ModelConfigOut | null>(null);

  if (models.isLoading) return <PageLoader label="Loading models…" />;
  if (models.isError) {
    return (
      <PageShell title="Models">
        <ErrorState
          title="Couldn't load models"
          message={(models.error as Error).message}
          onRetry={() => void models.refetch()}
        />
      </PageShell>
    );
  }

  const list = models.data ?? [];

  return (
    <PageShell
      title="Models"
      actions={
        <>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void queryClient.invalidateQueries({ queryKey: ["models"] })}
            aria-label="Refresh models"
          >
            <RefreshCw size={14} aria-hidden />
            <span className="hidden sm:inline">Refresh</span>
          </Button>
          {isAdmin && (
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                setEditing(null);
                setManageOpen(true);
              }}
            >
              <Plus size={14} aria-hidden />
              Register model
            </Button>
          )}
        </>
      }
    >
      <p className="border-b border-border/60 px-4 py-3 text-sm leading-relaxed text-fg-muted sm:px-6">
        Models are served through the backend gateway. The list comes from the backend's model API —
        including adapter, context window and capability flags. Health is probed live.
      </p>
      {list.length === 0 ? (
        <EmptyState
          className="pt-20"
          icon={<Cpu />}
          title="No models registered"
          description={
            isAdmin
              ? "Register a model to make it available in conversations."
              : "An administrator has to register at least one model."
          }
        />
      ) : (
        <ul className="divide-y divide-border/60">
          {list.map((model) => (
            <ModelRow key={model.id} model={model} onEdit={setEditing} onOpenManage={setManageOpen} />
          ))}
        </ul>
      )}

      {isAdmin && (
        <ModelManageDialog
          key={editing?.id ?? "create"}
          open={manageOpen}
          editing={editing}
          onClose={() => setManageOpen(false)}
          onSaved={() => {
            setManageOpen(false);
            void queryClient.invalidateQueries({ queryKey: ["models"] });
          }}
        />
      )}
    </PageShell>
  );
}

function ModelRow({
  model,
  onEdit,
  onOpenManage,
}: {
  model: ModelConfigOut;
  onEdit: (m: ModelConfigOut) => void;
  onOpenManage: (open: boolean) => void;
}) {
  const health = useQuery({
    queryKey: ["models", model.id, "health"],
    queryFn: () => get<ModelHealthOut>(`/models/${model.id}/health`),
    retry: false,
    staleTime: 10_000,
  });
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  return (
    <li className="px-4 py-4 sm:px-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-fg">
            {model.display_name}
            {model.is_default && <Badge tone="accent">default</Badge>}
            {!model.is_enabled && <Badge tone="muted">disabled</Badge>}
            <span className="font-mono text-2xs text-fg-subtle">{model.name}</span>
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-fg-subtle">
            <span className="inline-flex items-center gap-1">
              <Database size={11} aria-hidden /> {model.adapter_type}
            </span>
            <span>ctx {model.context_window.toLocaleString()}</span>
            <span>max out {model.max_output_tokens.toLocaleString()}</span>
            {model.capabilities.tool_calls && (
              <span className="inline-flex items-center gap-1">
                <Wrench size={11} aria-hidden /> tool calls
              </span>
            )}
            {model.capabilities.streaming && <span>streaming</span>}
          </p>
          {model.endpoint && (
            <p className="mt-1 truncate font-mono text-2xs text-fg-subtle" title={model.endpoint}>
              {model.endpoint}
              {model.model_id ? ` · ${model.model_id}` : ""}
            </p>
          )}
          <p className="mt-1 text-2xs text-fg-subtle">
            updated {formatDateTime(model.updated_at)}
            {model.api_key && <span> · key {model.api_key}</span>}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {health.isLoading ? (
            <Badge tone="muted">probing…</Badge>
          ) : health.isError ? (
            <Badge tone="danger" title={(health.error as Error).message}>
              probe failed
            </Badge>
          ) : health.data ? (
            <Badge tone={health.data.healthy ? "success" : "warning"} title={health.data.detail}>
              {health.data.healthy ? "healthy" : "unreachable"}
            </Badge>
          ) : null}
          {isAdmin && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                onEdit(model);
                onOpenManage(true);
              }}
            >
              <Settings2 size={13} aria-hidden />
              Edit
            </Button>
          )}
        </div>
      </div>
    </li>
  );
}

interface ModelForm {
  name: string;
  display_name: string;
  adapter_type: "test" | "openai_compatible";
  endpoint: string;
  model_id: string;
  context_window: string;
  max_output_tokens: string;
  api_key: string;
}

const EMPTY_FORM: ModelForm = {
  name: "",
  display_name: "",
  adapter_type: "openai_compatible",
  endpoint: "",
  model_id: "",
  context_window: "8192",
  max_output_tokens: "2048",
  api_key: "",
};

function ModelManageDialog({
  open,
  editing,
  onClose,
  onSaved,
}: {
  open: boolean;
  editing: ModelConfigOut | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  // The parent renders this dialog fresh per target (keyed remount below),
  // so the form initialises directly from `editing` — no syncing effect.
  const [form, setForm] = useState<ModelForm>(() =>
    editing
      ? {
          name: editing.name,
          display_name: editing.display_name,
          adapter_type: editing.adapter_type,
          endpoint: editing.endpoint ?? "",
          model_id: editing.model_id ?? "",
          context_window: String(editing.context_window),
          max_output_tokens: String(editing.max_output_tokens),
          api_key: "",
        }
      : EMPTY_FORM,
  );

  const mutation = useMutation({
    mutationFn: async () => {
      const common = {
        display_name: form.display_name.trim(),
        endpoint: form.endpoint.trim() || null,
        model_id: form.model_id.trim() || null,
        context_window: Number(form.context_window) || 8192,
        max_output_tokens: Number(form.max_output_tokens) || 2048,
        ...(form.api_key.trim() ? { api_key: form.api_key.trim() } : {}),
      };
      if (editing) {
        return patch<ModelConfigOut>(`/models/${editing.id}`, common);
      }
      return post<ModelConfigOut>("/models", {
        ...common,
        name: form.name.trim(),
        adapter_type: form.adapter_type,
      });
    },
    onSuccess: (saved) => {
      toast.success(editing ? "Model updated" : "Model registered", saved.display_name);
      void queryClient.invalidateQueries({ queryKey: ["models"] });
      onSaved();
    },
    onError: (err) => {
      const apiError = err as ApiError;
      setError(apiError.message || "Save failed");
    },
  });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={editing ? `Edit ${editing.display_name}` : "Register a model"}
      description={
        editing
          ? "Changes apply to new requests immediately (adapter cache is invalidated)."
          : "openai_compatible models point at a /chat/completions runtime (Ollama, vLLM…)."
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={mutation.isPending}
            onClick={() => {
              setError(null);
              if (!form.display_name.trim()) {
                setError("Display name is required.");
                return;
              }
              if (!editing && !/^[a-zA-Z0-9._-]+$/.test(form.name.trim())) {
                setError("Name may only contain letters, digits, dots, dashes and underscores.");
                return;
              }
              if (form.adapter_type === "openai_compatible" && !form.endpoint.trim() && !(editing?.endpoint && !form.endpoint.trim())) {
                if (!editing) {
                  setError("openai_compatible adapters require an endpoint.");
                  return;
                }
              }
              mutation.mutate();
            }}
          >
            {editing ? "Save changes" : "Register"}
          </Button>
        </>
      }
    >
      {error && (
        <p role="alert" className="mb-3 flex items-center gap-2 rounded-md border border-danger/30 bg-danger-subtle px-3 py-2 text-xs text-danger">
          <CircleAlert size={14} aria-hidden /> {error}
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        {!editing && (
          <>
            <TextField
              label="Name (unique key)"
              placeholder="sparkx-local"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              hint="Letters, digits, . _ -"
            />
            <SelectField
              label="Adapter"
              value={form.adapter_type}
              onChange={(e) =>
                setForm({ ...form, adapter_type: e.target.value as typeof form.adapter_type })
              }
              hint="test = deterministic in-process adapter"
              options={[
                { value: "openai_compatible", label: "openai_compatible" },
                { value: "test", label: "test (deterministic)" },
              ]}
            />
          </>
        )}
        <TextField
          label="Display name"
          placeholder="SparkX 2.6 4B — Local Ollama"
          value={form.display_name}
          onChange={(e) => setForm({ ...form, display_name: e.target.value })}
        />
        <TextField
          label="Endpoint"
          placeholder="http://127.0.0.1:11434/v1"
          value={form.endpoint}
          onChange={(e) => setForm({ ...form, endpoint: e.target.value })}
          hint="Base URL; /chat/completions is appended"
        />
        <TextField
          label="Model id"
          placeholder="SparkLLM/Spark-X2.6-4B:q4_k_m"
          value={form.model_id}
          onChange={(e) => setForm({ ...form, model_id: e.target.value })}
        />
        <TextField
          label="API key (optional)"
          placeholder="empty for local runtimes"
          value={form.api_key}
          onChange={(e) => setForm({ ...form, api_key: e.target.value })}
          hint={editing ? "leave empty to keep the current key" : undefined}
        />
        <TextField
          label="Context window"
          inputMode="numeric"
          value={form.context_window}
          onChange={(e) => setForm({ ...form, context_window: e.target.value })}
        />
        <TextField
          label="Max output tokens"
          inputMode="numeric"
          value={form.max_output_tokens}
          onChange={(e) => setForm({ ...form, max_output_tokens: e.target.value })}
        />
      </div>
      <p className="mt-4 text-2xs leading-relaxed text-fg-subtle">
        Enable/disable and default-flag changes go through the same endpoint; the backend clears the
        previous default when a new one is set. The API key is stored server-side and only shown
        masked.
      </p>
    </Dialog>
  );
}
