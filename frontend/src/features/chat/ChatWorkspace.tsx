import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import {
  Archive,
  ArchiveRestore,
  ArrowDown,
  Check,
  Copy,
  MessageSquare,
  PanelRight,
  Pencil,
  RefreshCw,
  Search,
  Send,
  Trash2,
} from "lucide-react";
import { get, patch, post, del } from "@/services/api/client";
import type { ChatResponse, ConversationOut, FileOut, MessageOut, SearchHit } from "@/types/api";
import { toast } from "@/components/ui/toast";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { SelectField, TextField } from "@/components/ui/Field";
import { EmptyState, ErrorState, PageLoader } from "@/components/ui/State";
import MessageBubble, { StreamingBubble, SystemNotice } from "@/components/messages/MessageBubble";
import { Composer } from "@/components/chat/Composer";
import { useChatStream, uploadFiles } from "@/features/chat/useChatStream";
import { useActivity } from "@/features/activity/ActivityProvider";
import { useIsDesktop } from "@/hooks/useMediaQuery";
import { formatTime } from "@/utils/format";

const CONVERSATIONS_KEY = ["conversations"] as const;

export default function ChatWorkspace() {
  const { conversationId } = useParams<{ conversationId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const activity = useActivity();
  const isDesktop = useIsDesktop();

  // ---- conversation data ------------------------------------------------
  // The cast below is safe: the query only runs with a real id and the
  // undefined-branch returns before this line executes.
  const conversationQuery = useQuery({
    queryKey: ["conversations", "detail", conversationId],
    queryFn: () => get<ConversationOut>(`/conversations/${conversationId}`),
    enabled: conversationId !== undefined,
  });

  const messagesQuery = useQuery({
    queryKey: ["conversations", conversationId, "messages"],
    queryFn: () => get<MessageOut[]>(`/conversations/${conversationId}/messages`, { limit: 200 }),
    enabled: conversationId !== undefined,
  });

  const invalidations = useCallback(() => {
    if (!conversationId) return;
    void queryClient.invalidateQueries({ queryKey: ["conversations", conversationId, "messages"] });
    void queryClient.invalidateQueries({ queryKey: ["conversations", "detail", conversationId] });
    void queryClient.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
  }, [conversationId, queryClient]);

  const stream = useChatStream(
    (line) => activity.push({ kind: "stream", title: line, level: "info" }),
    invalidations,
  );

  // ---- attachments (pending upload for the next message) -----------------
  const [attachments, setAttachments] = useState<FileOut[]>([]);
  const [uploadBusy, setUploadBusy] = useState(false);

  const attachFiles = async (files: FileList) => {
    if (!conversationId || conversationQuery.data?.status === "archived") return;
    setUploadBusy(true);
    try {
      const uploaded = await uploadFiles(files, conversationId);
      setAttachments((current) => [...current, ...uploaded].slice(0, 10));
      toast.success("Files uploaded", "Extraction is running on the backend.");
    } catch (err) {
      toast.error("Upload failed", (err as Error).message);
    } finally {
      setUploadBusy(false);
    }
  };

  // ---- send ----------------------------------------------------------------
  const send = async (text: string, modelOverride: string | null) => {
    if (!conversationId || !conversationQuery.data) return;
    if (conversationQuery.data.status === "archived") {
      toast.warning("Conversation archived", "Unarchive it to continue chatting.");
      return;
    }
    const attachmentIds = attachments.map((a) => a.id);
    const modelForMessage = modelOverride ?? conversationQuery.data.model_config_id ?? null;
    try {
      await stream.send(conversationId, text, modelForMessage, attachmentIds);
      setAttachments([]);
    } catch (err) {
      toast.error("Message failed", (err as Error).message);
    }
  };

  // The streaming route already persists the user message; after `done` we
  // refetch. If the stream errored we fall back to the non-streaming route so
  // the user still gets a reply (and the user message is already saved).
  const [fallbackBusy, setFallbackBusy] = useState(false);
  const retryNonStreaming = async (text: string) => {
    if (!conversationId) return;
    setFallbackBusy(true);
    try {
      const response = await post<ChatResponse>(`/conversations/${conversationId}/messages`, {
        content: text,
        attachments: attachments.map((a) => a.id),
      });
      void response;
      invalidations();
    } catch (err) {
      toast.error("Retry failed", (err as Error).message);
    } finally {
      setFallbackBusy(false);
    }
  };

  // ---- autoscroll ----------------------------------------------------------
  const scrollerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  const atBottomRef = useRef(true);
  const onScroll = () => {
    const el = scrollerRef.current;
    if (!el) return;
    const nowAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    atBottomRef.current = nowAtBottom;
    setAtBottom(nowAtBottom);
  };
  useEffect(() => {
    // follow the latest message while the user is at the bottom
    if (atBottom) {
      bottomRef.current?.scrollIntoView({ behavior: "instant" as ScrollBehavior });
    }
  });
  // new conversation selection: scroll state resets through the DOM (list
  // re-renders at top) — no state-write effect needed

  // ---- search ----------------------------------------------------------
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const searchResults = useQuery({
    queryKey: ["conversations", conversationId, "search", searchQuery],
    queryFn: () => get<SearchHit[]>(`/conversations/${conversationId}/search`, { q: searchQuery, limit: 50 }),
    enabled: searchOpen && !!conversationId && searchQuery.trim().length > 0,
  });

  // ---- conversation management ------------------------------------------
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [confirmDeleteBusy, setConfirmDeleteBusy] = useState(false);
  const [renameTitle, setRenameTitle] = useState("");

  const archiveToggle = async () => {
    if (!conversationId || !conversationQuery.data) return;
    try {
      const archived = conversationQuery.data.status === "archived";
      const updated = await post<ConversationOut>(
        `/conversations/${conversationId}/${archived ? "unarchive" : "archive"}`,
      );
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      toast.success(archived ? "Conversation unarchived" : "Conversation archived", updated.title);
      invalidations();
    } catch (err) {
      toast.error("Action failed", (err as Error).message);
    }
  };

  const doDelete = async () => {
    if (!conversationId) return;
    setConfirmDeleteBusy(true);
    try {
      await del(`/conversations/${conversationId}`);
      toast.success("Conversation deleted");
      void queryClient.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
      navigate("/chat", { replace: true });
    } catch (err) {
      toast.error("Delete failed", (err as Error).message);
      setConfirmDeleteBusy(false);
    }
  };

  const regenerate = async (message: MessageOut) => {
    if (!conversationId) return;
    try {
      await post<ChatResponse>(`/conversations/${conversationId}/messages/${message.id}/regenerate`);
      invalidations();
      toast.success("Response regenerated", "The previous version is kept as inactive history.");
    } catch (err) {
      toast.error("Regeneration failed", (err as Error).message);
    }
  };

  // ---- no conversation selected: empty state ------------------------------
  if (!conversationId) {
    return <NoConversationSelected />;
  }

  if (conversationQuery.isLoading || (messagesQuery.isLoading && conversationId)) {
    return <PageLoader label="Loading conversation…" />;
  }
  if (conversationQuery.isError) {
    return (
      <ErrorState
        title="Conversation unavailable"
        message={(conversationQuery.error as Error).message}
        onRetry={() => void conversationQuery.refetch()}
      />
    );
  }

  const conversation = conversationQuery.data as ConversationOut;
  const archived = conversation.status === "archived";
  const messages = messagesQuery.data ?? [];
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant" && m.is_active);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header */}
      <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-border bg-bg-panel/60 px-3 backdrop-blur-sm sm:px-5">
        <div className="flex min-w-0 items-center gap-2.5">
          <button
            type="button"
            onClick={() => navigate("/chat")}
            aria-label="All conversations"
            title="All conversations"
            className="flex size-8 shrink-0 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg"
          >
            <MessageSquare size={16} aria-hidden />
          </button>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-fg">{conversation.title}</p>
            <p className="truncate text-2xs text-fg-subtle">
              {conversation.mode} · {conversation.message_count} messages · created{" "}
              {formatTime(conversation.created_at)}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" aria-label="Search in conversation" title="Search" onClick={() => setSearchOpen(true)} className="size-8 p-0">
            <Search size={15} aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label="Rename conversation"
            title="Rename"
            onClick={() => {
              setRenameTitle(conversation.title);
              setRenameOpen(true);
            }}
            className="size-8 p-0"
          >
            <Pencil size={15} aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label={archived ? "Unarchive conversation" : "Archive conversation"}
            title={archived ? "Unarchive" : "Archive"}
            onClick={() => void archiveToggle()}
            className="size-8 p-0"
          >
            {archived ? <ArchiveRestore size={15} aria-hidden /> : <Archive size={15} aria-hidden />}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label="Delete conversation"
            title="Delete"
            onClick={() => setDeleteOpen(true)}
            className="size-8 p-0 text-fg-muted hover:text-danger"
          >
            <Trash2 size={15} aria-hidden />
          </Button>
          {!isDesktop && (
            <Button variant="ghost" size="sm" aria-label="Agent activity" title="Activity" onClick={activity.toggle} className="size-8 p-0">
              <PanelRight size={15} aria-hidden />
            </Button>
          )}
        </div>
      </header>

      {/* Messages */}
      <div
        ref={scrollerRef}
        onScroll={onScroll}
        className="relative min-h-0 flex-1 overflow-y-auto"
        role="log"
        aria-label="Conversation messages"
      >
        {messagesQuery.isError ? (
          <ErrorState
            title="Couldn't load messages"
            message={(messagesQuery.error as Error).message}
            onRetry={() => void messagesQuery.refetch()}
            className="pt-16"
          />
        ) : messages.length === 0 && stream.state.phase === "idle" ? (
          <EmptyState
            className="pt-24"
            icon={<MessageSquare />}
            title="Start the conversation"
            description={
              archived
                ? "This conversation is archived. Unarchive it to send messages."
                : "Ask anything, or attach a document and ask about it. Streaming replies arrive token by token."
            }
          />
        ) : (
          <div className="pb-4 pt-2">
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} />
            ))}

            {stream.state.phase !== "idle" && (
              <StreamingBubble
                text={stream.state.text}
                phase={stream.state.phase === "connecting" ? "connecting" : stream.state.phase === "tools" ? "tools" : "streaming"}
              />
            )}

            {stream.state.error && (
              <SystemNotice>
                Stream error: {stream.state.error}. The backend saved your message —{" "}
                <button
                  type="button"
                  className="font-medium text-accent-hover underline underline-offset-2"
                  onClick={() => {
                    const text = window.prompt("Re-send as a new message:", "");
                    if (text) void retryNonStreaming(text);
                  }}
                >
                  send a follow-up
                </button>{" "}
                or retry below.
              </SystemNotice>
            )}

            {fallbackBusy && (
              <SystemNotice>Retrying over the non-streaming route…</SystemNotice>
            )}

            <div ref={bottomRef} />
          </div>
        )}

        {!atBottom && (
          <button
            type="button"
            aria-label="Scroll to latest"
            onClick={() => bottomRef.current?.scrollIntoView({ behavior: "smooth" })}
            className="sticky bottom-4 left-1/2 z-10 -ml-4 flex size-9 -translate-x-1/2 items-center justify-center rounded-full border border-border bg-bg-elevated text-fg shadow-lg transition-colors hover:bg-bg-hover"
          >
            <ArrowDown size={16} aria-hidden />
          </button>
        )}
      </div>

      {/* Regenerate the latest assistant reply */}
      {lastAssistant && stream.state.phase === "idle" && !stream.state.error && messages.length > 0 && (
        <div className="flex justify-center pb-1">
          <button
            type="button"
            onClick={() => void regenerate(lastAssistant)}
            className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-2xs text-fg-subtle transition-colors hover:bg-bg-hover hover:text-fg"
          >
            <RefreshCw size={11} aria-hidden /> Regenerate last response
          </button>
        </div>
      )}

      {/* Composer */}
      <Composer
        onSend={(text, model) => void send(text, model)}
        onStop={stream.stop}
        streaming={stream.streaming}
        disabled={archived}
        disabledReason={archived ? "This conversation is archived — unarchive to send messages" : undefined}
        selectedModelId={conversation.model_config_id}
        onModelSelect={async (id) => {
          try {
            await patch<ConversationOut>(`/conversations/${conversationId}`, {
              model_config_id: id,
            });
            void queryClient.invalidateQueries({ queryKey: ["conversations", "detail", conversationId] });
            toast.success("Model updated", "New messages in this conversation will use it.");
          } catch (err) {
            toast.error("Couldn't set model", (err as Error).message);
          }
        }}
        attachments={attachments}
        onAttach={(files) => void attachFiles(files)}
        onRemoveAttachment={(fileId) => setAttachments((current) => current.filter((a) => a.id !== fileId))}
        uploadBusy={uploadBusy}
      />

      {/* Rename dialog */}
      <Dialog
        open={renameOpen}
        onClose={() => setRenameOpen(false)}
        title="Rename conversation"
        description="The title is only shown to you."
        footer={
          <>
            <Button variant="ghost" onClick={() => setRenameOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={renameTitle.trim().length === 0}
              onClick={async () => {
                try {
                  await patch<ConversationOut>(`/conversations/${conversationId}`, {
                    title: renameTitle.trim(),
                  });
                  setRenameOpen(false);
                  invalidations();
                  toast.success("Renamed");
                } catch (err) {
                  toast.error("Rename failed", (err as Error).message);
                }
              }}
            >
              Save
            </Button>
          </>
        }
      >
        <TextField
          autoFocus
          label="Title"
          value={renameTitle}
          onChange={(e) => setRenameTitle(e.target.value)}
          maxLength={200}
        />
      </Dialog>

      {/* Delete confirmation */}
      <Dialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title="Delete this conversation?"
        description="This permanently removes the conversation and all of its messages. There is no undo."
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleteOpen(false)}>
              Keep it
            </Button>
            <Button variant="danger" loading={confirmDeleteBusy} onClick={() => void doDelete()}>
              Delete permanently
            </Button>
          </>
        }
      >
        <p className="text-sm text-fg-muted">
          “{conversation.title}” and {conversation.message_count} messages will be removed.
        </p>
      </Dialog>

      {/* Search dialog */}
      <Dialog
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        title="Search in conversation"
        description="Full-text search across this conversation's messages (backend endpoint)."
      >
        <SearchPanel
          query={searchQuery}
          onQuery={setSearchQuery}
          results={searchResults}
          onClose={() => setSearchOpen(false)}
        />
      </Dialog>
    </div>
  );
}

