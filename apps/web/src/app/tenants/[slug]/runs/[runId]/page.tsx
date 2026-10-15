"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { AnswerView } from "@/components/answer";
import { useTenant } from "@/components/tenant-context";
import { Timeline } from "@/components/trace";
import { Badge, Card, ErrorBanner, JsonView, Spinner, Stat, formatDate } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import type { RunDetail } from "@/lib/types";

const JAEGER = process.env.NEXT_PUBLIC_JAEGER_URL;

export default function RunTracePage() {
  const { tenant } = useTenant();
  const { runId } = useParams<{ runId: string }>();
  const { data: run, error } = useApi<RunDetail>(`/runs/${runId}`, tenant.slug);

  if (error) return <ErrorBanner error={error} />;
  if (!run) return <Spinner />;

  return (
    <div className="space-y-5">
      <Link href={`/tenants/${tenant.slug}/runs`} className="text-sm text-indigo-600 hover:underline">
        &larr; All runs
      </Link>
      <Card
        title={
          <span className="flex items-center gap-2">
            Trace <Badge value={run.status} />
          </span>
        }
      >
        <p className="mb-4 text-sm text-slate-800">&ldquo;{run.request}&rdquo;</p>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Stat label="Latency" value={`${run.latency_ms ?? "-"} ms`} sub="excludes time waiting for humans" />
          <Stat label="LLM calls" value={run.llm_calls} sub={<span className="font-mono">{run.model}</span>} />
          <Stat label="Tokens" value={`${run.input_tokens} / ${run.output_tokens}`} sub={`in / out - ${run.cache_read_tokens} cache-read`} />
          <Stat label="Tool calls" value={run.tool_calls.length} sub={`${run.tool_calls.filter((c) => c.status === "succeeded").length} succeeded`} />
          <Stat label="Started" value={<span className="text-sm">{formatDate(run.created_at)}</span>} />
        </div>
        <dl className="mt-4 grid gap-x-6 gap-y-1 font-mono text-xs text-slate-600 md:grid-cols-2">
          <div>
            <dt className="inline text-slate-400">tenant_id </dt>
            <dd className="inline">{run.tenant_id}</dd>
          </div>
          <div>
            <dt className="inline text-slate-400">run_id </dt>
            <dd className="inline">{run.id}</dd>
          </div>
          <div>
            <dt className="inline text-slate-400">request_id </dt>
            <dd className="inline">{run.request_id}</dd>
          </div>
          <div>
            <dt className="inline text-slate-400">trace_id </dt>
            <dd className="inline">
              {JAEGER && run.trace_id ? (
                <a className="text-indigo-600 hover:underline" href={`${JAEGER}/trace/${run.trace_id}`} target="_blank" rel="noreferrer">
                  {run.trace_id}
                </a>
              ) : (
                run.trace_id
              )}
            </dd>
          </div>
        </dl>
        {run.error && (
          <div className="mt-4">
            <ErrorBanner error={new Error(run.error)} />
          </div>
        )}
      </Card>
      <div className="grid gap-5 xl:grid-cols-5">
        <div className="xl:col-span-3">
          <Card title="Timeline (click a step for details)">
            <Timeline steps={run.steps} />
          </Card>
        </div>
        <div className="space-y-5 xl:col-span-2">
          <Card title="Final answer">{run.response ? <AnswerView answer={run.response} /> : <p className="text-sm text-slate-500">No answer (yet).</p>}</Card>
          <Card title="Tool calls">
            <ul className="space-y-3">
              {run.tool_calls.map((call) => (
                <li key={call.id} className="rounded-md border border-slate-200 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-sm">
                      {call.source_id && <span className="mr-2 text-slate-400">{call.source_id}</span>}
                      {call.tool_name}
                    </span>
                    <span className="flex items-center gap-2">
                      {call.requires_approval && <Badge value={call.approval_status ?? "pending"} />}
                      <Badge value={call.status} />
                    </span>
                  </div>
                  <details className="mt-2">
                    <summary className="cursor-pointer text-xs text-slate-500">request / response {call.latency_ms != null ? `- ${call.latency_ms} ms` : ""}</summary>
                    <div className="mt-2 space-y-2">
                      <JsonView value={call.request_json} maxHeight="8rem" />
                      <JsonView value={call.response_json} maxHeight="12rem" />
                    </div>
                  </details>
                </li>
              ))}
              {run.tool_calls.length === 0 && <p className="text-sm text-slate-500">No tools were called.</p>}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
