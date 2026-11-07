import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  FileStack,
  FileText,
  RefreshCw,
  Trash2,
  TriangleAlert,
  UploadCloud,
} from "lucide-react";
import { apiRequest, get, del, apiBaseUrl, type ApiError } from "@/services/api/client";
import { authStore } from "@/features/auth/authStore";
import type { FileDetailOut, FileOut, Page } from "@/types/api";
import { PageShell } from "@/components/layout/AppLayout";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState, ErrorState, PageLoader } from "@/components/ui/State";
import { toast } from "@/components/ui/toast";
import { useFiles } from "@/features/queries";
import { formatBytes, formatDateTime, formatRelative } from "@/utils/format";
import { cn } from "@/utils/cn";

const STATUS_TONES = {
  pending: "muted",
  processing: "info",
  ready: "success",
  failed: "danger",
  quarantined: "danger",
} as const;

export default function FilesPage() {
  const files = useFiles();
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [detailOpen, setDetailOpen] = useState<FileDetailOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<FileOut | null>(null);

  const upload = async (list: FileList | File[]) => {
    setBusy(true);
    try {
      for (const file of Array.from(list)) {
        const form = new FormData();
        form.append("file", file);
        await apiRequest<FileOut>({
          path: "/files",
          method: "POST",
          formData: form,
          timeoutMs: 120_000,
        });
      }
      toast.success("Upload complete", "Text extraction runs in the background.");
      void queryClient.invalidateQueries({ queryKey: ["files"] });
    } catch (err) {
      toast.error("Upload failed", (err as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = useMutation({
    mutationFn: (id: string) => del(`/files/${id}`),
    onSuccess: () => {
      toast.success("File deleted");
      setDeleteTarget(null);
      void queryClient.invalidateQueries({ queryKey: ["files"] });
    },
    onError: (err) => toast.error("Delete failed", (err as ApiError).message),
  });

  const openDetail = async (file: FileOut) => {
    try {
      const detail = await get<FileDetailOut>(`/files/${file.id}`, { include_text: true });
      setDetailOpen(detail);
    } catch (err) {
      toast.error("Couldn't open file", (err as Error).message);
    }
  };

  return (
    <PageShell
      title="Files"
      actions={
        <>
          <Button
            variant="ghost"
            onClick={() => void queryClient.invalidateQueries({ queryKey: ["files"] })}
            aria-label="Refresh files"
          >
            <RefreshCw size={14} aria-hidden />
          </Button>
          <Button variant="primary" loading={busy} onClick={() => inputRef.current?.click()}>
            <UploadCloud size={14} aria-hidden />
            Upload
          </Button>
        </>
      }
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files?.length) void upload(e.target.files);
          e.target.value = "";
        }}
        accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.json,.py,.js,.ts,.html,.css,.yaml,.yml,.toml,.xml,.log,.jpeg,.jpg,.png"
      />

      <p className="border-b border-border/60 px-4 py-3 text-sm leading-relaxed text-fg-muted sm:px-6">
        Uploads are stored server-side; text is extracted by the backend (PDF/DOCX/CSV/XLSX…).
        In chat, attach a file to have the assistant read it through the document tool.
      </p>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer.files.length) void upload(e.dataTransfer.files);
        }}
        className={cn(
          "border-b border-border/60 px-4 py-6 text-center transition-colors sm:px-6",
          dragOver ? "bg-accent-subtle/40" : "",
        )}
      >
        <p className="text-xs text-fg-subtle">
          Drag & drop files here — up to 50 MiB each;{" "}
          <button
            type="button"
            className="font-medium text-accent-hover underline underline-offset-2"
            onClick={() => inputRef.current?.click()}
          >
            or browse
          </button>
        </p>
      </div>

      {files.isLoading ? (
        <PageLoader label="Loading files…" />
      ) : files.isError ? (
        <ErrorState
          title="Couldn't load files"
          message={(files.error as Error).message}
          onRetry={() => void files.refetch()}
        />
      ) : (files.data?.items.length ?? 0) === 0 ? (
        <EmptyState
          className="pt-16"
          icon={<FileStack />}
          title="No files yet"
          description="Upload documents to analyse them in conversations or autonomous tasks."
        />
      ) : (
        <ul className="divide-y divide-border/60" aria-label="Files">
          {(files.data?.items ?? []).map((file) => (
            <li key={file.id} className="flex items-center gap-3 px-4 py-3.5 sm:px-6">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-bg-elevated text-fg-subtle">
                <FileText size={16} aria-hidden />
              </span>
              <button
                type="button"
                onClick={() => void openDetail(file)}
                className="min-w-0 flex-1 text-left"
                aria-label={`Open ${file.original_filename}`}
              >
                <p className="flex flex-wrap items-center gap-2">
                  <span className="truncate text-sm font-medium text-fg">{file.original_filename}</span>
                  <Badge tone={STATUS_TONES[file.status]}>{file.status}</Badge>
                  {file.scan_status === "infected" && <Badge tone="danger">infected</Badge>}
                </p>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-2xs text-fg-subtle">
                  <span>{formatBytes(file.size_bytes)}</span>
                  <span aria-hidden>·</span>
                  <span className="font-mono">{file.mime_type}</span>
                  <span aria-hidden>·</span>
                  <span>{file.extracted_chars.toLocaleString()} chars extracted</span>
                  <span aria-hidden>·</span>
                  <span>{formatRelative(file.created_at)}</span>
                  {file.error && <span className="text-danger">· {file.error}</span>}
                </p>
              </button>
              <div className="flex shrink-0 items-center gap-1">
                <button
                  type="button"
                  onClick={() => void downloadFile(file, file.original_filename)}
                  className="rounded-md p-2 text-fg-subtle transition-colors hover:bg-bg-hover hover:text-fg"
                  aria-label={`Download ${file.original_filename}`}
                  title="Download"
                >
                  <Download size={15} aria-hidden />
                </button>
                <button
                  type="button"
                  onClick={() => setDeleteTarget(file)}
                  className="rounded-md p-2 text-fg-subtle transition-colors hover:bg-danger-subtle hover:text-danger"
                  aria-label={`Delete ${file.original_filename}`}
                >
                  <Trash2 size={15} aria-hidden />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* detail dialog */}
      <Dialog
        open={detailOpen !== null}
        onClose={() => setDetailOpen(null)}
        title={detailOpen?.original_filename ?? ""}
        description={
          detailOpen
            ? `${formatBytes(detailOpen.size_bytes)} · ${detailOpen.mime_type} · uploaded ${formatDateTime(detailOpen.created_at)}`
            : undefined
        }
      >
        {detailOpen && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Meta label="Status" value={detailOpen.status} />
              <Meta label="Extracted" value={`${detailOpen.extracted_chars.toLocaleString()} chars`} />
              <Meta label="SHA-256" value={`${detailOpen.sha256.slice(0, 12)}…`} />
              <Meta label="Scan" value={detailOpen.scan_status} />
            </div>
            {detailOpen.error && (
              <p role="alert" className="flex items-center gap-2 rounded-md border border-danger/30 bg-danger-subtle px-3 py-2 text-xs text-danger">
                <TriangleAlert size={14} aria-hidden /> {detailOpen.error}
              </p>
            )}
            {detailOpen.extracted_text ? (
              <details open>
                <summary className="cursor-pointer text-xs font-medium text-fg-muted">
                  Extracted text
                </summary>
                <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-[#0d0f13] p-3 font-mono text-2xs leading-relaxed text-fg-muted">
                  {detailOpen.extracted_text.slice(0, 20_000)}
                </pre>
              </details>
            ) : (
              <p className="text-xs text-fg-subtle">
                No extracted text {detailOpen.status === "processing" ? "yet — extraction is queued." : "available for this file."}
              </p>
            )}
            <Button variant="secondary" onClick={() => void downloadFile(detailOpen, detailOpen.original_filename)}>
              <Download size={13} aria-hidden /> Download
            </Button>
          </div>
        )}
      </Dialog>

      <Dialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title="Delete this file?"
        description="Removes the stored file and its extraction. Attachments that reference it in old messages stay in history."
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleteTarget(null)}>
              Keep it
            </Button>
            <Button
              variant="danger"
              loading={remove.isPending}
              onClick={() => deleteTarget && remove.mutate(deleteTarget.id)}
            >
              Delete
            </Button>
          </>
        }
      >
        {deleteTarget && (
          <p className="text-sm text-fg-muted">
            “{deleteTarget.original_filename}” ({formatBytes(deleteTarget.size_bytes)}) will be removed from storage.
          </p>
        )}
      </Dialog>
    </PageShell>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-bg-elevated/50 px-2.5 py-2">
      <p className="text-2xs uppercase tracking-wide text-fg-subtle">{label}</p>
      <p className="mt-0.5 truncate text-xs text-fg">{value}</p>
    </div>
  );
}

async function downloadFile(file: FileOut, filename: string) {
  try {
    const response = await fetch(`${apiBaseUrl()}/files/${file.id}/download`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      throw new Error(`download failed (${response.status})`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename || file.original_filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    toast.error("Download failed", (err as Error).message);
  }
}

function authHeaders(): Record<string, string> {
  // the token lives in the auth store; import lazily to avoid cycles
  const token = authStore.getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export type { Page };
