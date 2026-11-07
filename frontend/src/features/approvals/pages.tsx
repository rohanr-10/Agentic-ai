import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import {
  Brain,
  Check,
  ChevronRight,
  Clock,
  ListChecks,
  TriangleAlert,
  X,
} from "lucide-react";
import { post, type ApiError } from "@/services/api/client";
import type { ApprovalOut, ApprovalStatus, Page, PlanSpec, PlanStepSpec } from "@/types/api";
import { PageShell } from "@/components/layout/AppLayout";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/Field";
import { EmptyState, ErrorState, PageLoader } from "@/components/ui/State";
import { toast } from "@/components/ui/toast";
import { useApprovals } from "@/features/queries";
import { useActivity } from "@/features/activity/ActivityProvider";
import { formatDateTime, formatRelative } from "@/utils/format";
import { cn } from "@/utils/cn";

const STATUS_TONES: Record<ApprovalStatus, "warning" | "success" | "danger" | "muted"> = {
  pending: "warning",
  approved: "success",
  rejected: "danger",
  expired: "muted",
};

export default function ApprovalsPage() {
  const [statusFilter, setStatusFilter] = useState<ApprovalStatus | "">("pending");
  const approvals = useApprovals(statusFilter || undefined);
  const activity = useActivity();
  const queryClient = useQueryClient();
  const [focus, setFocus] = useState<ApprovalOut | null>(null);
  const [deciding, setDeciding] = useState<"approve" | "reject" | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const decide = useMutation({
    mutationFn: async ({ id, decision, why }: { id: string; decision: "approve" | "reject"; why?: string }) =>
      post<ApprovalOut>(`/approvals/${id}`, { decision, reason: why || null }),
    onSuccess: (updated) => {
      toast.success(
        deciding === "approve" ? "Approved" : "Rejected",
        deciding === "approve"
          ? "The orchestrator resumes the task now."
          : "The task will be cancelled.",
      );
      activity.push({
        kind: "approval.requested",
        title: `Approval ${updated.status}`,
        taskId: updated.task_id,
        level: updated.status === "approved" ? "success" : "error",
      });
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
      void queryClient.invalidateQueries({ queryKey: ["tasks", updated.task_id] });
      setFocus(null);
      setDeciding(null);
      setReason("");
    },
    onError: (err) => {
      toast.error("Decision failed", (err as ApiError).message);
      setBusy(false);
    },
  });

  const confirmDecision = () => {
    if (!focus || !deciding) return;
    setBusy(true);
    decide.mutate({ id: focus.id, decision: deciding, why: reason.trim() || undefined });
  };

  return (
    <PageShell
      title="Approvals"
      actions={
        <SelectField
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as ApprovalStatus | "")}
          aria-label="Filter approvals"
          className="w-40"
          options={[
            { value: "pending", label: "Pending" },
            { value: "approved", label: "Approved" },
            { value: "rejected", label: "Rejected" },
            { value: "expired", label: "Expired" },
            { value: "", label: "All" },
          ]}
        />
      }
    >
      <p className="border-b border-border/60 px-4 py-3 text-sm leading-relaxed text-fg-muted sm:px-6">
        Plans and sensitive tool steps require your explicit decision. Nothing executes before you
        approve — enforced by the backend, not by this page.
      </p>

      {approvals.isLoading ? (
        <PageLoader label="Loading approvals…" />
      ) : approvals.isError ? (
        <ErrorState
          title="Couldn't load approvals"
          message={(approvals.error as Error).message}
          onRetry={() => void approvals.refetch()}
        />
      ) : (approvals.data?.items.length ?? 0) === 0 ? (
        <EmptyState
          className="pt-20"
          icon={<Brain />}
          title={statusFilter === "pending" ? "Nothing waiting for approval" : "No approvals here"}
          description={
            statusFilter === "pending"
              ? "You're all caught up. New requests appear the moment a task needs a decision."
              : "Try a different filter."
          }
        />
      ) : (
        <ul className="divide-y divide-border/60" aria-label="Approval requests">
          {(approvals.data?.items ?? []).map((approval) => (
            <li key={approval.id} className="px-4 py-4 sm:px-6">
              <button
                type="button"
                onClick={() => {
                  setFocus(approval);
                  setDeciding(null);
                  setReason("");
                }}
                className="group flex w-full items-start gap-3 text-left"
                aria-label={`Review ${approval.title}`}
              >
                <span
                  aria-hidden
                  className={cn(
                    "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg border",
                    approval.status === "pending"
                      ? "border-warning/30 bg-warning-subtle text-warning"
                      : "border-border bg-bg-elevated text-fg-subtle",
                  )}
                >
                  {approval.status === "pending" ? (
                    <TriangleAlert size={15} aria-hidden />
                  ) : (
                    <ListChecks size={15} aria-hidden />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-medium text-fg">{approval.title}</span>
                    <Badge tone={STATUS_TONES[approval.status]}>{approval.status}</Badge>
                    <Badge tone="muted">{approval.kind}</Badge>
                  </span>
                  <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-2xs text-fg-subtle">
                    <span>requested {formatRelative(approval.requested_at)}</span>
                    {approval.expires_at && (
                      <>
                        <span aria-hidden>·</span>
                        <span>expires {formatDateTime(approval.expires_at)}</span>
                      </>
                    )}
                    {approval.payload.step?.tool_key && (
                      <>
                        <span aria-hidden>·</span>
                        <span className="font-mono text-accent-hover">{approval.payload.step.tool_key}</span>
                      </>
                    )}
                  </span>
                </span>
                <ChevronRight
                  size={15}
                  aria-hidden
                  className="mt-1 shrink-0 text-fg-subtle transition-transform group-hover:translate-x-0.5 group-hover:text-fg-muted"
                />
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* decision dialog */}
      <Dialog
        open={focus !== null}
        onClose={() => {
          setFocus(null);
          setDeciding(null);
        }}
        title={focus?.title ?? ""}
        description={focus ? `${focus.kind} approval · ${formatDateTime(focus.requested_at)}` : undefined}
        footer={
          focus?.status === "pending" ? (
            <>
              <Button
                variant="outline"
                loading={busy && deciding === "reject"}
                onClick={() => setDeciding("reject")}
                className={cn(deciding === "reject" ? "" : "")}
              >
                <X size={13} aria-hidden /> Reject…
              </Button>
              <Button
                variant="primary"
                loading={busy && deciding === "approve"}
                onClick={() => setDeciding("approve")}
              >
                <Check size={13} aria-hidden /> Approve…
              </Button>
            </>
          ) : (
            <Button variant="ghost" onClick={() => setFocus(null)}>
              Close
            </Button>
          )
        }
      >
        {focus && (
          <div className="space-y-4">
            <div className="rounded-lg border border-border bg-bg-elevated/50 p-3.5">
              <p className="mb-1 text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
                {focus.kind === "plan" ? "Proposed plan" : "Proposed step"}
              </p>
              {focus.kind === "plan" ? (
                <PlanPreview plan={focus.payload.plan ?? null} objective={focus.payload.objective} />
              ) : (
                <StepPreview step={focus.payload.step ?? null} dependsOn={focus.payload.depends_on ?? []} />
              )}
            </div>

            {focus.status === "pending" && deciding && (
              <div>
                <p className="mb-2 text-xs text-fg-muted">
                  {deciding === "approve"
                    ? "Approving lets the backend execute exactly what is listed above — under the task's budgets and sandbox."
                    : "Rejecting cancels the task with an approval_rejected error."}
                </p>
                <textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Optional reason (recorded in the audit trail)"
                  aria-label="Reason"
                  maxLength={2000}
                  className="min-h-20 w-full rounded-md border border-border bg-bg-input px-3 py-2 text-sm text-fg placeholder:text-fg-subtle focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25"
                />
                <div className="mt-3 flex justify-end gap-2">
                  <Button variant="ghost" onClick={() => setDeciding(null)}>
                    Back
                  </Button>
                  <Button
                    variant={deciding === "approve" ? "primary" : "danger"}
                    loading={busy}
                    onClick={confirmDecision}
                  >
                    Confirm {deciding}
                  </Button>
                </div>
              </div>
            )}

            {focus.status !== "pending" && (
              <p className="text-xs text-fg-subtle">
                {focus.status} {focus.decided_at ? `at ${formatDateTime(focus.decided_at)}` : ""}
                {focus.reason ? ` — “${focus.reason}”` : ""}
              </p>
            )}
            {focus.status === "pending" && !deciding && (
              <p className="flex items-center gap-2 text-xs text-fg-subtle">
                <Clock size={13} aria-hidden />
                {focus.expires_at
                  ? `Expires ${formatDateTime(focus.expires_at)} — after that the task fails automatically.`
                  : "No expiry recorded."}
              </p>
            )}
            <p className="text-2xs text-fg-subtle">
              Task:{" "}
              <Link to={`/tasks/${focus.task_id}`} className="text-accent-hover hover:underline">
                open task detail
              </Link>
            </p>
          </div>
        )}
      </Dialog>
    </PageShell>
  );
}

function PlanPreview({ plan, objective }: { plan: PlanSpec | null; objective?: string }) {
  if (!plan) {
    return <p className="text-sm text-fg-muted">{objective ?? "No plan payload recorded."}</p>;
  }
  return (
    <div>
      {plan.objective_restated && (
        <p className="mb-2 text-sm italic text-fg-muted">{plan.objective_restated}</p>
      )}
      <ol className="space-y-1.5">
        {plan.steps.map((step, index) => {
          const dependencies = step.depends_on ?? [];
          return (
            <li key={step.id} className="flex items-start gap-2 text-xs text-fg-muted">
              <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-border text-[9px] text-fg-subtle">
                {index + 1}
              </span>
              <span className="min-w-0">
                <span className="font-medium text-fg">{step.title}</span>
                {step.tool_key && <span className="font-mono text-2xs text-accent-hover"> · {step.tool_key}</span>}
                {step.requires_approval && <span className="text-2xs text-warning"> · sensitive</span>}
                {dependencies.length > 0 && (
                  <span className="text-2xs text-fg-subtle"> — after {dependencies.join(", ")}</span>
                )}
              </span>
            </li>
          );
        })}
      </ol>
      {plan.notes && <p className="mt-2 text-2xs text-fg-subtle">Notes: {plan.notes}</p>}
    </div>
  );
}

function StepPreview({ step }: { step: PlanStepSpec | null; dependsOn?: string[] }) {
  if (!step) return <p className="text-sm text-fg-muted">No step payload recorded.</p>;
  const dependencies = step.depends_on ?? [];
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium text-fg">{step.title}</p>
      {step.description && <p className="text-xs leading-relaxed text-fg-muted">{step.description}</p>}
      <p className="flex flex-wrap items-center gap-2 text-2xs text-fg-subtle">
        {step.tool_key && <span className="font-mono text-accent-hover">{step.tool_key}</span>}
        <span>timeout {step.timeout_s ?? 60}s</span>
        {dependencies.length > 0 && <span>after {dependencies.join(", ")}</span>}
      </p>
      {step.input && Object.keys(step.input).length > 0 && (
        <details open>
          <summary className="cursor-pointer text-2xs text-fg-subtle hover:text-fg-muted">
            proposed input
          </summary>
          <pre className="mt-1.5 max-h-40 overflow-auto rounded-md border border-border bg-[#0d0f13] p-2.5 font-mono text-2xs leading-relaxed text-fg-muted">
{JSON.stringify(step.input, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

// keep Page import referenced for future typed list use
export type { Page };
