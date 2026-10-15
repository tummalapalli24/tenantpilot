"use client";

import Link from "next/link";

import { useUser } from "@/components/session";
import { Badge, ErrorBanner, Spinner, formatDate } from "@/components/ui";
import { can } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { Tenant } from "@/lib/types";

export default function TenantsPage() {
  const user = useUser();
  const { data: tenants, error, loading } = useApi<Tenant[]>("/tenants");

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold">Customers</h1>
          <p className="text-sm text-slate-500">Each customer is an isolated tenant with its own data, tools, model and policies.</p>
        </div>
        {can(user, "tenants:manage") && (
          <Link href="/tenants/new" className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500">
            Create customer
          </Link>
        )}
      </div>
      <ErrorBanner error={error} />
      {loading && <Spinner />}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {tenants?.map((tenant) => (
          <Link key={tenant.id} href={`/tenants/${tenant.slug}`} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition hover:border-indigo-300 hover:shadow">
            <div className="flex items-start justify-between">
              <div>
                <div className="font-medium text-slate-900">{tenant.name}</div>
                <div className="font-mono text-xs text-slate-500">{tenant.slug}</div>
              </div>
              <Badge value={tenant.status} />
            </div>
            <dl className="mt-4 grid grid-cols-2 gap-2 text-xs">
              <div>
                <dt className="text-slate-400">Model</dt>
                <dd className="font-mono text-slate-700">{tenant.config?.model_name ?? "-"}</dd>
              </div>
              <div>
                <dt className="text-slate-400">Write approval</dt>
                <dd className="text-slate-700">{tenant.config?.approval_required ? "required" : "off"}</dd>
              </div>
              <div className="col-span-2">
                <dt className="text-slate-400">Onboarded</dt>
                <dd className="text-slate-700">{formatDate(tenant.created_at)}</dd>
              </div>
            </dl>
          </Link>
        ))}
      </div>
      {tenants?.length === 0 && <p className="text-sm text-slate-500">No customers you can access yet.</p>}
    </div>
  );
}
