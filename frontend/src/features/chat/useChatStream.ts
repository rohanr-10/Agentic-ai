import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  AttachmentOut,
  ChatResponse,
  ConversationOut,
  FileOut,
  MessageOut,
  UUID,
} from "@/types/api";
import { get, post } from "@/services/api/client";
import { openSse, type SseStreamHandle } from "@/services/streaming/sse";
import { authStore } from "@/features/auth/authStore";

export interface ToolEvent {
  id: string;
  name: string;
  kind: "call" | "result";
  message_id?: string;
}

export interface StreamState {
  phase: "idle" | "connecting" | "streaming" | "tools";
  text: string;
  events: ToolEvent[];
  error: string | null;
}

export interface UseChatStreamResult {
  state: StreamState;
  send: (
    conversationId: UUID,
    text: string,
    modelOverride: UUID | null,
    attachmentIds: UUID[],
  ) => Promise<void>;
  stop: () => void;
  streaming: boolean;
}

export function useChatStream(
  onActivity: (line: string) => void,
  onFinished: () => void,
): UseChatStreamResult {
  const [state, setState] = useState<StreamState>({
    phase: "idle",
    text: "",
    events: [],
    error: null,
  });
  const handleRef = useRef<SseStreamHandle | null>(null);

  // latest-callback pattern: stable references for the stream callbacks
  const latestActivity = useRef(onActivity);
  const latestFinished = useRef(onFinished);
  useEffect(() => {
    latestActivity.current = onActivity;
    latestFinished.current = onFinished;
  }, [onActivity, onFinished]);

  const stop = useCallback(() => {
    handleRef.current?.abort();
    handleRef.current = null;
    setState((s) => ({ ...s, phase: "idle" }));
    latestFinished.current();
  }, []);

  useEffect(() => () => handleRef.current?.abort(), []);

  const send = useCallback(
    async (conversationId: UUID, text: string, modelOverride: UUID | null, attachmentIds: UUID[]) => {
      if (handleRef.current) return; // one at a time
      const clientRequestId =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `c-${Date.now()}-${Math.random().toString(16).slice(2)}`;

      setState({ phase: "connecting", text: "", events: [], error: null });

      handleRef.current = openSse(
        {
          path: `/conversations/${conversationId}/messages/stream`,
          method: "POST",
          body: {
            content: text,
            attachments: attachmentIds,
            client_request_id: clientRequestId,
            ...(modelOverride ? { model_config_id: modelOverride } : {}),
          },
        },
        (event) => {
          if (event.event === "delta") {
            const chunk = (event.data as { text?: string }).text ?? "";
            setState((s) => ({ ...s, phase: "streaming", text: s.text + chunk }));
          } else if (event.event === "message.created") {
            latestActivity.current("message sent");
          } else if (event.event === "tool_call") {
            const data = event.data as { id: string; name: string };
            setState((s) => ({
              ...s,
              phase: "tools",
              events: [...s.events, { id: data.id, name: data.name, kind: "call" }],
            }));
            latestActivity.current(`tool call: ${data.name}`);
          } else if (event.event === "tool_result") {
            const data = event.data as { id: string; name: string; message_id?: string };
            setState((s) => ({
              ...s,
              events: [
                ...s.events,
                { id: data.id, name: data.name, kind: "result", message_id: data.message_id },
              ],
            }));
            latestActivity.current(`tool result: ${data.name}`);
          } else if (event.event === "message.completed") {
            latestActivity.current("reply completed");
          } else if (event.event === "error") {
            const message = (event.data as { message?: string })?.message ?? "stream failed";
            setState((s) => ({ ...s, error: message }));
          } else if (event.event === "done") {
            handleRef.current?.abort();
            handleRef.current = null;
            setState({ phase: "idle", text: "", events: [], error: null });
            latestFinished.current();
          }
        },
        () => authStore.getAccessToken(),
      );

      void handleRef.current.done.finally(() => {
        handleRef.current = null;
      });
    },
    [],
  );

  return {
    state,
    send,
    stop,
    streaming: state.phase !== "idle",
  };
}

export function useMessages(conversationId: UUID | undefined) {
  return useQuery({
    queryKey: ["conversations", conversationId, "messages"],
    queryFn: () => get<MessageOut[]>(`/conversations/${conversationId}/messages`, { limit: 200 }),
    enabled: conversationId !== undefined,
  });
}

/** Upload one or more files (optionally bound to a conversation). */
export async function uploadFiles(
  files: FileList | File[],
  conversationId: UUID | null,
): Promise<FileOut[]> {
  const uploaded: FileOut[] = [];
  for (const file of Array.from(files)) {
    const form = new FormData();
    form.append("file", file);
    const query = conversationId ? `?conversation_id=${conversationId}` : "";
    const record = await post<FileOut>(`/files${query}`, undefined, { formData: form });
    uploaded.push(record);
  }
  return uploaded;
}

export function attachmentsOf(message: MessageOut): AttachmentOut[] {
  return message.attachments ?? [];
}

export function isConversationArchived(data: ConversationOut | undefined): boolean {
  return data?.status === "archived";
}

export function useQueryInvalidationAfterSend() {
  const queryClient = useQueryClient();
  return useMemo(
    () => ({
      conversation: (id: UUID) => {
        void queryClient.invalidateQueries({ queryKey: ["conversations", id, "messages"] });
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      },
    }),
    [queryClient],
  );
}

/** Fallback: send via the non-streaming route (also used to retry on stream failure). */
export async function sendMessageNonStreaming(
  conversationId: UUID,
  text: string,
  modelOverride: UUID | null,
  attachmentIds: UUID[],
  clientRequestId: string,
): Promise<ChatResponse> {
  return post<ChatResponse>(
    `/conversations/${conversationId}/messages`,
    {
      content: text,
      attachments: attachmentIds,
      client_request_id: clientRequestId,
      ...(modelOverride ? { model_config_id: modelOverride } : {}),
    },
    { retryOn401: true },
  );
}
