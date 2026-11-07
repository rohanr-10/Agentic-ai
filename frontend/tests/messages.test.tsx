import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import MessageBubble, { StreamingBubble } from "@/components/messages/MessageBubble";
import type { MessageOut } from "@/types/api";

function baseMessage(overrides: Partial<MessageOut>): MessageOut {
  return {
    id: "m1",
    conversation_id: "c1",
    seq: 1,
    role: "user",
    content: "hello",
    parent_message_id: null,
    is_active: true,
    token_count: null,
    tool_call_id: null,
    tool_name: null,
    meta: {},
    created_at: "2026-11-01T10:00:00Z",
    edited_at: null,
    attachments: [],
    ...overrides,
  };
}

describe("MessageBubble", () => {
  it("renders user messages on the right with content", () => {
    render(<MessageBubble message={baseMessage({ role: "user", content: "hi there" })} />);
    const article = screen.getByRole("article");
    expect(article).toHaveTextContent("hi there");
  });

  it("renders assistant markdown including code blocks", () => {
    render(
      <MessageBubble
        message={baseMessage({
          role: "assistant",
          content: "Use this:\n```python\nprint('hi')\n```\nthanks",
        })}
      />,
    );
    expect(screen.getByRole("article")).toHaveTextContent("print");
  });

  it("renders tool-call metadata from assistant messages", () => {
    render(
      <MessageBubble
        message={baseMessage({
          role: "assistant",
          content: "",
          meta: {
            tool_calls: [{ id: "call-1", name: "http.request", arguments: { url: "https://x.test" } }],
          },
        })}
      />,
    );
    expect(screen.getByText(/tool call/i)).toBeInTheDocument();
    expect(screen.getByText(/http\.request/)).toBeInTheDocument();
  });

  it("renders tool result messages as collapsible blocks", () => {
    render(
      <MessageBubble
        message={baseMessage({
          role: "tool",
          content: '{"status":200}',
          tool_name: "http.request",
          tool_call_id: "call-1",
        })}
      />,
    );
    expect(screen.getByText(/http\.request · result/i)).toBeInTheDocument();
  });

  it("marks interrupted assistant messages", () => {
    render(
      <MessageBubble
        message={baseMessage({ role: "assistant", meta: { interrupted: true }, content: "partial" })}
      />,
    );
    expect(screen.getByText(/interrupted/i)).toBeInTheDocument();
  });
});

describe("StreamingBubble", () => {
  it("shows connecting state", () => {
    render(<StreamingBubble text="" phase="connecting" />);
    expect(screen.getByTestId("streaming-bubble")).toHaveTextContent(/contacting the model/i);
  });

  it("shows streamed text", () => {
    render(<StreamingBubble text="Once upon" phase="streaming" />);
    expect(screen.getByTestId("streaming-bubble")).toHaveTextContent("Once upon");
  });
});

describe("login + message flow against a stubbed backend", () => {
  it("shows a login error envelope from the API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ error: { code: "invalid_credentials", message: "email or password is incorrect" } }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      ) as unknown as typeof fetch,
    );
    const { default: LoginPage } = await import("@/features/auth/pages");
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <LoginPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/email/i), "u@e.test");
    await user.type(screen.getByLabelText(/password/i), "badpass1234");
    await user.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/email or password is incorrect/i);
    });
    vi.unstubAllGlobals();
  });
});
