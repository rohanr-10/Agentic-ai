import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate } from "react-router";
import { RequireAuth, RedirectIfAuthed } from "@/app/RequireAuth";
import AppLayout from "@/components/layout/AppLayout";
import { PageLoader } from "@/components/ui/State";
import LoginPage, { RegisterPage } from "@/features/auth/pages";
import ErrorBoundary from "@/app/ErrorBoundary";
import NotFoundPage from "@/app/NotFound";

const ChatWorkspace = lazy(() => import("@/features/chat/ChatWorkspace"));
const TasksPage = lazy(() => import("@/features/tasks/pages"));
const TaskDetailPage = lazy(() =>
  import("@/features/tasks/pages").then((m) => ({ default: m.TaskDetailPage })),
);
const FilesPage = lazy(() => import("@/features/files/pages"));
const ModelsPage = lazy(() => import("@/features/models/pages"));
const MemoryPage = lazy(() => import("@/features/memory/pages"));
const ApprovalsPage = lazy(() => import("@/features/approvals/pages"));
const ToolsPage = lazy(() => import("@/features/tools/pages"));
const SettingsPage = lazy(() => import("@/features/settings/pages"));

/** Wraps a lazy page in the standard Suspense fallback. */
function lazily(node: React.ReactNode) {
  return <Suspense fallback={<PageLoader />}>{node}</Suspense>;
}

export const router = createBrowserRouter([
  {
    element: <RedirectIfAuthed to="/" />,
    children: [
      { path: "/login", element: <LoginPage /> },
      { path: "/register", element: <RegisterPage /> },
    ],
  },
  {
    element: <RequireAuth />,
    errorElement: <ErrorBoundary />,
    children: [
      {
        path: "/",
        element: <AppLayout />,
        children: [
          { index: true, element: <Navigate to="/chat" replace /> },
          { path: "chat", element: lazily(<ChatWorkspace />) },
          { path: "chat/:conversationId", element: lazily(<ChatWorkspace />) },
          { path: "tasks", element: lazily(<TasksPage />) },
          { path: "tasks/:taskId", element: lazily(<TaskDetailPage />) },
          { path: "approvals", element: lazily(<ApprovalsPage />) },
          { path: "files", element: lazily(<FilesPage />) },
          { path: "models", element: lazily(<ModelsPage />) },
          { path: "memory", element: lazily(<MemoryPage />) },
          { path: "tools", element: lazily(<ToolsPage />) },
          { path: "settings", element: lazily(<SettingsPage />) },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
