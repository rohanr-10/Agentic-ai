/**
 * SSE over fetch — required by the backend's chat streaming route, which is a
 * POST (`EventSource` cannot POST or set headers). Parses `text/event-stream`
 * frames exactly as the backend emits them: `event: <type>` lines, `data:`
 * lines (multi-line joined), `: keepalive` comments ignored.
 */
import { apiBaseUrl } from "@/services/api/client";

export interface SseEvent {
  event: string;
  data: unknown;
}

export interface SseOptions {
  path: string; // relative to the API base URL
  method?: "GET" | "POST";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

/** Split a chunk into complete SSE frames on "\n\n" (or "\r\n\r\n"). */
export class FrameBuffer {
  private buffer = "";

  push(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const events: SseEvent[] = [];
    let boundary: number;
    // Normalise \r\n to \n once per append for boundary simplicity.
    while ((boundary = this.buffer.indexOf("\n\n")) !== -1) {
      const rawFrame = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      const parsed = parseFrame(rawFrame);
      if (parsed) events.push(parsed);
    }
    return events;
  }

  flush(): SseEvent[] {
    const rest = this.buffer;
    this.buffer = "";
    if (!rest.trim()) return [];
    const parsed = parseFrame(rest);
    return parsed ? [parsed] : [];
  }
}

function parseFrame(rawFrame: string): SseEvent | null {
  let eventName = "message";
  const dataLines: string[] = [];
  for (const line of rawFrame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue; // comment / keepalive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventName = value;
    else if (field === "data") dataLines.push(value);
  }
  if (dataLines.length === 0) return null;
  const raw = dataLines.join("\n");
  try {
    return { event: eventName, data: JSON.parse(raw) };
  } catch {
    return { event: eventName, data: raw };
  }
}

export interface SseStreamHandle {
  /** Resolves when the server closes or the request is aborted; rejects on network errors before first byte. */
  done: Promise<void>;
  abort(): void;
}

/**
 * Opens an SSE request and invokes `onEvent` for every frame. Auth token is
 * attached as a Bearer header (POST routes) or `token` query param (GET
 * routes such as task events, per the backend contract).
 */
export function openSse(
  options: SseOptions,
  onEvent: (event: SseEvent) => void,
  getAccessToken: () => string | null,
): SseStreamHandle {
  const controller = new AbortController();
  const external = options.signal;
  const onExternalAbort = () => controller.abort(external?.reason);
  if (external) {
    if (external.aborted) controller.abort(external.reason);
    external.addEventListener("abort", onExternalAbort, { once: true });
  }

  const done = (async () => {
    let url = `${apiBaseUrl()}${options.path}`;
    const query = new URLSearchParams();
    if (options.query) {
      for (const [key, value] of Object.entries(options.query)) {
        if (value === undefined || value === null) continue;
        query.append(key, String(value));
      }
    }
    // GET routes cannot rely on headers in browsers (EventSource semantics);
    // the backend accepts ?token= for them.
    const method = options.method ?? "POST";
    const token = getAccessToken();
    if (method === "GET" && token && !("token" in (options.query ?? {}))) {
      query.append("token", token);
    }
    const qs = query.toString();
    if (qs) url += `?${qs}`;

    const headers: Record<string, string> = { ...options.headers };
    if (method !== "GET") {
      headers.Accept = "text/event-stream";
      if (options.body !== undefined) headers["Content-Type"] = "application/json";
      if (token) headers.Authorization = `Bearer ${token}`;
    } else {
      headers.Accept = "text/event-stream";
    }

    let response: Response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body: options.body !== undefined && method !== "GET" ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      throw err;
    }

    if (!response.ok || !response.body) {
      // Surface the backend error envelope as a synthetic SSE error event so
      // stream consumers show a meaningful message.
      let message = `stream request failed (${response.status})`;
      try {
        const body = (await response.json()) as { error?: { message?: string } };
        if (body?.error?.message) message = body.error.message;
      } catch {
        /* non-JSON */
      }
      onEvent({ event: "error", data: { message } });
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const frames = new FrameBuffer();
    try {
      for (;;) {
        const { value, done: streamDone } = await reader.read();
        if (streamDone) break;
        for (const event of frames.push(decoder.decode(value, { stream: true }))) {
          onEvent(event);
        }
      }
      for (const event of frames.push(decoder.decode())) onEvent(event);
      for (const event of frames.flush()) onEvent(event);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      throw err;
    }
  })().finally(() => {
    external?.removeEventListener("abort", onExternalAbort);
  });

  return {
    done: done.then(
      () => undefined,
      () => undefined,
    ), // never rejects to the caller; errors arrive as `error` events
    abort: () => controller.abort(new DOMException("client aborted", "AbortError")),
  };
}
