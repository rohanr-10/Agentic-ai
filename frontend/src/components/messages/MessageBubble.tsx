import { memo, useCallback, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Check, Copy, FileStack, Terminal, Wrench } from "lucide-react";
import type { MessageOut } from "@/types/api";
import { cn } from "@/utils/cn";
import { formatBytes, formatTime, initials } from "@/utils/format";

function CopyButton({ getText, label }: { getText: () => string; label: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(getText());
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable */
    }
  }, [getText]);
  return (
    <button
      type="button"
      onClick={onCopy}
      aria-label={copied ? "Copied" : label}
      className={cn(
        "rounded-xs p-1.5 text-fg-subtle opacity-0 transition-all hover:bg-bg-hover hover:text-fg",
        "focus-visible:opacity-100 group-hover:opacity-100",
        copied && "opacity-100 text-success",
      )}
    >
      {copied ? <Check size={13} aria-hidden /> : <Copy size={13} aria-hidden />}
    </button>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  let rendered: string;
  try {
    rendered = JSON.stringify(value, null, 2);
  } catch {
    rendered = String(value);
  }
  return (
    <pre className="max-h-56 overflow-auto rounded-md border border-border bg-[#0d0f13] p-3">
      <code className="font-mono text-xs leading-relaxed text-fg-muted">{rendered}</code>
    </pre>
  );
}

function ToolCallBlock({ message }: { message: MessageOut }) {
  const calls = message.meta.tool_calls ?? [];
  return (
    <div className="rounded-lg border border-border bg-bg-elevated/60 px-3.5 py-3">
      <p className="mb-2 flex items-center gap-2 text-xs font-semibold text-fg">
        <Wrench size={13} className="text-accent" aria-hidden />
        {calls.length > 1 ? `${calls.length} tool calls` : "Tool call"}
        <span className="font-normal text-fg-subtle">from the assistant</span>
      </p>
      <div className="space-y-2">
        {calls.map((call) => (
          <div key={call.id} className="rounded-md border border-border bg-bg-input/60 p-2.5">
            <p className="mb-1.5 font-mono text-2xs text-accent-hover">
              {call.name} <span className="text-fg-subtle">({call.id})</span>
            </p>
            <JsonBlock value={call.arguments} />
          </div>
        ))}
      </div>
    </div>
  );
}

function ToolResultBlock({ message }: { message: MessageOut }) {
  let parsed: unknown = message.content;
  try {
    parsed = JSON.parse(message.content);
  } catch {
    parsed = message.content;
  }
  const isError = message.meta.tool_error === true;
  return (
    <details className="group/tool rounded-lg border border-border bg-bg-elevated/60">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3.5 py-2.5 text-xs font-semibold text-fg">
        <Terminal size={13} className={cn(isError ? "text-danger" : "text-success")} aria-hidden />
        <span className={cn(isError ? "text-danger" : "")}>
          {message.tool_name ?? "tool"} · {isError ? "returned an error" : "result"}
        </span>
        <span className="ml-auto text-2xs font-normal text-fg-subtle group-open:hidden">show</span>
        <span className="ml-auto hidden text-2xs font-normal text-fg-subtle group-open:inline">hide</span>
      </summary>
      <div className="px-3.5 pb-3">
        <JsonBlock value={parsed} />
      </div>
    </details>
  );
}

function Attachments({ message }: { message: MessageOut }) {
  if (message.attachments.length === 0) return null;
  return (
    <p className="mt-2 flex flex-wrap items-center gap-1.5">
      {message.attachments.map((file) => (
        <span
          key={file.file_id}
          className="inline-flex max-w-56 items-center gap-1.5 rounded-full border border-border bg-bg-elevated px-2 py-0.5 text-2xs text-fg-muted"
          title={`${file.filename} · ${file.mime_type}`}
        >
          <FileStack size={11} className="shrink-0 text-accent" aria-hidden />
          <span className="truncate">{file.filename}</span>
          <span className="shrink-0 text-fg-subtle">{formatBytes(file.size_bytes)}</span>
        </span>
      ))}
    </p>
  );
}

