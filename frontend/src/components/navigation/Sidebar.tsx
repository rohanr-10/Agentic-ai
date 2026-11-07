import { useQueryClient } from "@tanstack/react-query";
import { NavLink } from "react-router";
import {
  ChevronsLeft,
  ChevronsRight,
  Brain,
  FileStack,
  LogOut,
  MessageSquare,
  Settings,
  Sparkles,
  Wrench,
  ListChecks,
  Cpu,
} from "lucide-react";
import { cn } from "@/utils/cn";
import { useAuth } from "@/features/auth/useAuth";
import { useAuthActions } from "@/features/auth/useAuthActions";
import { useConversations } from "@/features/queries";
import ConversationList from "@/components/navigation/ConversationList";
import { Dropdown, MenuSeparator } from "@/components/ui/Dropdown";
import { initials } from "@/utils/format";

interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
  onNavigate: () => void;
}

const NAV_ITEMS = [
  { to: "/chat", label: "Chat", icon: MessageSquare, end: false },
  { to: "/tasks", label: "Tasks", icon: ListChecks, end: true },
  { to: "/approvals", label: "Approvals", icon: Brain, end: true },
  { to: "/files", label: "Files", icon: FileStack, end: true },
  { to: "/models", label: "Models", icon: Cpu, end: true },
] as const;

export default function Sidebar({ collapsed, onToggleCollapse, onNavigate }: SidebarProps) {
  const { user } = useAuth();
  const { logout } = useAuthActions();
  const queryClient = useQueryClient();
  const conversations = useConversations();

  return (
    <div
      className={cn(
        "flex h-full flex-col bg-bg-sidebar transition-[width] duration-200",
        collapsed ? "w-[64px]" : "w-[264px]",
      )}
    >
      {/* Brand */}
      <div className={cn("flex items-center gap-2.5 border-b border-border px-3", collapsed ? "h-14 justify-center" : "h-14 px-4")}>
        <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-accent/15 text-accent">
          <Sparkles size={15} aria-hidden />
        </span>
        {!collapsed && (
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold leading-tight text-fg">Agentic</span>
            <span className="block truncate text-2xs text-fg-subtle">Platform workspace</span>
          </span>
        )}
      </div>

      {/* Primary nav */}
      <nav aria-label="Primary" className={cn("flex flex-col gap-0.5 border-b border-border py-2", collapsed ? "px-2" : "px-2.5")}>
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onNavigate}
            title={collapsed ? label : undefined}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2.5 rounded-md text-sm transition-colors",
                collapsed ? "h-9 justify-center" : "h-9 px-2.5",
                isActive
                  ? "bg-accent-subtle text-fg"
                  : "text-fg-muted hover:bg-bg-hover hover:text-fg",
              )
            }
          >
            <Icon size={16} className="shrink-0" aria-hidden />
            {!collapsed && <span className="truncate">{label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* Conversations */}
      <div className="min-h-0 flex-1 overflow-hidden">
        <ConversationList collapsed={collapsed} onNavigate={onNavigate} conversations={conversations} />
      </div>

      {/* Footer: user + settings */}
      <div className={cn("flex items-center gap-1 border-t border-border py-2", collapsed ? "flex-col gap-2 px-2" : "justify-between px-3")}>
        {collapsed ? (
          <>
            <NavLink
              to="/settings"
              onClick={onNavigate}
              title="Settings"
              className="flex size-8 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-hover hover:text-fg"
            >
              <Settings size={16} aria-hidden />
            </NavLink>
            <NavLink
              to="/tools"
              onClick={onNavigate}
              title="Tools"
              className="flex size-8 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-hover hover:text-fg"
            >
              <Wrench size={16} aria-hidden />
            </NavLink>
            <NavLink
              to="/memory"
              onClick={onNavigate}
              title="Memory"
              className="flex size-8 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-hover hover:text-fg"
            >
              <Brain size={16} aria-hidden />
            </NavLink>
            <button
              type="button"
              onClick={onToggleCollapse}
              title="Expand sidebar"
              aria-label="Expand sidebar"
              className="hidden size-8 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-hover hover:text-fg lg:flex"
            >
              <ChevronsRight size={16} aria-hidden />
            </button>
          </>
        ) : (
          <>
            <Dropdown
              align="start"
              trigger={({ onClick, ref, "aria-expanded": expanded }) => (
                <button
                  ref={ref}
                  type="button"
                  onClick={onClick}
                  aria-expanded={expanded}
                  className="flex min-w-0 items-center gap-2.5 rounded-md px-1.5 py-1.5 text-left transition-colors hover:bg-bg-hover"
                >
                  <span className="flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-bg-elevated text-2xs font-semibold text-accent">
                    {initials(user?.display_name ?? "?")}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-fg">
                      {user?.display_name ?? "…"}
                    </span>
                    <span className="block truncate text-2xs text-fg-subtle">{user?.email ?? ""}</span>
                  </span>
                </button>
              )}
            >
              {(close) => (
                <div className="min-w-56">
                  <div className="px-2.5 py-2">
                    <p className="text-xs font-semibold text-fg">{user?.display_name}</p>
                    <p className="truncate text-2xs text-fg-subtle">{user?.email}</p>
                    <p className="mt-1 text-2xs text-fg-subtle">
                      Role: {user?.role ?? "—"} ·{" "}
                      {user?.role === "admin" ? "full platform access" : "standard access"}
                    </p>
                  </div>
                  <MenuSeparator />
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center gap-2.5 rounded-sm px-2.5 py-1.5 text-left text-sm text-fg-muted transition-colors hover:bg-danger-subtle hover:text-danger"
                    onClick={async () => {
                      close();
                      await logout();
                      void queryClient.clear();
                    }}
                  >
                    <LogOut size={15} className="shrink-0 opacity-80" aria-hidden />
                    Sign out
                  </button>
                </div>
              )}
            </Dropdown>
            <div className="flex items-center gap-0.5">
              <NavLink
                to="/memory"
                onClick={onNavigate}
                title="Memory"
                className="flex size-8 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-hover hover:text-fg"
              >
                <Brain size={16} aria-hidden />
              </NavLink>
              <NavLink
                to="/tools"
                onClick={onNavigate}
                title="Tools"
                className="flex size-8 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-hover hover:text-fg"
              >
                <Wrench size={16} aria-hidden />
              </NavLink>
              <NavLink
                to="/settings"
                onClick={onNavigate}
                title="Settings"
                className="flex size-8 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-hover hover:text-fg"
              >
                <Settings size={16} aria-hidden />
              </NavLink>
              <button
                type="button"
                onClick={onToggleCollapse}
                title="Collapse sidebar"
                aria-label="Collapse sidebar"
                className="flex size-8 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-hover hover:text-fg"
              >
                <ChevronsLeft size={16} aria-hidden />
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
