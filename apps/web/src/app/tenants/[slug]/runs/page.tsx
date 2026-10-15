"use client";

import Link from "next/link";
import { useState } from "react";

import { useTenant } from "@/components/tenant-context";
import { Badge, ErrorBanner, Select, Spinner, formatDate } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import type { RunSummary } from "@/lib/types";

export default function RunsPage() {
  const { tenant } = useTenant();
  const [status, setStatus] = useState("");
  const query = `/runs?limit=100${status ? `&status=${status}` : ""}`;
  const { data: runs, error, loading } = useApi<RunSummary[]>(query, tenant.slug);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500">Every agent run is persisted with a full step-by-step trace.</p>
        <Select className="w-48" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          <option value="completed">completed</option>
          <option value="awaiting_approval">awaiting approval</option>
          <option value="failed">failed</option>
        </Select>
      </div>
      <ErrorBanner error={error} />
      {loading && <Spinner />}
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2">Request</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Model</th>
              <th className="px-4 py-2 text-right">Latency</th>
              <th className="px-4 py-2 text-right">Tokens in/out</th>
              <th className="px-4 py-2">When</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {runs?.map((run) => (
              <tr key={run.id} className="hover:bg-slate-50">
                <td className="max-w-md truncate px-4 py-2">
                  <Link className="text-indigo-700 hover:underline" href={`/tenants/${tenant.slug}/runs/${run.id}`}>
                    {run.request}
                  </Link>
                </td>
                <td className="px-4 py-2">
                  <Badge value={run.status} />
                </td>
                <td className="px-4 py-2 font-mono text-xs">{run.model}</td>
                <td className="px-4 py-2 text-right tabular-nums">{run.latency_ms ?? "-"} ms</td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {run.input_tokens} / {run.output_tokens}
                </td>
                <td className="px-4 py-2 text-xs text-slate-500">{formatDate(run.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {runs?.length === 0 && <p className="p-4 text-sm text-slate-500">No runs yet. Try the Agent tab.</p>}
      </div>
    </div>
  );
}
