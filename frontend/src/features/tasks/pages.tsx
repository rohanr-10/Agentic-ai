import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";
import {
  CircleAlert,
  CircleCheckBig,
  Clock,
  Loader2,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Square,
  Trash2,
  TriangleAlert,
  ListChecks,
} from "lucide-react";
import { get, post, del, type ApiError } from "@/services/api/client";
import type { TaskOut, TaskDetailOut, TaskStatus, UUID } from "@/types/api";
import { PageShell } from "@/components/layout/AppLayout";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField, TextArea, TextField } from "@/components/ui/Field";
import { EmptyState, ErrorState, PageLoader } from "@/components/ui/State";
import { toast } from "@/components/ui/toast";
import { useTasks } from "@/features/queries";
import { formatRelative } from "@/utils/format";
import { cn } from "@/utils/cn";

type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info" | "muted";

const STATUS_TONES: Record<TaskStatus, Tone> = {
  created: "neutral",
  queued: "neutral",
  planning: "info",
  awaiting_approval: "warning",
  ready: "info",
  running: "accent",
  paused: "warning",
  retrying: "info",
  verifying: "info",
  completed: "success",
  failed: "danger",
  cancelled: "muted",
};

export const statusTone = (status: TaskStatus): Tone => STATUS_TONES[status];

export const statusLabel = (status: TaskStatus): string =>
  status.replace(/_/g, " ");


