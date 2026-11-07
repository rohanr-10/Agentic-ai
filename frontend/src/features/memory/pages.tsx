import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Plus, RefreshCw, Search, ShieldCheck, Trash2 } from "lucide-react";
import { post, del, type ApiError } from "@/services/api/client";
import type { MemoryOut, Page, MemoryCategory } from "@/types/api";
import { PageShell } from "@/components/layout/AppLayout";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField, TextArea, TextField } from "@/components/ui/Field";
import { EmptyState, ErrorState, PageLoader } from "@/components/ui/State";
import { toast } from "@/components/ui/toast";
import { useMemoryList } from "@/features/queries";
import { formatRelative } from "@/utils/format";

const CATEGORY_TONES: Record<MemoryCategory, "accent" | "info" | "success" | "muted"> = {
  conversational: "muted",
  episodic: "info",
  semantic: "accent",
  procedural: "success",
};

const SOURCE_LABEL: Record<string, string> = {
  user_provided: "user",
  extracted: "extracted",
  model_generated: "model",
  verified: "verified",
};

export default function MemoryPage() {
  const memory = useMemoryList();
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [verifyTarget, setVerifyTarget] = useState<MemoryOut | null>(null);
  const [evidence, setEvidence] = useState("");

  const search = useQuery({
    queryKey: ["memory", "search", searchQuery],
    queryFn: () =>
      post<{ record: MemoryOut; score: number }[]>("/memory/search", {
        query: searchQuery.trim(),
        top_k: 20,
      }),
    enabled: searchQuery.trim().length > 0,
  });

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["memory"] });

  const forget = useMutation({
    mutationFn: (id: string) => del(`/memory/${id}`),
    onSuccess: () => {
      toast.success("Memory forgotten");
      invalidate();
    },
    onError: (err) => toast.error("Couldn't forget", (err as ApiError).message),
  });

  const verify = useMutation({
    mutationFn: () =>
      post<MemoryOut>(`/memory/${verifyTarget?.id}/verify`, { evidence: evidence.trim() }),
    onSuccess: () => {
      toast.success("Marked verified", "It is now treated as a known fact in context assembly.");
      setVerifyTarget(null);
      setEvidence("");
      invalidate();
    },
    onError: (err) => toast.error("Verification failed", (err as ApiError).message),
  });

  const rows: Array<{ record: MemoryOut; score?: number }> = searchQuery.trim()
    ? (search.data ?? []).map((hit) => ({ record: hit.record, score: hit.score }))
    : (memory.data?.items ?? []).map((record) => ({ record }));

  return (
    <PageShell
      title="Memory"
      actions={
        <>
          <Button variant="ghost" onClick={invalidate} aria-label="Refresh memory">
            <RefreshCw size={14} aria-hidden />
          </Button>
          <Button variant="primary" onClick={() => setCreateOpen(true)}>
            <Plus size={14} aria-hidden />
            Store memory
          </Button>
        </>
      }
    >
      <p className="border-b border-border/60 px-4 py-3 text-sm leading-relaxed text-fg-muted sm:px-6">
        Long-lived knowledge the assistant recalls across conversations. The backend separates
        user-provided/verified facts from model-generated notes and never upgrades provenance
        silently.
      </p>

      <div className="border-b border-border/60 px-4 py-3 sm:px-6">
        <TextField
          leftIcon={<Search size={15} aria-hidden />}
          placeholder="Recall by keyword (scored search on the backend)…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          aria-label="Search memory"
        />
      </div>

      {searchQuery.trim() ? (
        search.isLoading ? (
          <PageLoader label="Searching…" />
        ) : search.isError ? (
          <ErrorState title="Search failed" message={(search.error as Error).message} />
        ) : rows.length === 0 ? (
          <EmptyState className="pt-16" icon={<Brain />} title="No matches" description="Try a different term." />
        ) : (
          <ul className="divide-y divide-border/60" aria-label="Memory search results">
            {rows.map(({ record, score }) => (
              <MemoryRow
                key={record.id}
                record={record}
                score={score}
                onVerify={() => setVerifyTarget(record)}
                onForget={() => record && forget.mutate(record.id)}
              />
            ))}
          </ul>
        )
      ) : memory.isLoading ? (
        <PageLoader label="Loading memory…" />
      ) : memory.isError ? (
        <ErrorState
          title="Couldn't load memory"
          message={(memory.error as Error).message}
          onRetry={() => void memory.refetch()}
        />
      ) : rows.length === 0 ? (
        <EmptyState
          className="pt-16"
          icon={<Brain />}
          title="No stored memories"
          description="Store facts you want the assistant to remember across conversations — your preferences, project context, definitions."
        />
      ) : (
        <ul className="divide-y divide-border/60" aria-label="Stored memories">
          {rows.map(({ record }) => (
            <MemoryRow
              key={record.id}
              record={record}
              onVerify={() => setVerifyTarget(record)}
              onForget={() => record && forget.mutate(record.id)}
            />
          ))}
        </ul>
      )}

      <CreateMemoryDialog open={createOpen} onClose={() => setCreateOpen(false)} onSaved={invalidate} />

      <Dialog
        open={verifyTarget !== null}
        onClose={() => setVerifyTarget(null)}
        title="Mark as verified"
        description="Requires explicit evidence; verified memories are stated as facts in future conversations."
        footer={
          <>
            <Button variant="ghost" onClick={() => setVerifyTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={verify.isPending}
              disabled={evidence.trim().length < 3}
              onClick={() => verify.mutate()}
            >
              Verify
            </Button>
          </>
        }
      >
        {verifyTarget && (
          <div className="space-y-3">
            <p className="rounded-md border border-border bg-bg-elevated/50 p-3 text-sm text-fg-muted">
              {verifyTarget.content}
            </p>
            <TextArea
              autoFocus
              label="Evidence"
              placeholder="Where this was confirmed (source, link, observation)…"
              value={evidence}
              onChange={(e) => setEvidence(e.target.value)}
              maxLength={2000}
            />
          </div>
        )}
      </Dialog>
    </PageShell>
  );
}

