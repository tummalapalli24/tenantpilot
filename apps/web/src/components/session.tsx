"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useMemo, useSyncExternalStore } from "react";

import { Badge, Spinner } from "@/components/ui";
import { SESSION_EVENT, clearSession } from "@/lib/api";
import type { User } from "@/lib/types";

function subscribe(callback: () => void): () => void {
  window.addEventListener("storage", callback);
  window.addEventListener(SESSION_EVENT, callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener(SESSION_EVENT, callback);
  };
}

function readRaw(): string {
  try {
    return `${localStorage.getItem("tp.token") ?? ""}\n${localStorage.getItem("tp.user") ?? ""}`;
  } catch {
    return "\n";
  }
}

const noop = () => () => {};

/** True only after hydration, so server HTML and the first client render match. */
export function useHydrated(): boolean {
  return useSyncExternalStore(noop, () => true, () => false);
}

export function useUser(): User | null {
  const raw = useSyncExternalStore(subscribe, readRaw, () => "\n");
  return useMemo(() => {
    const [token, user] = raw.split("\n");
    if (!token || !user) return null;
    try {
      return JSON.parse(user) as User;
    } catch {
      return null;
    }
  }, [raw]);
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const hydrated = useHydrated();
  const user = useUser();
  const router = useRouter();
  useEffect(() => {
    if (hydrated && !user) router.replace("/login");
  }, [hydrated, user, router]);
  if (!hydrated || !user) {
    return (
      <div className="p-8">
        <Spinner />
      </div>
    );
  }
  return <>{children}</>;
}

export function Shell({ children }: { children: ReactNode }) {
  const user = useUser();
  const router = useRouter();
  const pathname = usePathname();
  return (
    <div className="flex min-h-full flex-col">
      <nav className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-6">
            <Link href="/tenants" className="flex items-center gap-2 font-semibold text-slate-900">
              <span className="grid h-7 w-7 place-items-center rounded-md bg-indigo-600 text-sm text-white">TP</span>
              TenantPilot
            </Link>
            <Link href="/tenants" className={pathname === "/tenants" ? "text-sm font-medium text-indigo-600" : "text-sm text-slate-600 hover:text-slate-900"}>
              Customers
            </Link>
          </div>
          {user && (
            <div className="flex items-center gap-3 text-sm text-slate-600">
              <span>{user.email}</span>
              <Badge value={user.role} />
              <button
                className="text-slate-500 hover:text-slate-900"
                onClick={() => {
                  clearSession();
                  router.replace("/login");
                }}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </nav>
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
