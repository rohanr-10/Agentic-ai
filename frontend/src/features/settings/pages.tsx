import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { KeyRound, ShieldCheck, UserRound } from "lucide-react";
import { PageShell } from "@/components/layout/AppLayout";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Field";
import { toast } from "@/components/ui/toast";
import { useAuth } from "@/features/auth/useAuth";
import { useAuthActions } from "@/features/auth/useAuthActions";
import { useActivity } from "@/features/activity/ActivityProvider";
import { apiBaseUrl } from "@/services/api/client";
import { formatDateTime } from "@/utils/format";

export default function SettingsPage() {
  const { user } = useAuth();
  const { updateProfile, logout } = useAuthActions();
  const activity = useActivity();
  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [savingName, setSavingName] = useState(false);

  const save = useMutation({
    mutationFn: async () => {
      setSavingName(true);
      const error = await updateProfile(displayName.trim());
      setSavingName(false);
      if (error) throw new Error(error.message);
    },
    onSuccess: () => toast.success("Profile updated"),
    onError: (err) => toast.error("Update failed", (err as Error).message),
  });

  return (
    <PageShell title="Settings">
      <div className="mx-auto max-w-2xl space-y-6 px-4 py-6 sm:px-6">
        {/* account */}
        <section className="rounded-xl border border-border bg-bg-panel p-5" aria-label="Account">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
            <UserRound size={15} className="text-fg-subtle" aria-hidden />
            Account
          </h2>
          <div className="mt-4 space-y-4">
            <TextField
              label="Display name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              maxLength={120}
            />
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-fg-subtle">{user?.email}</p>
              <Button
                variant="secondary"
                size="sm"
                loading={savingName || save.isPending}
                disabled={displayName.trim() === user?.display_name || displayName.trim().length === 0}
                onClick={() => save.mutate()}
              >
                Save
              </Button>
            </div>
            <p className="flex flex-wrap items-center gap-2 text-2xs text-fg-subtle">
              <Badge tone={user?.role === "admin" ? "accent" : "muted"}>{user?.role}</Badge>
              <span>member since {formatDateTime(user?.created_at)}</span>
              <span aria-hidden>·</span>
              <span>last login {formatDateTime(user?.last_login_at)}</span>
            </p>
          </div>
        </section>

        {/* session */}
        <section className="rounded-xl border border-border bg-bg-panel p-5" aria-label="Session">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
            <KeyRound size={15} className="text-fg-subtle" aria-hidden />
            Session
          </h2>
          <div className="mt-3 space-y-3 text-xs leading-relaxed text-fg-muted">
            <p>
              Tokens are held in memory only (no localStorage). Sessions survive navigation and
              tab reloads are not persisted — that is the deliberate trade-off documented in the
              backend security model. Access tokens rotate silently every 30 minutes.
            </p>
            <Button
              variant="danger"
              size="sm"
              onClick={() => void logout()}
            >
              Sign out
            </Button>
          </div>
        </section>

        {/* platform */}
        <section className="rounded-xl border border-border bg-bg-panel p-5" aria-label="Platform">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
            <ShieldCheck size={15} className="text-fg-subtle" aria-hidden />
            Platform
          </h2>
          <dl className="mt-3 grid gap-x-6 gap-y-3 text-xs sm:grid-cols-2">
            <div>
              <dt className="text-fg-subtle">API base</dt>
              <dd className="mt-0.5 font-mono text-fg-muted">{apiBaseUrl()}</dd>
            </div>
            <div>
              <dt className="text-fg-subtle">Live events</dt>
              <dd className="mt-0.5 text-fg-muted">
                {activity.connection === "open" ? "connected" : activity.connection}
              </dd>
            </div>
            <div>
              <dt className="text-fg-subtle">Streaming</dt>
              <dd className="mt-0.5 text-fg-muted">SSE over the backend chat route</dd>
            </div>
            <div>
              <dt className="text-fg-subtle">OpenAPI</dt>
              <dd className="mt-0.5">
                <a
                  href={`${apiBaseUrl().replace(/\/v1$/, "")}/openapi.json`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent-hover hover:underline"
                >
                  openapi.json
                </a>{" "}
                ·{" "}
                <a
                  href={`${apiBaseUrl().replace(/\/v1$/, "")}/docs`.replace("//docs", "/docs")}
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent-hover hover:underline"
                >
                  Swagger UI
                </a>
              </dd>
            </div>
          </dl>
        </section>
      </div>
    </PageShell>
  );
}
