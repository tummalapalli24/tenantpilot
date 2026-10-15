"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Button, ErrorBanner, Field, Input } from "@/components/ui";
import { api, saveSession } from "@/lib/api";
import type { User } from "@/lib/types";

const DEMO_USERS = [
  ["admin@tenantpilot.dev", "admin - all customers, manages tenants/connectors/tools"],
  ["engineer@tenantpilot.dev", "engineer - Acme + BetaCorp, runs agents, approves actions"],
  ["acme-engineer@tenantpilot.dev", "engineer - Acme only (try opening BetaCorp)"],
  ["viewer@tenantpilot.dev", "viewer - read-only dashboards"],
];

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("admin@tenantpilot.dev");
  const [password, setPassword] = useState("tenantpilot-demo");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ access_token: string; user: User }>("/auth/login", { method: "POST", body: { email, password } });
      saveSession(res.access_token, res.user);
      router.replace("/tenants");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-lg bg-indigo-600 font-semibold text-white">TP</span>
          <div>
            <h1 className="text-lg font-semibold">TenantPilot</h1>
            <p className="text-sm text-slate-500">Deploy, govern and observe customer AI agents</p>
          </div>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <Field label="Email">
            <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />
          </Field>
          <Field label="Password">
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </Field>
          <ErrorBanner error={error} />
          <Button type="submit" disabled={busy} className="w-full justify-center">
            {busy ? "Signing in..." : "Sign in"}
          </Button>
        </form>
        <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-4 text-xs text-slate-500">
          <p className="mb-2 font-medium text-slate-600">Demo accounts (password: tenantpilot-demo)</p>
          <ul className="space-y-1">
            {DEMO_USERS.map(([user, role]) => (
              <li key={user}>
                <button type="button" className="font-mono text-indigo-600 hover:underline" onClick={() => setEmail(user)}>
                  {user}
                </button>{" "}
                - {role}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
