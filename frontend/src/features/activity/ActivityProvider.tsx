import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import type { UUID } from "@/types/api";
import { RealtimeClient, type RealtimeEvent, type RealtimeStatus } from "@/services/streaming/ws";
import { authStore } from "@/features/auth/authStore";
import { useAuth } from "@/features/auth/useAuth";

export interface ActivityEntry {
  id: number;
  at: string; // ISO
  kind:
    | "task.status"
    | "task.plan"
    | "task.step"
    | "task.control"
    | "task.note"
    | "approval.requested"
    | "notification"
    | "stream"
    | "connection";
  title: string;
  detail?: string;
  taskId?: UUID;
  conversationId?: UUID;
  level?: "info" | "success" | "warning" | "error";
}

interface ActivityContextValue {
  /** Whether the panel is currently visible (any viewport). */
  activityOpen: boolean;
  toggle(): void;
  open(): void;
  /** Number of approvals awaiting a decision (drives badges). */
  pendingCount: number;
  /** Most recent events first; newest at index 0. */
  feed: ActivityEntry[];
  push(entry: Omit<ActivityEntry, "id" | "at"> & { at?: string }): void;
  connection: RealtimeStatus;
  /** Task the panel should focus on (set when opening a task from a toast). */
  focusTaskId: UUID | null;
  setFocusTask(id: UUID | null): void;
}

const ActivityContext = createContext<ActivityContextValue | null>(null);

export function useActivity(): ActivityContextValue {
  const context = useContext(ActivityContext);
  if (!context) throw new Error("useActivity must be used inside ActivityProvider");
  return context;
}

const MAX_FEED = 120;

