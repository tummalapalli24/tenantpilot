"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { TenantCtx } from "@/components/tenant-context";
import { Badge, ErrorBanner, Spinner, cx } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import type { TenantDetail } from "@/lib/types";

const TABS = [
  ["", "Overview"],
  ["/agent", "Agent"],
  ["/runs", "Runs & traces"],
  ["/knowledge", "Knowledge"],
  ["/connectors", "Connectors"],
  ["/tools", "Tools"],
  ["/audit", "Audit log"],
] as const;

export default function TenantLayout({ children }: { children: ReactNode }) {
  const { slug } = useParams<{ slug: string }>();
  const pathname = usePathname();
  const { data: tenant, error, reload } = useApi<TenantDetail>(`/tenants/${slug}`);
  const base = `/tenants/${slug}`;

  if (error) return <ErrorBanner error={error} />;
  if (!tenant) return <Spinner />;

  return (
    <TenantCtx.Provider value={{ tenant, reload }}>
      <div className="space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold">{tenant.name}</h1>
              <Badge value={tenant.status} />
            </div>
            <p className="font-mono text-xs text-slate-500">
              tenant {tenant.id} - model {tenant.config?.model_name}
            </p>
          </div>
          {tenant.stats.runs_awaiting_approval > 0 && (
            <Link href={`${base}/runs`} className="rounded-md bg-amber-50 px-3 py-1.5 text-sm text-amber-800 ring-1 ring-amber-200">
              {tenant.stats.runs_awaiting_approval} run(s) awaiting approval
            </Link>
          )}
        </div>
        <nav className="flex gap-1 overflow-x-auto border-b border-slate-200">
          {TABS.map(([suffix, label]) => {
            const href = base + suffix;
            const active = suffix === "" ? pathname === base : pathname.startsWith(href);
            return (
              <Link
                key={label}
                href={href}
                className={cx(
                  "-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm",
                  active ? "border-indigo-600 font-medium text-indigo-700" : "border-transparent text-slate-500 hover:text-slate-800",
                )}
              >
                {label}
              </Link>
            );
          })}
        </nav>
        {children}
      </div>
    </TenantCtx.Provider>
  );
}
