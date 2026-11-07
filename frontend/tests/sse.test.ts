import { describe, expect, it } from "vitest";
import { FrameBuffer, openSse, type SseEvent } from "@/services/streaming/sse";

describe("SSE frame parsing", () => {
  it("parses event/data frames exactly as the backend emits them", () => {
    const buffer = new FrameBuffer();
    const events = buffer.push(
      'event: message.created\ndata: {"message_id":"m1","role":"user","seq":1}\n\n' +
        'event: delta\ndata: {"text":"Hel"}\n\n' +
        'event: delta\ndata: {"text":"lo"}\n\n' +
        ": keepalive\n\n" +
        'event: done\ndata: {}\n\n',
    );
    expect(events.map((e) => e.event)).toEqual([
      "message.created",
      "delta",
      "delta",
      "done",
    ]);
    expect((events[0]!.data as { message_id: string }).message_id).toBe("m1");
    expect((events[1]!.data as { text: string }).text).toBe("Hel");
  });

  it("handles multi-line data payloads (repeated data: lines)", () => {
    const buffer = new FrameBuffer();
    const events = buffer.push('event: snapshot\ndata: {"a":\ndata:  1}\n\n');
    expect(events).toHaveLength(1);
    expect(events[0]!.event).toBe("snapshot");
    expect(events[0]!.data).toEqual({ a: 1 });
  });

  it("survives chunk splitting mid-frame", () => {
    const buffer = new FrameBuffer();
    const first = buffer.push('event: delta\ndata: {"text":"par');
    expect(first).toHaveLength(0); // incomplete frame stays buffered
    const second = buffer.push('tial"}\n\n');
    expect(second).toHaveLength(1);
    expect((second[0]!.data as { text: string }).text).toBe("partial");
  });

  it("ignores comment-only frames", () => {
    const buffer = new FrameBuffer();
    const events = buffer.push(": this is a keepalive\n\n\n\n");
    expect(events).toHaveLength(0);
  });
});

describe("openSse", () => {
  it("emits a synthetic error event for non-2xx responses", async () => {
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ error: { code: "not_found", message: "task not found" } }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      })) as typeof fetch;

    const received: SseEvent[] = [];
    const handle = openSse(
      { path: "/tasks/000/events", method: "GET" },
      (event) => received.push(event),
      () => "token",
    );
    await handle.done;
    expect(received).toHaveLength(1);
    expect(received[0]!.event).toBe("error");
    expect((received[0]!.data as { message: string }).message).toBe("task not found");
  });

  it("appends the access token as ?token= for GET streams", async () => {
    let capturedUrl = "";
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      capturedUrl = String(input);
      return new Response('event: done\ndata: {}\n\n', {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    }) as typeof fetch;

    const handle = openSse(
      { path: "/tasks/abc/events", method: "GET" },
      () => {},
      () => "the-token",
    );
    await handle.done;
    expect(capturedUrl).toContain("token=the-token");
  });
});
