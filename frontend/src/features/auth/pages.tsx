import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { AtSign, KeyRound, Sparkle, TriangleAlert, UserRound } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Field";
import { useAuth } from "@/features/auth/useAuth";
import { useAuthActions, type AuthActionError } from "@/features/auth/useAuthActions";

const CARD_CLASS =
  "rounded-xl border border-border bg-bg-panel p-5 shadow-[0_18px_60px_rgba(0,0,0,0.35)]";

function SubmitError({ error }: { error: AuthActionError | null }) {
  if (!error) return null;
  return (
    <div
      role="alert"
      className="mb-4 flex items-start gap-2 rounded-md border border-danger/30 bg-danger-subtle px-3 py-2.5 text-xs leading-relaxed text-danger"
    >
      <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden />
      <span>{error.message}</span>
    </div>
  );
}

function AuthBrand({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="mb-8 text-center">
      <div className="mx-auto mb-4 flex size-11 items-center justify-center rounded-xl border border-border bg-bg-panel text-accent">
        <Sparkle size={20} aria-hidden />
      </div>
      <h1 className="text-lg font-semibold text-fg">{title}</h1>
      <p className="mt-1 text-sm text-fg-muted">{subtitle}</p>
    </div>
  );
}

export default function LoginPage() {
  const { login } = useAuthActions();
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<AuthActionError | null>(null);

  if (isAuthenticated) {
    navigate("/", { replace: true });
    return null;
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    const result = await login(email.trim(), password);
    setBusy(false);
    if (result) setError(result);
    else navigate("/", { replace: true });
  };

  return (
    <main className="flex min-h-dvh items-center justify-center bg-bg px-4 py-10">
      <div className="w-full max-w-sm">
        <AuthBrand title="Welcome back" subtitle="Sign in to your agentic workspace." />
        <form onSubmit={submit} noValidate className={CARD_CLASS}>
          <SubmitError error={error} />
          <div className="space-y-4">
            <TextField
              type="email"
              autoComplete="email"
              label="Email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              leftIcon={<AtSign size={16} aria-hidden />}
            />
            <TextField
              type="password"
              autoComplete="current-password"
              label="Password"
              placeholder="Your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              leftIcon={<KeyRound size={16} aria-hidden />}
            />
            <Button type="submit" variant="primary" size="md" className="w-full" loading={busy}>
              Sign in
            </Button>
          </div>
          <p className="mt-4 text-center text-xs text-fg-subtle">
            No account yet?{" "}
            <Link to="/register" className="font-medium text-accent-hover hover:underline">
              Create one
            </Link>
          </p>
        </form>
      </div>
    </main>
  );
}

export function RegisterPage() {
  const { register } = useAuthActions();
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<AuthActionError | null>(null);

  if (isAuthenticated) {
    navigate("/", { replace: true });
    return null;
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    const result = await register(email.trim(), password, displayName.trim());
    setBusy(false);
    if (result) setError(result);
    else navigate("/", { replace: true });
  };

  return (
    <main className="flex min-h-dvh items-center justify-center bg-bg px-4 py-10">
      <div className="w-full max-w-sm">
        <AuthBrand
          title="Create your account"
          subtitle="One account for conversations, tasks, files and models."
        />
        <form onSubmit={submit} noValidate className={CARD_CLASS}>
          <SubmitError error={error} />
          <div className="space-y-4">
            <TextField
              label="Display name"
              placeholder="Ada Lovelace"
              autoComplete="name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              required
              leftIcon={<UserRound size={16} aria-hidden />}
            />
            <TextField
              type="email"
              autoComplete="email"
              label="Email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              leftIcon={<AtSign size={16} aria-hidden />}
            />
            <TextField
              type="password"
              autoComplete="new-password"
              label="Password"
              placeholder="Min 10 chars with letters and digits"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              leftIcon={<KeyRound size={16} aria-hidden />}
            />
            <Button type="submit" variant="primary" size="md" className="w-full" loading={busy}>
              Create account
            </Button>
          </div>
          <p className="mt-4 text-center text-xs text-fg-subtle">
            Already registered?{" "}
            <Link to="/login" className="font-medium text-accent-hover hover:underline">
              Sign in
            </Link>
          </p>
        </form>
      </div>
    </main>
  );
}