// --------------------------------------------------------------------- list
export default function TasksPage() {
  const [statusFilter, setStatusFilter] = useState<TaskStatus | "">("");
  const [createOpen, setCreateOpen] = useState(false);
  const tasks = useTasks(statusFilter ? { status: statusFilter } : undefined);
  const navigate = useNavigate();

  return (
    <PageShell
      title="Tasks"
      actions={
        <>
          <SelectField
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as TaskStatus | "")}
            aria-label="Filter by status"
            className="w-40"
            options={[
              { value: "", label: "All statuses" },
              ...(
                [
                  "created",
                  "queued",
                  "planning",
                  "awaiting_approval",
                  "ready",
                  "running",
                  "paused",
                  "retrying",
                  "verifying",
                  "completed",
                  "failed",
                  "cancelled",
                ] as TaskStatus[]
              ).map((s) => ({ value: s, label: statusLabel(s) })),
            ]}
          />
          <Button variant="primary" onClick={() => setCreateOpen(true)}>
            <Plus size={14} aria-hidden />
            New task
          </Button>
        </>
      }
    >
      <p className="border-b border-border/60 px-4 py-3 text-sm leading-relaxed text-fg-muted sm:px-6">
        Autonomous objectives executed by the backend orchestrator: plan → approval → sandboxed
        steps → verification. Every state below comes from the backend task API.
      </p>

      {tasks.isLoading ? (
        <PageLoader label="Loading tasks…" />
      ) : tasks.isError ? (
        <ErrorState
          title="Couldn't load tasks"
          message={(tasks.error as Error).message}
          onRetry={() => void tasks.refetch()}
        />
      ) : (tasks.data?.items.length ?? 0) === 0 ? (
        <EmptyState
          className="pt-20"
          icon={<ListChecks />}
          title={statusFilter ? "No tasks with this status" : "No tasks yet"}
          description={
            statusFilter
              ? "Try another status or clear the filter."
              : "Create a task with an objective; the orchestrator will plan and (if needed) ask for approval."
          }
          action={
            <Button variant="primary" onClick={() => setCreateOpen(true)}>
              <Plus size={14} aria-hidden /> New task
            </Button>
          }
        />
      ) : (
        <ul className="divide-y divide-border/60" aria-label="Tasks">
          {(tasks.data?.items ?? []).map((task) => (
            <li key={task.id} className="px-4 py-3.5 transition-colors hover:bg-bg-hover/40 sm:px-6">
              <Link
                to={`/tasks/${task.id}`}
                className="flex items-center gap-3"
                aria-label={`Open task ${task.title}`}
              >
                <TaskStatusIcon status={task.status} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-fg">{task.title}</span>
                    <Badge tone={statusTone(task.status)}>{statusLabel(task.status)}</Badge>
                  </span>
                  <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-2xs text-fg-subtle">
                    <span className="max-w-xl truncate">{task.objective}</span>
                  </span>
                  <span className="mt-0.5 flex items-center gap-x-2 text-2xs text-fg-subtle">
                    <span>{formatRelative(task.created_at)}</span>
                    <span aria-hidden>·</span>
                    <span>
                      {(task.usage.steps_executed ?? 0)} steps · {(task.usage.tool_calls ?? 0)} tool calls
                    </span>
                    {task.conversation_id && (
                      <>
                        <span aria-hidden>·</span>
                        <span className="text-accent-hover">linked conversation</span>
                      </>
                    )}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}

      <CreateTaskDialog open={createOpen} onClose={() => setCreateOpen(false)} onCreated={(id) => navigate(`/tasks/${id}`)} />
    </PageShell>
  );
}

function TaskStatusIcon({ status, size = 16 }: { status: TaskStatus; size?: number }) {
  const cls = cn("shrink-0", {
    "text-accent animate-pulse": status === "running" || status === "planning" || status === "verifying",
    "text-warning": status === "awaiting_approval" || status === "paused",
    "text-success": status === "completed",
    "text-danger": status === "failed",
    "text-fg-subtle": ["created", "queued", "ready", "cancelled", "retrying"].includes(status),
  });
  if (status === "completed") return <CircleCheckBig size={size} className={cn(cls, "text-success")} aria-hidden />;
  if (status === "failed") return <CircleAlert size={size} className={cn(cls, "text-danger")} aria-hidden />;
  if (status === "awaiting_approval") return <TriangleAlert size={size} className={cn(cls, "text-warning")} aria-hidden />;
  if (status === "running" || status === "planning" || status === "verifying" || status === "retrying")
    return <Loader2 size={size} className={cn(cls, "animate-spin")} aria-hidden />;
  return <Clock size={size} className={cls} aria-hidden />;
}

// ---------------------------------------------------------------- creation
function CreateTaskDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: UUID) => void;
}) {
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [priority, setPriority] = useState("5");
  const [deadline, setDeadline] = useState("1800");
  const [maxSteps, setMaxSteps] = useState("50");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const create = useMutation({
    mutationFn: () =>
      post<TaskDetailOut>("/tasks", {
        title: title.trim(),
        objective: objective.trim(),
        priority: Number(priority) || 5,
        auto_start: true,
        budgets: {
          deadline_s: Number(deadline) || 1800,
          max_steps: Number(maxSteps) || undefined,
        },
      }),
    onSuccess: (task) => {
      toast.success("Task created", "The orchestrator is planning. Approvals appear under Approvals.");
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      onCreated(task.id);
      setTitle("");
      setObjective("");
      onClose();
    },
    onError: (err) => setError((err as ApiError).message || "Creation failed"),
  });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New autonomous task"
      description="State a concrete objective. The model proposes a plan; you approve it before anything executes."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            onClick={() => {
              setError(null);
              if (!title.trim() || !objective.trim()) {
                setError("Title and objective are required.");
                return;
              }
              create.mutate();
            }}
          >
            Create and start
          </Button>
        </>
      }
    >
      {error && (
        <p role="alert" className="mb-3 rounded-md border border-danger/30 bg-danger-subtle px-3 py-2 text-xs text-danger">
          {error}
        </p>
      )}
      <div className="space-y-4">
        <TextField
          label="Title"
          placeholder="Summarise the uploaded CSV"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={200}
        />
        <TextArea
          label="Objective"
          placeholder="Read the attached sales.csv, compute monthly totals, and write a summary to the workspace."
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          maxLength={20_000}
          className="[&_textarea]:min-h-28"
        />
        <div className="grid grid-cols-3 gap-3">
          <TextField
            label="Priority (1–9)"
            inputMode="numeric"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          />
          <TextField
            label="Deadline (s)"
            inputMode="numeric"
            value={deadline}
            onChange={(e) => setDeadline(e.target.value)}
          />
          <TextField
            label="Max steps"
            inputMode="numeric"
            value={maxSteps}
            onChange={(e) => setMaxSteps(e.target.value)}
          />
        </div>
        <p className="text-2xs leading-relaxed text-fg-subtle">
          Without an approved plan nothing runs: sandboxed tools, budgets and approvals are enforced
          by the backend, not by this interface.
        </p>
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------- detail
export function TaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const task = useQuery({
    queryKey: ["tasks", taskId],
    queryFn: () => get<TaskDetailOut>(`/tasks/${taskId}`),
    enabled: taskId !== undefined,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ["created", "queued", "planning", "awaiting_approval", "ready", "running", "paused", "retrying", "verifying"].includes(status)
        ? 1500
        : false;
    },
  });
  const executions = useQuery({
    queryKey: ["tasks", taskId, "executions"],
    // the backend returns a bare array (not a Page envelope) for this route
    queryFn: () => get<TaskExecutionRow[]>(`/tasks/${taskId}/executions`, { limit: 500 }),
    enabled: taskId !== undefined,
    refetchInterval: 4000,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["tasks", taskId] });
    void queryClient.invalidateQueries({ queryKey: ["tasks", taskId, "executions"] });
    void queryClient.invalidateQueries({ queryKey: ["tasks"] });
  };

  const control = useMutation({
    mutationFn: async (action: "pause" | "resume" | "cancel") => {
      const response = await post<TaskOut>(`/tasks/${taskId}/${action}`);
      return response;
    },
    onSuccess: (_data, action) => {
      toast.success(`Task ${action}d`);
      invalidate();
    },
    onError: (err) => toast.error("Control failed", (err as ApiError).message),
  });

  const remove = useMutation({
    mutationFn: () => del(`/tasks/${taskId}`),
    onSuccess: () => {
      toast.success("Task deleted");
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      navigate("/tasks", { replace: true });
    },
    onError: (err) => toast.error("Delete failed", (err as ApiError).message),
  });

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const addNote = useMutation({
    mutationFn: () => post<TaskOut>(`/tasks/${taskId}/notes`, { content: note.trim() }),
    onSuccess: () => {
      toast.success("Instruction sent", "The orchestrator will incorporate it.");
      setNote("");
      setNoteOpen(false);
      invalidate();
    },
    onError: (err) => toast.error("Couldn't add note", (err as ApiError).message),
  });

  if (!taskId) return null;
  if (task.isLoading) return <PageLoader label="Loading task…" />;
  if (task.isError) {
    return (
      <PageShell title="Task">
        <ErrorState
          title="Task unavailable"
          message={(task.error as Error).message}
          onRetry={() => void task.refetch()}
        />
      </PageShell>
    );
  }

  const data = task.data!;
  const terminal = ["completed", "failed", "cancelled"].includes(data.status);
  const active = !terminal;
  const pendingApproval = data.status === "awaiting_approval";

  return (
    <PageShell
      title={data.title}
      actions={
        <>
          {active && !pendingApproval && (
            <Button variant="secondary" onClick={() => control.mutate("pause")} loading={control.isPending && control.variables === "pause"}>
              <Pause size={13} aria-hidden /> Pause
            </Button>
          )}
          {data.status === "paused" && (
            <Button variant="secondary" onClick={() => control.mutate("resume")} loading={control.isPending && control.variables === "resume"}>
              <Play size={13} aria-hidden /> Resume
            </Button>
          )}
          {active && (
            <>
              <Button variant="secondary" onClick={() => setNoteOpen(true)}>
                <Plus size={13} aria-hidden /> Add instruction
              </Button>
              <Button variant="danger" onClick={() => control.mutate("cancel")} loading={control.isPending && control.variables === "cancel"}>
                <Square size={13} aria-hidden /> Cancel
              </Button>
            </>
          )}
          {terminal && (
            <Button variant="ghost" onClick={() => setDeleteOpen(true)} className="text-fg-muted hover:text-danger">
              <Trash2 size={13} aria-hidden /> Delete
            </Button>
          )}
          <Button variant="ghost" onClick={invalidate} aria-label="Refresh task">
            <RefreshCw size={14} aria-hidden />
          </Button>
        </>
      }
    >
      <div className="mx-auto max-w-3xl space-y-6 px-4 py-5 sm:px-6">
        {/* status overview */}
        <section className="rounded-xl border border-border bg-bg-panel p-4" aria-label="Task overview">
          <p className="flex flex-wrap items-center gap-2">
            <Badge tone={statusTone(data.status)} dot>
              {statusLabel(data.status)}
            </Badge>
            {data.cancel_requested && <Badge tone="warning">cancel requested</Badge>}
            {data.pause_requested && <Badge tone="warning">pause requested</Badge>}
            <span className="text-2xs text-fg-subtle">v{data.version} · plan v{data.plan_version}</span>
          </p>
          <p className="mt-3 text-sm leading-relaxed text-fg-muted">{data.objective}</p>
          {pendingApproval && (
            <div className="mt-3 flex items-center justify-between gap-3 rounded-md border border-warning/30 bg-warning-subtle px-3 py-2.5">
              <p className="text-xs text-warning">
                Waiting for your approval — open{" "}
                <Link to="/approvals" className="font-medium underline underline-offset-2">
                  Approvals
                </Link>
                .
              </p>
            </div>
          )}
          {data.error && (
            <div role="alert" className="mt-3 rounded-md border border-danger/30 bg-danger-subtle px-3 py-2.5 text-xs text-danger">
              <span className="font-semibold">{data.error.code}:</span> {data.error.message}
            </div>
          )}
          {data.result_summary && (
            <div className="mt-3 rounded-md border border-success/25 bg-success-subtle px-3 py-2.5 text-xs leading-relaxed text-success">
              <span className="font-semibold">Result:</span> {data.result_summary}
            </div>
          )}
        </section>

        {/* plan */}
        <PlanTimeline data={data} />

        {/* executions */}
        <section aria-label="Execution history">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-fg">
            <Clock size={14} className="text-fg-subtle" aria-hidden />
            Execution history
          </h2>
          {executions.isLoading ? (
            <InlineLoaderRow />
          ) : (executions.data?.length ?? 0) === 0 ? (
            <p className="rounded-lg border border-border/70 bg-bg-elevated/40 px-4 py-6 text-center text-xs text-fg-subtle">
              No step has run yet. Executions appear here as the orchestrator works.
            </p>
          ) : (
            <ol className="relative space-y-2.5 border-l border-border pl-4">
              {(executions.data ?? []).map((execution) => (
                <li key={execution.id} className="relative rounded-md border border-border bg-bg-elevated/50 p-3">
                  <span
                    aria-hidden
                    className="absolute -left-[21px] top-4 size-2 rounded-full border-2 border-bg-panel"
                    style={{ background: EXECUTION_COLOR[execution.status] ?? "#646c78" }}
                  />
                  <p className="flex flex-wrap items-center gap-2 text-xs text-fg">
                    <span className="font-mono text-2xs text-accent-hover">{execution.step_id}</span>
                    <span aria-hidden>·</span>
                    <span className="text-fg-subtle">attempt {execution.attempt}</span>
                    <Badge tone={execution.status === "succeeded" ? "success" : execution.status === "running" ? "accent" : execution.status === "failed" ? "danger" : "muted"}>
                      {execution.status}
                    </Badge>
                    {execution.duration_ms != null && (
                      <span className="text-2xs text-fg-subtle">{(execution.duration_ms / 1000).toFixed(1)}s</span>
                    )}
                  </p>
                  {execution.input && (
                    <details className="mt-2">
                      <summary className="cursor-pointer text-2xs text-fg-subtle hover:text-fg-muted">
                        input / output
                      </summary>
                      <pre className="mt-1.5 max-h-48 overflow-auto rounded-md border border-border bg-[#0d0f13] p-2.5 font-mono text-2xs leading-relaxed text-fg-muted">
{JSON.stringify({ input: execution.input, output: execution.output ?? execution.error }, null, 2)}
                      </pre>
                    </details>
                  )}
                </li>
              ))}
            </ol>
          )}
        </section>

        {/* usage & budgets */}
        <section aria-label="Usage and budgets" className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Steps executed" value={String(data.usage.steps_executed ?? 0)} hint={`max ${data.budgets.max_steps ?? "—"}`} />
          <Stat label="Tool calls" value={String(data.usage.tool_calls ?? 0)} hint={`max ${data.budgets.max_tool_calls ?? "—"}`} />
          <Stat label="Plan revisions" value={String(data.usage.revisions ?? 0)} hint={`max ${data.budgets.max_plan_revisions ?? "—"}`} />
          <Stat label="Deadline" value={data.deadline_at ? formatRelative(data.deadline_at) : "—"} hint={data.finished_at ? `finished ${formatRelative(data.finished_at)}` : undefined} />
        </section>
      </div>

      {/* add-instruction dialog */}
      <Dialog
        open={noteOpen}
        onClose={() => setNoteOpen(false)}
        title="Add instruction"
        description="Injected into the running task's context (hybrid mode)."
        footer={
          <>
            <Button variant="ghost" onClick={() => setNoteOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={addNote.isPending}
              disabled={note.trim().length === 0}
              onClick={() => addNote.mutate()}
            >
              Send
            </Button>
          </>
        }
      >
        <TextArea
          autoFocus
          label="Instruction"
          placeholder="Focus only on Q3 data and include a chart."
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={4000}
        />
      </Dialog>

      <Dialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title="Delete this task?"
        description="Removes the task and its history. Terminal tasks only."
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleteOpen(false)}>
              Keep it
            </Button>
            <Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-fg-muted">
          “{data.title}” and all execution records will be removed.
        </p>
      </Dialog>
    </PageShell>
  );
}

