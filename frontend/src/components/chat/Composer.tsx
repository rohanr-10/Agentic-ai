import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { ArrowUp, FileStack, Square } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { ModelSelector } from "@/components/models/ModelSelector";
import { cn } from "@/utils/cn";
import type { FileOut } from "@/types/api";
import { formatBytes } from "@/utils/format";

export interface ComposerState {
  text: string;
  sending: boolean;
}

export interface ComposerProps {
  onSend: (text: string, modelOverride: string | null) => void;
  onStop?: () => void;
  streaming: boolean;
  disabled?: boolean;
  disabledReason?: string;
  placeholder?: string;
  selectedModelId: string | null;
  onModelSelect: (id: string | null) => void;
  attachments: FileOut[];
  onAttach: (files: FileList) => void;
  onRemoveAttachment: (fileId: string) => void;
  uploadBusy?: boolean;
}

export function Composer({
  onSend,
  onStop,
  streaming,
  disabled,
  disabledReason,
  placeholder = "Message the assistant…  (Enter to send, Shift+Enter for a new line)",
  selectedModelId,
  onModelSelect,
  attachments,
  onAttach,
  onRemoveAttachment,
  uploadBusy,
}: ComposerProps) {
  const [text, setText] = useState("");
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // auto-grow textarea (cap at ~40vh)
  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, Math.floor(window.innerHeight * 0.4))}px`;
  }, [text]);

  useEffect(() => {
    if (!streaming) areaRef.current?.focus();
  }, [streaming]);

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled || streaming) return;
    onSend(trimmed, selectedModelId);
    setText("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="border-t border-border bg-gradient-to-b from-transparent to-bg/60 px-3 pb-3 pt-2 sm:px-6">
      <div className="mx-auto max-w-3xl">
        {attachments.length > 0 && (
          <ul className="mb-2 flex flex-wrap gap-1.5" aria-label="Attachments">
            {attachments.map((file) => (
              <li
                key={file.id}
                className="inline-flex max-w-64 items-center gap-1.5 rounded-full border border-border bg-bg-elevated py-1 pl-2.5 pr-1 text-2xs text-fg-muted"
              >
                <FileStack size={11} className="shrink-0 text-accent" aria-hidden />
                <span className="truncate">{file.original_filename}</span>
                <span className="shrink-0 text-fg-subtle">{formatBytes(file.size_bytes)}</span>
                <button
                  type="button"
                  aria-label={`Remove ${file.original_filename}`}
                  onClick={() => onRemoveAttachment(file.id)}
                  className="rounded-full p-0.5 text-fg-subtle transition-colors hover:text-danger"
                >
                  <Square size={9} aria-hidden className="pointer-events-none" />
                </button>
              </li>
            ))}
          </ul>
        )}

        <div
          className={cn(
            "rounded-xl border border-border bg-bg-input transition-colors",
            "focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/20",
            disabled && "opacity-60",
          )}
        >
          <textarea
            ref={areaRef}
            rows={1}
            value={text}
            disabled={disabled}
            placeholder={disabled ? disabledReason ?? "Unavailable" : placeholder}
            aria-label="Message"
            onKeyDown={onKeyDown}
            onChange={(e) => setText(e.target.value)}
            className="block max-h-[40vh] w-full resize-none bg-transparent px-3.5 pb-1 pt-3 text-md leading-relaxed text-fg placeholder:text-fg-subtle focus:outline-none disabled:cursor-not-allowed"
          />
          <div className="flex items-center justify-between gap-2 px-2 pb-2 pt-1">
            <div className="flex items-center gap-1">
              <input
                ref={fileRef}
                type="file"
                multiple
                hidden
                onChange={(e) => {
                  if (e.target.files?.length) onAttach(e.target.files);
                  e.target.value = "";
                }}
                accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.json,.py,.js,.ts,.html,.css,.yaml,.yml,.toml,.xml,.log,.jpeg,.jpg,.png"
              />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => fileRef.current?.click()}
                loading={uploadBusy}
                aria-label="Attach files"
                title="Attach files"
                className="size-8 p-0"
              >
                {!uploadBusy && <FileStack size={15} aria-hidden />}
              </Button>
            </div>
            <div className="flex items-center gap-2">
              <ModelSelector
                size="xs"
                selectedId={selectedModelId}
                onSelect={onModelSelect}
                compact
              />
              {streaming ? (
                <Button
                  variant="danger"
                  size="sm"
                  onClick={onStop}
                  aria-label="Stop generating"
                  title="Stop generating (the partial reply is kept)"
                >
                  <Square size={13} className="mr-1" aria-hidden />
                  Stop
                </Button>
              ) : (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={submit}
                  disabled={disabled || text.trim().length === 0}
                  aria-label="Send message"
                  className="size-8 p-0"
                >
                  <ArrowUp size={16} aria-hidden />
                </Button>
              )}
            </div>
          </div>
        </div>
        <p className="mt-1.5 px-1 text-2xs text-fg-subtle">
          {disabled
            ? disabledReason ?? ""
            : "Streaming replies come from the backend SSE route. Attachments are extracted server-side."}
        </p>
      </div>
    </div>
  );
}