function MemoryRow({
  record,
  score,
  onVerify,
  onForget,
}: {
  record?: MemoryOut;
  score?: number;
  onVerify: () => void;
  onForget: () => void;
}) {
  if (!record) return null;
  const canVerify = record.source !== "verified" && record.source !== "user_provided";
  return (
    <li className="px-4 py-4 sm:px-6">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-xs text-fg-subtle">
            <Badge tone={CATEGORY_TONES[record.category]}>{record.category}</Badge>
            <span className="font-medium text-fg-muted">{SOURCE_LABEL[record.source] ?? record.source}</span>
            <span>v{record.version}</span>
            {score !== undefined && (
              <span className="rounded-full border border-accent/30 bg-accent-subtle px-1.5 text-[10px] text-accent">
                {(score * 100).toFixed(0)}% match
              </span>
            )}
            {record.expires_at && <span>expires {formatRelative(record.expires_at)}</span>}
          </p>
          <p className="mt-1.5 text-sm leading-relaxed text-fg">{record.content}</p>
          <p className="mt-1 text-2xs text-fg-subtle">
            updated {formatRelative(record.updated_at)}
            {record.title ? ` · ${record.title}` : ""}
            {record.key ? ` · ${record.key}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 gap-1">
          {canVerify && (
            <Button variant="ghost" size="sm" onClick={onVerify} aria-label="Verify memory" title="Verify">
              <ShieldCheck size={14} aria-hidden />
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={onForget}
            aria-label="Forget memory"
            className="text-fg-subtle hover:text-danger"
          >
            <Trash2 size={14} aria-hidden />
          </Button>
        </div>
      </div>
    </li>
  );
}

function CreateMemoryDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [category, setCategory] = useState<MemoryCategory>("semantic");
  const [title, setTitle] = useState("");
  const [key, setKey] = useState("");
  const [content, setContent] = useState("");
  const [importance, setImportance] = useState("0.5");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      post<MemoryOut>("/memory", {
        category,
        title: title.trim() || null,
        key: key.trim() || null,
        content: content.trim(),
        source: "user_provided",
        importance: Number(importance) || 0.5,
        confidence: 0.95,
      }),
    onSuccess: () => {
      toast.success("Memory stored", "It will be recalled in future conversations.");
      onSaved();
      onClose();
      setTitle("");
      setKey("");
      setContent("");
    },
    onError: (err) => setError((err as ApiError).message || "Couldn't store memory"),
  });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Store a memory"
      description="Stored as a user-provided fact — always treated as trusted in context assembly."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={content.trim().length === 0}
            onClick={() => {
              setError(null);
              create.mutate();
            }}
          >
            Store
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
        <SelectField
          label="Category"
          value={category}
          onChange={(e) => setCategory(e.target.value as MemoryCategory)}
          options={[
            { value: "semantic", label: "semantic — facts" },
            { value: "episodic", label: "episodic — events" },
            { value: "procedural", label: "procedural — how-to" },
            { value: "conversational", label: "conversational — dialogue notes" },
          ]}
        />
        <div className="grid grid-cols-2 gap-3">
          <TextField label="Title (optional)" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
          <TextField label="Key (optional)" placeholder="project-stack" value={key} onChange={(e) => setKey(e.target.value)} maxLength={150} />
        </div>
        <TextArea
          label="Content"
          placeholder="The production database is PostgreSQL 16 with read replicas."
          value={content}
          onChange={(e) => setContent(e.target.value)}
          maxLength={32_000}
          className="[&_textarea]:min-h-28"
        />
        <TextField
          label="Importance (0–1)"
          inputMode="decimal"
          value={importance}
          onChange={(e) => setImportance(e.target.value)}
          hint="Higher importance survives longer in recall"
        />
      </div>
    </Dialog>
  );
}

export type { Page };