interface TaskExecutionRow {
  id: UUID;
  step_id: string;
  attempt: number;
  status: "pending" | "running" | "succeeded" | "failed" | "skipped" | "cancelled";
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  error: { code: string; message: string } | null;
  duration_ms: number | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

const EXECUTION_COLOR: Record<string, string> = {
  succeeded: "#35c48f",
  running: "#4c8dff",
  failed: "#ef6b6b",
  cancelled: "#646c78",
  pending: "#9aa3af",
  skipped: "#646c78",
};

function InlineLoaderRow() {
  return (
    <p role="status" className="px-1 py-4 text-xs text-fg-subtle">
      Loading executions…
    </p>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border bg-bg-panel px-3.5 py-3">
      <p className="text-2xs uppercase tracking-wide text-fg-subtle">{label}</p>
      <p className="mt-1 text-sm font-semibold text-fg">{value}</p>
      {hint && <p className="text-2xs text-fg-subtle">{hint}</p>}
    </div>
  );
}

function PlanTimeline({ data }: { data: TaskDetailOut }) {
  const plan = data.plan;
  const completed = useMemo(
    () => new Set(Object.keys((data.checkpoint?.completed ?? {}) as Record<string, unknown>)),
    [data.checkpoint],
  );
  const approved = useMemo(
    () => new Set(data.checkpoint?.approved_steps ?? []),
    [data.checkpoint],
  );

  if (!plan || plan.steps.length === 0) {
    return (
      <section aria-label="Plan">
        <h2 className="mb-3 text-sm font-semibold text-fg">Plan</h2>
        <p className="rounded-lg border border-border/70 bg-bg-elevated/40 px-4 py-6 text-center text-xs text-fg-subtle">
          No plan yet — the orchestrator composes one when the task starts (model-planned tasks),
          or the plan awaits approval.
        </p>
      </section>
    );
  }

  return (
    <section aria-label="Plan">
      <h2 className="mb-1 text-sm font-semibold text-fg">
        Plan <span className="font-normal text-fg-subtle">v{data.plan_version} · {plan.steps.length} steps</span>
      </h2>
      {plan.objective_restated && (
        <p className="mb-3 text-xs italic leading-relaxed text-fg-muted">{plan.objective_restated}</p>
      )}
      <ol className="relative space-y-2 border-l border-border pl-5">
        {plan.steps.map((step) => {
          const done = completed.has(step.id);
          const isApproved = approved.has(step.id);
          const dependencies = step.depends_on ?? [];
          const criteria = step.success_criteria ?? [];
          return (
            <li key={step.id} className="relative rounded-md border border-border bg-bg-elevated/50 p-3">
              <span
                aria-hidden
                className={cn(
                  "absolute -left-[25px] top-4 flex size-3.5 items-center justify-center rounded-full border-2 border-bg-panel",
                  done ? "bg-success" : "bg-bg-elevated border-border-strong",
                )}
              />
              <p className="flex flex-wrap items-center gap-2 text-xs text-fg">
                <span className="font-medium">{step.title}</span>
                {done && <Badge tone="success">done</Badge>}
                {isApproved && !done && <Badge tone="accent">approved</Badge>}
                {step.requires_approval && !isApproved && !done && <Badge tone="warning">needs approval</Badge>}
                {step.tool_key && (
                  <span className="rounded-full border border-border px-1.5 font-mono text-[10px] text-fg-subtle">
                    {step.tool_key}
                  </span>
                )}
                {dependencies.length > 0 && (
                  <span className="text-2xs text-fg-subtle">after {dependencies.join(", ")}</span>
                )}
              </p>
              {step.description && (
                <p className="mt-1 text-xs leading-relaxed text-fg-muted">{step.description}</p>
              )}
              {criteria.length > 0 && (
                <p className="mt-1.5 text-2xs text-fg-subtle">
                  success: {criteria.join("; ")}
                </p>
              )}
            </li>
          );
        })}
      </ol>
      {plan.notes && <p className="mt-3 text-2xs text-fg-subtle">Notes: {plan.notes}</p>}
    </section>
  );
}
