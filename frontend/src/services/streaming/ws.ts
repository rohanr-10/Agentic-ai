/**
 * WebSocket client for /api/v1/ws realtime events.
 *
 * Backend protocol (app/api/v1/ws.py):
 *   connect:        /api/v1/ws?token=<access_token>  (query param — required)
 *   send:           {"action": "subscribe"|"unsubscribe", "topics": [...]}
 *   receive:        {"topic": ..., "type": ..., "data": ..., "timestamp": ...}
 *   error frame:    {"type": "error", "data": {"message": ...}}
 *   close codes:    4401 invalid/missing token, 1011 internal error.
 *
 * Auto-reconnect with capped exponential backoff, only while enabled. In-memory
 * auth token provider injected by the auth store; closures are dropped via the
 * explicit `close()`.
 */
export interface RealtimeEvent {
  topic: string;
  type: string;
  data: Record<string, unknown>;
  timestamp: string;
}

export interface RealtimeClientOptions {
  url: string;
  getAccessToken: () => string | null;
  onEvent: (event: RealtimeEvent) => void;
  /** When false/absent the client does not connect. */
  enabled: () => boolean;
  onStatusChange?: (status: RealtimeStatus) => void;
}

export type RealtimeStatus = "disabled" | "connecting" | "open" | "reconnecting" | "closed";

const MAX_BACKOFF_MS = 8_000;
const BASE_BACKOFF_MS = 500;

export class RealtimeClient {
  private socket: WebSocket | null = null;
  private topics = new Set<string>();
  private attempts = 0;
  private backoffTimer: ReturnType<typeof setTimeout> | null = null;
  private status: RealtimeStatus = "disabled";
  private closed = false;

  constructor(private readonly options: RealtimeClientOptions) {}

  private setStatus(status: RealtimeStatus) {
    if (this.status === status) return;
    this.status = status;
    this.options.onStatusChange?.(status);
  }
  connect(): void {
    if (this.closed) return;
    if (!this.options.enabled()) {
      this.setStatus("disabled");
      return;
    }
    const token = this.options.getAccessToken();
    if (!token) {
      this.setStatus("disabled");
      return;
    }
    const separator = this.options.url.includes("?") ? "&" : "?";
    const url = `${this.options.url}${separator}token=${encodeURIComponent(token)}`;
    this.setStatus(this.attempts === 0 ? "connecting" : ("reconnecting" as RealtimeStatus));

    let socket: WebSocket;
    try {
      socket = new WebSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.attempts = 0;
      this.setStatus("open");
      if (this.topics.size > 0) {
        this.sendRaw({ action: "subscribe", topics: [...this.topics] });
      }
    };

    socket.onmessage = (message) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(message.data as string);
      } catch {
        return;
      }
      if (!parsed || typeof parsed !== "object") return;
      const frame = parsed as Partial<RealtimeEvent> & { type?: string };
      if (frame.type === "error" || !frame.topic || !frame.type) return; // control frames
      this.options.onEvent({
        topic: frame.topic,
        type: frame.type,
        data: (frame.data ?? {}) as Record<string, unknown>,
        timestamp: frame.timestamp ?? new Date().toISOString(),
      });
    };

    socket.onclose = (event) => {
      this.socket = null;
      if (this.closed) {
        this.setStatus("closed");
        return;
      }
      // 4401: token invalid — do not spin; the auth layer will re-enable on login.
      if (event.code === 4401) {
        this.setStatus("disabled");
        return;
      }
      this.scheduleReconnect();
    };

    socket.onerror = () => {
      /* onclose follows; nothing to do here */
    };
  }

  private scheduleReconnect() {
    if (this.closed) return;
    this.setStatus("reconnecting");
    const delay = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** this.attempts);
    this.attempts += 1;
    this.backoffTimer = setTimeout(() => {
      this.backoffTimer = null;
      this.connect();
    }, delay);
  }

  private sendRaw(payload: unknown): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload));
    }
  }

  subscribe(topics: string[]): void {
    const fresh = topics.filter((t) => !this.topics.has(t));
    if (fresh.length === 0) return;
    for (const t of fresh) this.topics.add(t);
    if (this.status === "disabled") {
      this.connect();
      return;
    }
    // The onopen handler resends the full topic set; only send when open.
    this.sendRaw({ action: "subscribe", topics: fresh });
  }

  unsubscribe(topics: string[]): void {
    const known = topics.filter((t) => this.topics.has(t));
    if (known.length === 0) return;
    for (const t of known) this.topics.delete(t);
    this.sendRaw({ action: "unsubscribe", topics: known });
  }

  close(): void {
    this.closed = true;
    if (this.backoffTimer) clearTimeout(this.backoffTimer);
    this.backoffTimer = null;
    this.socket?.close(1000, "client closed");
    this.socket = null;
    this.setStatus("closed");
  }
}