function NoConversationSelected() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["conversations", "recent"],
    queryFn: () => get<ConversationOut[]>("/conversations", { limit: 8 }),
  });
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [mode, setMode] = useState<"conversational" | "hybrid">("conversational");

  const create = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const conversation = await post<ConversationOut>("/conversations", {
        title: title.trim() || "New conversation",
        mode,
      });
      void queryClient.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
      navigate(`/chat/${conversation.id}`, { replace: true });
    } catch (err) {
      toast.error("Couldn't create conversation", (err as Error).message);
      setCreating(false);
    }
  };

  return (
    <div className="flex h-full flex-col items-center justify-center px-6 py-12 text-center">
      <EmptyState
        icon={<MessageSquare />}
        title="Start a new conversation"
        description="Create one now, or pick a recent conversation below."
      />
      <div className="mt-6 w-full max-w-sm rounded-xl border border-border bg-bg-panel p-4">
        <TextField
          autoFocus
          label="Title"
          placeholder="Quarterly report analysis"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={200}
        />
        <div className="mt-3 flex items-center justify-between gap-3">
          <SelectField
            label="Mode"
            value={mode}
            onChange={(e) => setMode(e.target.value as "conversational" | "hybrid")}
            className="min-w-40"
            options={[
              { value: "conversational", label: "conversational" },
              { value: "hybrid", label: "hybrid (chat + tasks)" },
            ]}
          />
          <Button variant="primary" loading={creating} onClick={() => void create()} className="self-end">
            <Send size={13} aria-hidden /> Create
          </Button>
        </div>
        <p className="mt-3 text-2xs leading-relaxed text-fg-subtle">
          Hybrid mode lets tasks run in the background while you keep chatting; tasks attached to
          pure conversational threads are rejected by the backend.
        </p>
      </div>
      {data && data.length > 0 && (
        <ul className="mt-6 w-full max-w-sm space-y-1" aria-label="Recent conversations">
          {data.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => navigate(`/chat/${c.id}`)}
                className="flex w-full items-center justify-between gap-3 rounded-md border border-border bg-bg-panel px-3 py-2 text-left transition-colors hover:border-border-strong hover:bg-bg-hover"
              >
                <span className="min-w-0 flex-1 truncate text-sm text-fg">{c.title}</span>
                <span className="shrink-0 text-2xs text-fg-subtle">{c.message_count} msg</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SearchPanel({
  query,
  onQuery,
  results,
  onClose,
}: {
  query: string;
  onQuery: (value: string) => void;
  results: UseQueryResult<SearchHit[]>;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState<string | null>(null);
  return (
    <div>
      <TextField
        autoFocus
        label="Search text"
        placeholder="Type at least one character…"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        maxLength={200}
      />
      <div className="mt-4 max-h-80 overflow-y-auto" aria-live="polite">
        {query.trim().length === 0 ? (
          <p className="py-6 text-center text-xs text-fg-subtle">Type to search.</p>
        ) : results.isLoading ? (
          <p className="py-6 text-center text-xs text-fg-subtle">Searching…</p>
        ) : results.isError ? (
          <p className="py-6 text-center text-xs text-danger">Search failed.</p>
        ) : (results.data ?? []).length === 0 ? (
          <p className="py-6 text-center text-xs text-fg-subtle">No matches.</p>
        ) : (
          <ul className="space-y-2">
            {(results.data ?? []).map((hit) => (
              <li key={hit.message_id} className="rounded-md border border-border bg-bg-input/60 p-3">
                <p className="mb-1 flex items-center gap-2 text-2xs text-fg-subtle">
                  <span className="rounded-full border border-border px-1.5">{hit.role}</span>
                  seq {hit.seq} · {formatTime(hit.created_at)}
                  <button
                    type="button"
                    aria-label="Copy snippet"
                    className="ml-auto rounded-xs p-1 text-fg-subtle hover:text-fg"
                    onClick={async () => {
                      await navigator.clipboard.writeText(hit.content_snippet);
                      setCopied(hit.message_id);
                      setTimeout(() => setCopied(null), 1500);
                    }}
                  >
                    {copied === hit.message_id ? <Check size={12} aria-hidden /> : <Copy size={12} aria-hidden />}
                  </button>
                </p>
                <p className="text-sm leading-relaxed text-fg-muted">{hit.content_snippet}</p>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="mt-3 flex justify-end">
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
    </div>
  );
}
