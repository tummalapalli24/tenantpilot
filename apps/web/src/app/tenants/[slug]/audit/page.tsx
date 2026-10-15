"use client";

import { useTenant } from "@/components/tenant-context";
import { Badge, Card, ErrorBanner, Spinner, formatDate } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import type { AuditLog } from "@/lib/types";

export default function AuditPage() {
  const { tenant } = useTenant();
  const { data: logs, error } = useApi<AuditLog[]>(`/tenants/${tenant.slug}/audit-logs?limit=200`);
  return (
    <Card title="Audit log (append-only: the database role cannot update or delete it)">
      <ErrorBanner error={error} />
      {!logs && !error && <Spinner />}
      <table className="min-w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="py-2">When</th>
            <th>Actor</th>
            <th>Action</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {logs?.map((log) => (
            <tr key={log.id} className="align-top">
              <td className="whitespace-nowrap py-2 pr-4 text-xs text-slate-500">{formatDate(log.created_at)}</td>
              <td className="pr-4 text-xs">{log.actor_email ?? "system"}</td>
              <td className="pr-4">
                <Badge value={log.action} />
              </td>
              <td className="font-mono text-xs text-slate-600">
                {JSON.stringify(log.details)}
                {log.request_id && <div className="text-slate-400">request {log.request_id}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