export function ActivityProvider({
  children,
  activityAvailable,
  activityOpen,
  onToggleActivity,
}: {
  children: ReactNode;
  activityAvailable: boolean;
  activityOpen: boolean;
  onToggleActivity: () => void;
}) {
  const { isAuthenticated } = useAuth();
  const [feed, setFeed] = useState<ActivityEntry[]>([]);
  // unauthenticated users trivially have no pending approvals: derive the
  // badge value instead of resetting it inside an effect
  const [polledPending, setPolledPending] = useState(0);
  const pendingCount = isAuthenticated ? polledPending : 0;
  const setPendingCount = setPolledPending;
  const [connection, setConnection] = useState<RealtimeStatus>("disabled");
  const [focusTaskId, setFocusTaskId] = useState<UUID | null>(null);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const nextId = useRef(1);

  const push = useMemo<ActivityContextValue["push"]>(
    () => (entry) => {
      setFeed((current) => {
        const withId: ActivityEntry = {
          ...entry,
          id: nextId.current++,
          at: entry.at ?? new Date().toISOString(),
        };
        return [withId, ...current].slice(0, MAX_FEED);
      });
    },
    [],
  );

  // ---- pending approvals counter ---------------------------------------
  // Async polling: all setState calls happen in callbacks (async resolution
  // or timer), never synchronously in the effect body.
  useEffect(() => {
    if (!isAuthenticated) return;
    let cancelled = false;
    const load = async () => {
      try {
        const { get } = await import("@/services/api/client");
        const page = await get<{ total: number; items: unknown[] }>("/approvals", {
          status: "pending",
          limit: 1,
        });
        if (!cancelled) setPendingCount(page.total);
      } catch {
        if (!cancelled) setPendingCount(0); // fail silent — badge only
      }
    };
    void load();
    const timer = setInterval(load, 20_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [isAuthenticated, feed.length, setPendingCount]);

  // ---- realtime WebSocket ----------------------------------------------
  const clientRef = useRef<RealtimeClient | null>(null);

  useEffect(() => {
    if (!isAuthenticated) return;
    const client = new RealtimeClient({
      url: import.meta.env.VITE_WS_URL ?? "/api/v1/ws",
      getAccessToken: () => authStore.getAccessToken(),
      enabled: () => authStore.getAccessToken() !== null,
      onStatusChange: setConnection,
      onEvent: (event: RealtimeEvent) => {
        if (event.topic.startsWith("user:")) {
          // The backend publishes task transitions to the user's topic as
          // type "task.status" (states.transition) and durable notifications
          // as "notification". Handle both; notifications are marked read.
          if (event.type === "notification") {
            const id = typeof event.data.id === "string" ? event.data.id : null;
            push({
              kind: "notification",
              title: NOTIFICATION_TITLES[event.data.kind as string] ?? "Notification",
              detail: describeNotification(event.data),
              level: "info",
            });
            if (id) {
              void (async () => {
                try {
                  const { post } = await import("@/services/api/client");
                  await post(`/notifications/${id}/read`, {});
                } catch {
                  /* marked read on next visit */
                }
              })();
            }
            void queryClient.invalidateQueries({ queryKey: ["notifications"] });
            return;
          }
          if (event.type.startsWith("task.")) {
            const taskId = typeof event.data.task_id === "string" ? event.data.task_id : undefined;
            handleTaskEvent(event, taskId, push, queryClient);
            return;
          }
          return;
        }
        if (event.topic.startsWith("task:")) {
          handleTaskEvent(event, event.topic.slice("task:".length), push, queryClient);
        }
      },
    });
    client.connect();
    clientRef.current = client;
    // The user topic receives all task transitions (states.transition) and
    // durable notifications — subscribe as soon as we know the identity.
    const { user } = authStore.getSnapshot();
    if (user) {
      client.subscribe([`user:${user.id}`]);
    } else {
      // login may complete before the WS opens; retry on the next tick
      const retry = setTimeout(() => {
        const current = authStore.getSnapshot().user;
        if (current && clientRef.current === client) {
          client.subscribe([`user:${current.id}`]);
        }
      }, 1500);
      return () => {
        clearTimeout(retry);
        client.close();
        clientRef.current = null;
      };
    }
    return () => {
      client.close();
      clientRef.current = null;
    };
  }, [isAuthenticated, push, queryClient]);

  const value = useMemo<ActivityContextValue>(
    () => ({
      activityOpen,
      toggle: onToggleActivity,
      open: onToggleActivity, // single toggle behaviour; opening when closed
      pendingCount,
      feed,
      push,
      connection,
      focusTaskId,
      setFocusTask: (id) => {
        setFocusTaskId(id);
        if (id) navigate(`/tasks/${id}`);
      },
    }),
    [activityOpen, onToggleActivity, pendingCount, feed, push, connection, focusTaskId, navigate],
  );

  // keep unused import reference away from tree-shaking issues
  void activityAvailable;
  return <ActivityContext.Provider value={value}>{children}</ActivityContext.Provider>;
}

const NOTIFICATION_TITLES: Record<string, string> = {
  approval_requested: "Approval requested",
  task_completed: "Task completed",
  task_failed: "Task failed",
  task_cancelled: "Task cancelled",
  file_ready: "File ready",
  file_failed: "File processing failed",
  system: "System notice",
};

const STATUS_HINTS: Record<string, string> = {
  planning: "The orchestrator is composing a plan.",
  awaiting_approval: "Waiting for your decision on a plan or step.",
  ready: "Plan approved — execution starting.",
  running: "Executing steps in the sandbox.",
  verifying: "Checking results against the objective.",
  completed: "Objective delivered.",
  failed: "The task ended with an error.",
  cancelled: "The task was cancelled.",
  retrying: "Verification failed — replanning.",
  paused: "Execution paused.",
};

function levelForStatus(status: string): ActivityEntry["level"] {
  if (status === "completed") return "success";
  if (status === "failed") return "error";
  if (status === "awaiting_approval" || status === "paused") return "warning";
  return "info";
}

function describeNotification(data: Record<string, unknown>): string | undefined {
  const title = typeof data.title === "string" ? data.title : null;
  const taskId = typeof data.task_id === "string" ? data.task_id : null;
  if (title) return title;
  if (taskId) return `Task ${taskId.slice(0, 8)}…`;
  return undefined;
}

/** Shared handling for task events, whichever topic delivered them. */
function handleTaskEvent(
  event: RealtimeEvent,
  taskId: string | undefined,
  push: ActivityContextValue["push"],
  queryClient: ReturnType<typeof useQueryClient>,
): void {
  const id = taskId ?? String(event.data.task_id ?? "unknown");
  if (event.type === "task.status") {
    const status = String(event.data.status ?? "updated");
    push({
      kind: "task.status",
      taskId: id,
      title: `Task ${status.replace(/_/g, " ")}`,
      detail: STATUS_HINTS[status] ?? undefined,
      level: levelForStatus(status),
    });
    if (["completed", "failed", "cancelled"].includes(status)) {
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["tasks", id] });
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    }
  } else if (event.type === "task.plan") {
    push({
      kind: "task.plan",
      taskId: id,
      title: `Plan v${String(event.data.plan_version ?? "?")} · ${String(event.data.steps ?? "?")} steps`,
      level: "info",
    });
  } else if (event.type === "task.step") {
    push({
      kind: "task.step",
      taskId: id,
      title: `Step ${String(event.data.step_id ?? "?")} ${String(event.data.status ?? "")}`,
      level: String(event.data.status) === "succeeded" ? "success" : "info",
    });
  } else if (event.type === "task.control") {
    push({ kind: "task.control", taskId: id, title: `Control: ${String(event.data.action ?? "?")}`, level: "info" });
  } else if (event.type === "task.note") {
    push({ kind: "task.note", taskId: id, title: "New instruction injected", level: "info" });
  } else if (event.type === "notification" && event.data.kind === "approval_requested") {
    push({ kind: "approval.requested", taskId: id, title: "Approval requested", level: "warning" });
  }
  void queryClient.invalidateQueries({ queryKey: ["tasks", id] });
}
