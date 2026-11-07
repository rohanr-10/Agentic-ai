import { Navigate, Outlet, useLocation } from "react-router";
import { useAuth } from "@/features/auth/useAuth";

/** Route guard: unauthenticated users land on /login and return after login. */
export function RequireAuth() {
  const { isAuthenticated } = useAuth();
  const location = useLocation();
  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <Outlet />;
}

export function RedirectIfAuthed({ to = "/" }: { to?: string }) {
  const { isAuthenticated } = useAuth();
  if (isAuthenticated) return <Navigate to={to} replace />;
  return <Outlet />;
}