const MessageBubble = memo(function MessageBubble({ message }: { message: MessageOut }) {
  const isUser = message.role === "user";
  const isTool = message.role === "tool";

  if (isTool) {
    return (
      <div className="group animate-[message-in_160ms_var(--ease-out-soft)] px-3 py-1 sm:px-6">
        <div className="mx-auto max-w-3xl">
          <ToolResultBlock message={message} />
        </div>
      </div>
    );
  }

  // assistant message that carries tool calls (meta.tool_calls)
  if (message.role === "assistant" && (message.meta.tool_calls?.length ?? 0) > 0) {
    return (
      <div className="group animate-[message-in_160ms_var(--ease-out-soft)] px-3 py-1 sm:px-6">
        <div className="mx-auto max-w-3xl">
          <ToolCallBlock message={message} />
        </div>
      </div>
    );
  }

  return (
    <article
      aria-label={`${isUser ? "You" : "Assistant"} message`}
      className={cn("group animate-[message-in_160ms_var(--ease-out-soft)] px-3 py-2 sm:px-6", isUser && "flex justify-end")}
    >
      <div className={cn("mx-auto flex w-full max-w-3xl gap-3", isUser && "justify-end")}>
        {!isUser && (
          <span
            aria-hidden
            className="mt-1 flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-bg-elevated text-2xs font-semibold text-accent"
          >
            AI
          </span>
        )}
        <div className={cn("min-w-0", isUser ? "max-w-[85%]" : "flex-1")}>
          <div
            className={cn(
              "relative rounded-xl px-4 py-2.5",
              isUser
                ? "bg-accent/12 border border-accent/20 text-fg"
                : message.is_active === false
                  ? "border border-border/60 bg-bg-elevated/40 text-fg-subtle"
                  : "bg-bg-panel border border-border text-fg",
            )}
          >
            {typeof message.content === "string" && message.content.length > 0 ? (
              <div className="prose-chat">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {message.content}
                </ReactMarkdown>
              </div>
            ) : (
              <p className="text-sm italic text-fg-subtle">(empty response)</p>
            )}
            {message.is_active === false && (
              <p className="mt-1 text-2xs text-fg-subtle">superseded by a newer response</p>
            )}
            <Attachments message={message} />
            {isUser && (
              <span className="absolute -left-9 top-1 opacity-0 transition-opacity group-hover:opacity-100">
                <CopyButton getText={() => message.content} label="Copy message" />
              </span>
            )}
          </div>
          <p
            className={cn(
              "mt-1 flex items-center gap-1.5 text-2xs text-fg-subtle",
              isUser && "justify-end",
            )}
          >
            <span>{formatTime(message.created_at)}</span>
            {message.edited_at && <span title="edited">· edited</span>}
            {message.meta.interrupted && (
              <span title="the stream was interrupted; partial output saved">· interrupted</span>
            )}
            {message.meta.model && <span>· {message.meta.model}</span>}
            {message.meta.usage?.completion_tokens != null && (
              <span title="completion tokens">· {message.meta.usage.completion_tokens} tok</span>
            )}
          </p>
        </div>
        {isUser && (
          <span
            aria-hidden
            className="mt-1 flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-bg-elevated text-2xs font-semibold text-fg-muted"
          >
            {initials("You")}
          </span>
        )}
      </div>
    </article>
  );
});

export default MessageBubble;

/** Streaming placeholder bubble: assistant avatar + growing text + caret. */
export function StreamingBubble({ text, phase }: { text: string; phase: "connecting" | "streaming" | "tools" }) {
  return (
    <article aria-live="polite" className="px-3 py-2 sm:px-6" data-testid="streaming-bubble">
      <div className="mx-auto flex w-full max-w-3xl gap-3">
        <span
          aria-hidden
          className="mt-1 flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-bg-elevated text-2xs font-semibold text-accent"
        >
          AI
        </span>
        <div className="min-w-0 flex-1">
          <div className="rounded-xl border border-border bg-bg-panel px-4 py-2.5">
            {phase === "connecting" && (
              <p className="flex items-center gap-2 text-sm text-fg-subtle">
                <span className="flex gap-1" aria-hidden>
                  <span className="size-1.5 animate-[pulse-dot_1.2s_ease-in-out_infinite] rounded-full bg-accent" />
                  <span className="size-1.5 animate-[pulse-dot_1.2s_ease-in-out_0.2s_infinite] rounded-full bg-accent" />
                  <span className="size-1.5 animate-[pulse-dot_1.2s_ease-in-out_0.4s_infinite] rounded-full bg-accent" />
                </span>
                Contacting the model…
              </p>
            )}
            {phase === "tools" && (
              <p className="text-sm text-fg-muted">
                <span className="font-medium text-fg">Running tools…</span> results will follow.
              </p>
            )}
            {phase === "streaming" && (
              <div className="prose-chat">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
                <span aria-hidden className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-[caret-blink_1s_step-end_infinite] bg-accent" />
              </div>
            )}
          </div>
        </div>
      </div>
    </article>
  );
}

export function SystemNotice({ children }: { children: ReactNode }) {
  return (
    <div role="status" className="px-3 py-2 sm:px-6">
      <p className="mx-auto max-w-3xl rounded-md border border-border/70 bg-bg-elevated/50 px-3.5 py-2 text-xs text-fg-muted">
        {children}
      </p>
    </div>
  );
}
