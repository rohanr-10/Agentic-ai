import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { router } from "@/app/router";
import { ApiProvider } from "@/app/providers/ApiProvider";
import { mountToastContainer } from "@/components/ui/toast";
import "@/styles/globals.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: true,
      staleTime: 5_000,
    },
  },
});

function App() {
  return (
    <StrictMode>
      <ApiProvider>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </ApiProvider>
    </StrictMode>
  );
}

const container = document.getElementById("root");
if (container) {
  createRoot(container).render(<App />);
}

// Toast portal host (outside #root to avoid layout interference)
const toastHost = document.createElement("div");
toastHost.id = "toast-host";
document.body.appendChild(toastHost);
mountToastContainer(toastHost);
