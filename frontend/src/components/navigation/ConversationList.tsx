import { useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { NavLink } from "react-router";
import { Archive, MessageSquare, Pencil, Plus, Search, SquarePen, X } from "lucide-react";
import { cn } from "@/utils/cn";
import type { ConversationOut } from "@/types/api";
import { EmptyState, InlineLoader } from "@/components/ui/State";
import { formatRelative } from "@/utils/format";

interface ConversationListProps {
  collapsed: boolean;
  onNavigate: () => void;
  conversations: UseQueryResult<ConversationOut[]>;
}

export default function ConversationList({
  collapsed,
  onNavigate,
  conversations,
}: ConversationListProps) {
  const { data, isLoading, isError, refetch } = conversations;
  const [search, setSearch] = useState("");

  const active = (data ?? []).filter((c) => c.status === "active");
  const archived = (data ?? []).filter((c) => c.status === "archived");
  const filtered = search.trim()
    ? active.filter((c) => c.title.toLowerCase().includes(search.trim().toLowerCase()))
    : active;

  if (collapsed) {
    return (
      <div className="flex h-full flex-col items-center gap-2 py-3">
        <NavLink
          to="/chat"
          onClick={onNavigate}
          title="New conversation"
          aria-label="New conversation"
          className="flex size-9 items-center justify-center rounded-md bg-accent/15 text-accent transition-colors hover:bg-accent/25"
        >
          <Plus size={17} aria-hidden />
        </NavLink>
        <NavLink
          to="/chat"
          end
          onClick={onNavigate}
          title="Conversations"
          className="flex size-9 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-bg-hover hover:text-fg"
        >
          <MessageSquare size={16} aria-hidden />
        </NavLink>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 px-3 pt-3 pb-2">
        <div className="relative flex-1">
          <Search
            size={13}
            aria-hidden
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-subtle"
          />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search conversations"
            aria-label="Search conversations"
            className="h-8 w-full rounded-sm border border-border bg-bg-input pl-8 pr-7 text-xs text-fg placeholder:text-fg-subtle transition-colors focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20"
          />
          {search && (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => setSearch("")}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-xs p-1 text-fg-subtle hover:text-fg"
            >
              <X size={12} aria-hidden />
            </button>
          )}
        </div>
        <NavLink
          to="/chat"
          onClick={onNavigate}
          title="New conversation"
          aria-label="New conversation"
          className="flex size-8 shrink-0 items-center justify-center rounded-sm bg-accent text-bg transition-colors hover:bg-accent-hover"
        >
          <SquarePen size={15} aria-hidden />
        </NavLink>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {isLoading ? (
          <div className="p-4">
            <InlineLoader label="Loading conversations…" />
          </div>
        ) : isError ? (
          <EmptyState
            icon={<Archive />}
            title="Couldn't load conversations"
            description="The server didn't respond. Check the backend is running."
            action={
              <button
                type="button"
                onClick={() => void refetch()}
                className="rounded-sm border border-border px-3 py-1.5 text-xs font-medium text-fg hover:bg-bg-hover"
              >
                Retry
              </button>
            }
          />
        ) : filtered.length === 0 && archived.length === 0 ? (
          <EmptyState
            icon={<MessageSquare />}
            title={search ? "No matches" : "No conversations yet"}
            description={
              search
                ? "Try a different search term."
                : "Start a conversation to see it listed here."
            }
          />
        ) : (
          <>
            {filtered.length > 0 && (
              <ul className="space-y-0.5" aria-label="Recent conversations">
                {filtered.map((conversation) => (
                  <li key={conversation.id}>
                    <NavLink
                      to={`/chat/${conversation.id}`}
                      onClick={onNavigate}
                      className={({ isActive }) =>
                        cn(
                          "block rounded-md px-2.5 py-2 transition-colors",
                          isActive
                            ? "bg-accent-subtle text-fg"
                            : "text-fg-muted hover:bg-bg-hover hover:text-fg",
                        )
                      }
                    >
                      <span className="flex items-center gap-2">
                        <span className="min-w-0 flex-1 truncate text-sm font-medium leading-5">
                          {conversation.title}
                        </span>
                        {conversation.mode !== "conversational" && (
                          <span
                            title={`${conversation.mode} mode`}
                            className="shrink-0 rounded-full border border-border px-1.5 text-[10px] leading-4 text-fg-subtle"
                          >
                            {conversation.mode === "hybrid" ? "hybrid" : "auto"}
                          </span>
                        )}
                      </span>
                      <span className="mt-0.5 flex items-center gap-1.5 text-2xs text-fg-subtle">
                        <Pencil size={10} aria-hidden className={conversation.last_message_at ? "" : "opacity-0"} />
                        {formatRelative(conversation.last_message_at ?? conversation.updated_at)}
                        <span aria-hidden>·</span>
                        {conversation.message_count} messages
                      </span>
                    </NavLink>
                  </li>
                ))}
              </ul>
            )}

            {archived.length > 0 && (
              <details className="mt-3 group">
                <summary className="cursor-pointer list-none rounded-md px-2.5 py-1.5 text-2xs font-medium uppercase tracking-wide text-fg-subtle transition-colors hover:text-fg-muted">
                  Archived ({archived.length})
                </summary>
                <ul className="mt-1 space-y-0.5">
                  {archived.map((conversation) => (
                    <li key={conversation.id}>
                      <NavLink
                        to={`/chat/${conversation.id}`}
                        onClick={onNavigate}
                        className={({ isActive }) =>
                          cn(
                            "block truncate rounded-md px-2.5 py-1.5 text-sm transition-colors",
                            isActive
                              ? "bg-accent-subtle text-fg"
                              : "text-fg-subtle hover:bg-bg-hover hover:text-fg",
                          )
                        }
                      >
                        {conversation.title}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
      </div>
    </div>
  );
}
