import { useQuery } from "@tanstack/react-query";
import { get } from "@/services/api/client";
import type {
  ApprovalOut,
  ConversationOut,
  FileOut,
  MemoryOut,
  ModelConfigOut,
  NotificationOut,
  Page,
  SearchHit,
  TaskDetailOut,
  TaskExecutionOut,
  TaskOut,
  UserOut,
} from "@/types/api";

/** Shared query keys. */
export const qk = {
  me: ["auth", "me"] as const,
  models: ["models"] as const,
  modelHealth: (id: string) => ["models", id, "health"] as const,
  conversations: (filter?: string) => ["conversations", filter ?? "all"] as const,
  conversation: (id: string | undefined) => ["conversations", id] as const,
  messages: (id: string | undefined) => ["conversations", id, "messages"] as const,
  tasks: (filter?: { status?: string; conversationId?: string }) => ["tasks", filter ?? "all"] as const,
  task: (id: string | undefined) => ["tasks", id] as const,
  executions: (id: string | undefined) => ["tasks", id, "executions"] as const,
  approvals: (filter?: string) => ["approvals", filter ?? "all"] as const,
  files: (filter?: string) => ["files", filter ?? "all"] as const,
  memory: (filter?: string) => ["memory", filter ?? "all"] as const,
  tools: ["tools"] as const,
  notifications: ["notifications"] as const,
};

export function useModels() {
  return useQuery({
    queryKey: qk.models,
    queryFn: () => get<ModelConfigOut[]>("/models"),
    staleTime: 30_000,
  });
}

export function useConversations(search?: string) {
  return useQuery({
    queryKey: qk.conversations(search || undefined),
    queryFn: () =>
      get<ConversationOut[]>("/conversations", {
        limit: 200,
        q: search?.trim() || undefined,
      }),
    staleTime: 10_000,
  });
}

export function useTask(id: string | undefined, options?: { refetchInterval?: number | false }) {
  return useQuery({
    queryKey: qk.task(id),
    queryFn: () => get<TaskDetailOut>(`/tasks/${id}`),
    enabled: id !== undefined,
    refetchInterval: options?.refetchInterval ?? false,
    refetchOnWindowFocus: true,
  });
}

export function useTasks(filter?: { status?: string; conversationId?: string }) {
  return useQuery({
    queryKey: qk.tasks(filter),
    queryFn: () =>
      get<Page<TaskOut>>("/tasks", {
        limit: 200,
        status: filter?.status,
        conversation_id: filter?.conversationId,
      }),
    staleTime: 5_000,
  });
}

export function useExecutions(taskId: string | undefined) {
  return useQuery({
    queryKey: qk.executions(taskId),
    queryFn: () => get<TaskExecutionOut[]>(`/tasks/${taskId}/executions`, { limit: 500 }),
    enabled: taskId !== undefined,
  });
}

export function useApprovals(status?: string) {
  return useQuery({
    queryKey: qk.approvals(status),
    queryFn: () => get<Page<ApprovalOut>>("/approvals", { status, limit: 200 }),
    staleTime: 5_000,
  });
}

export function useFiles(status?: string) {
  return useQuery({
    queryKey: qk.files(status),
    queryFn: () => get<Page<FileOut>>("/files", { status, limit: 200 }),
    staleTime: 5_000,
    // extraction is a background job; there is no per-file status endpoint,
    // so poll while any row is still pending/processing
    refetchInterval: (query) => {
      const items = query.state.data?.items;
      const active = items?.some((f) => f.status === "pending" || f.status === "processing");
      return active ? 2000 : false;
    },
  });
}

export function useMemoryList() {
  return useQuery({
    queryKey: qk.memory(),
    queryFn: () => get<Page<MemoryOut>>("/memory", { limit: 200 }),
    staleTime: 10_000,
  });
}

export function useNotifications() {
  return useQuery({
    queryKey: qk.notifications,
    queryFn: () => get<Page<NotificationOut>>("/notifications", { limit: 50 }),
    // poll while the app is open; WS events trigger refetches too
    refetchInterval: 30_000,
  });
}

export function useProfile() {
  return useQuery({
    queryKey: qk.me,
    queryFn: () => get<UserOut>("/auth/me"),
    staleTime: Infinity,
  });
}

export function useConversationSearch(conversationId: string | undefined, query: string, enabled: boolean) {
  return useQuery({
    queryKey: ["conversations", conversationId, "search", query] as const,
    queryFn: () => get<SearchHit[]>(`/conversations/${conversationId}/search`, { q: query, limit: 50 }),
    enabled: enabled && conversationId !== undefined && query.trim().length > 0,
  });
}
