import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import LoginPage from "@/features/auth/pages";

function withProviders(node: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/login"]}>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LoginPage", () => {
  it("renders email/password fields and a submit button", () => {
    withProviders(<LoginPage />);
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeEnabled();
  });

  it("shows the backend error message on failed login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ error: { code: "invalid_credentials", message: "email or password is incorrect" } }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      ) as unknown as typeof fetch,
    );
    const user = userEvent.setup();
    withProviders(<LoginPage />);
    await user.type(screen.getByLabelText(/email/i), "user@example.com");
    await user.type(screen.getByLabelText(/password/i), "wrong-password");
    await user.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/email or password is incorrect/i);
    });
    vi.unstubAllGlobals();
  });
});
