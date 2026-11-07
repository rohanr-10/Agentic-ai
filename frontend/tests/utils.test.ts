import { describe, expect, it, vi } from "vitest";
import { formatBytes, formatDuration, formatRelative, initials, truncate } from "@/utils/format";

describe("format utils", () => {
  it("formats byte sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MiB");
    expect(formatBytes(-1)).toBe("—");
  });

  it("formats durations", () => {
    expect(formatDuration(45)).toBe("45 ms");
    expect(formatDuration(2500)).toBe("2.5 s");
    expect(formatDuration(null)).toBe("—");
  });

  it("formats relative times", () => {
    const now = new Date("2026-11-01T12:00:00Z");
    expect(formatRelative("2026-11-01T11:59:40Z", now)).toBe("just now");
    expect(formatRelative("2026-11-01T11:30:00Z", now)).toContain("30");
    expect(formatRelative(undefined)).toBe("—");
  });

  it("computes initials", () => {
    expect(initials("Ada Lovelace")).toBe("AL");
    expect(initials("Cher")).toBe("CH");
    expect(initials("   ")).toBe("?");
  });

  it("truncates with ellipsis", () => {
    expect(truncate("abcdef", 4)).toBe("abc…");
    expect(truncate("abc", 4)).toBe("abc");
  });
});

describe("authStore", () => {
  it("keeps tokens in memory and notifies subscribers", async () => {
    const { authStore } = await import("@/features/auth/authStore");
    const listener = vi.fn();
    const unsubscribe = authStore.subscribe(listener);
    authStore.applyTokens({
      access_token: "a",
      refresh_token: "r",
      token_type: "bearer",
      expires_in: 1800,
      user: {
        id: "u1",
        email: "x@y.z",
        display_name: "X",
        role: "user",
        is_active: true,
        created_at: "2026-01-01T00:00:00Z",
        last_login_at: null,
      },
    });
    expect(listener).toHaveBeenCalled();
    expect(authStore.getAccessToken()).toBe("a");
    expect(authStore.getSnapshot().user?.email).toBe("x@y.z");
    authStore.clear();
    unsubscribe();
    expect(authStore.getAccessToken()).toBeNull();
  });
});

describe("RealtimeClient", () => {
  it("does not connect when disabled and closes cleanly", async () => {
    const { RealtimeClient } = await import("@/services/streaming/ws");
    const events: unknown[] = [];
    const client = new RealtimeClient({
      url: "/api/v1/ws",
      getAccessToken: () => null,
      enabled: () => false,
      onEvent: (event) => events.push(event),
    });
    client.connect();
    client.close();
    expect(events).toHaveLength(0);
  });
});
