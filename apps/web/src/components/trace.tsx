"use client";

import { useState } from "react";

import { SourceChip } from "@/components/answer";
import { Badge, JsonView, cx } from "@/components/ui";
import type { RunStep } from "@/lib/types";

const STEP_LABEL: Record<string, string> = {
  tool_discovery: "Tool discovery",
  retrieval: "Retrieval",
  context_build: "Context build",
  llm_call: "LLM call",
  tool_call: "Tool call",
  approval_request: "Approval requested",
  approval: "Human decision",
  final_answer: "Answer validation",
};

const STEP_COLOR: Record<string, string> = {
  tool_discovery: "bg-slate-400",
  retrieval: "bg-violet-500",
  context_build: "bg-violet-400",
  llm_call: "bg-indigo-500",
  tool_call: "bg-sky-500",
  approval_request: "bg-amber-500",
  approval: "bg-amber-600",
  final_answer: "bg-emerald-500",
};

type Attrs = Record<string, unknown>;

function List({ label, items }: { label: string; items: unknown }) {
  const values = Array.isArray(items) ? items : [];
  return (
    <div className="text-xs">
      <span className="text-slate-500">{label}: </span>
      {values.length ? values.map((v) => <code key={String(v)} className="mr-1 rounded bg-slate-100 px-1">{String(v)}</code>) : <span className="text-slate-400">none</span>}
    </div>
  );
}

function ContextDetails({ a }: { a: Attrs }) {
  const selected = (a.selected as Attrs[]) ?? [];
  const dropped = (a.dropped as Attrs[]) ?? [];
  const budget = (a.budget as Attrs) ?? {};
  const tokens = (a.tokens as Attrs) ?? {};
  const intent = (a.intent as Attrs) ?? {};
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-4 text-xs text-slate-600">
        <span>intent <b>{String(intent.kind)}</b></span>
        <span>budget {String(budget.max_context_tokens)} tokens</span>
        <span>knowledge {String(tokens.knowledge)}/{String(budget.knowledge)} used</span>
        <span>system {String(tokens.system)}</span>
        <span>initial prompt ~{String(tokens.total_initial)} tokens</span>
      </div>
      <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-left text-slate-500">
          <tr>
            <th className="py-1 pr-2">id</th>
            <th className="pr-2">document / section</th>
            <th className="px-2 text-right">vector</th>
            <th className="px-2 text-right">lexical</th>
            <th className="px-2 text-right">final</th>
            <th className="px-2 text-right">tokens</th>
            <th className="pl-3">why selected</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {selected.map((s) => (
            <tr key={String(s.chunk_id)} title={String(s.preview)}>
              <td className="py-1">
                <SourceChip id={String(s.source_id)} />
              </td>
              <td className="max-w-[16rem] truncate pr-2">
                {String(s.document)} <span className="text-slate-400">{String(s.section ?? "")}</span>
              </td>
              <td className="px-2 text-right tabular-nums">{Number(s.vector_score).toFixed(3)}</td>
              <td className="px-2 text-right tabular-nums">{Number(s.lexical_score).toFixed(2)}</td>
              <td className="px-2 text-right tabular-nums">{Number(s.final_score).toFixed(3)}</td>
              <td className="px-2 text-right tabular-nums">{String(s.tokens)}</td>
              <td className="whitespace-nowrap pl-3 text-slate-500">{String(s.reason)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {dropped.length > 0 && (
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer">{dropped.length} candidate chunk(s) dropped</summary>
          <ul className="mt-1 space-y-0.5">
            {dropped.map((d, i) => (
              <li key={i}>
                {String(d.document)} - {String(d.reason)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function StepDetails({ step }: { step: RunStep }) {
  const a = step.attributes;
  switch (step.step_type) {
    case "context_build":
      return <ContextDetails a={a} />;
    case "tool_discovery":
      return (
        <div className="space-y-1">
          <List label="discovered via MCP" items={a.discovered} />
          <List label="enabled for tenant" items={a.enabled} />
          <List label="approval-gated" items={a.approval_gated} />
          <List label="disabled (never offered to the model)" items={a.disabled} />
          {Boolean(a.error) && <p className="text-xs text-rose-600">{String(a.error)}</p>}
        </div>
      );
    case "llm_call":
      return (
        <div className="space-y-2 text-xs">
          <div className="flex flex-wrap gap-4 text-slate-600">
            <span>model <b className="font-mono">{String(a.model)}</b></span>
            <span>stop <b>{String(a.stop_reason)}</b></span>
            <span>{String(a.input_tokens)} in / {String(a.output_tokens)} out tokens</span>
            <span>{String(a.cache_read_tokens ?? 0)} cache-read tokens</span>
          </div>
          {Array.isArray(a.notes) && a.notes.length > 0 && <List label="notes" items={a.notes} />}
          <div>
            <span className="text-slate-500">decision:</span>
            <JsonView value={a.actions} maxHeight="10rem" />
          </div>
          {Boolean(a.thinking_summary) && (
            <details>
              <summary className="cursor-pointer text-slate-500">reasoning summary</summary>
              <p className="mt-1 whitespace-pre-wrap text-slate-600">{String(a.thinking_summary)}</p>
            </details>
          )}
        </div>
      );
    case "tool_call":
      return (
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <p className="mb-1 text-xs text-slate-500">
              request <SourceChip id={String(a.source_id)} />
            </p>
            <JsonView value={a.arguments} maxHeight="12rem" />
          </div>
          <div>
            <p className="mb-1 text-xs text-slate-500">
              {a.error ? "error" : "result"} {a.truncated ? "(truncated to fit the budget)" : ""} - {String(a.result_tokens)} tokens
            </p>
            {a.error ? <p className="rounded bg-rose-50 p-2 text-xs text-rose-700">{String(a.error)}</p> : <JsonView value={a.result} maxHeight="12rem" />}
          </div>
        </div>
      );
    default:
      return <JsonView value={a} maxHeight="14rem" />;
  }
}

export function Timeline({ steps }: { steps: RunStep[] }) {
  const [open, setOpen] = useState<number | null>(steps.find((s) => s.step_type === "context_build")?.seq ?? null);
  const max = Math.max(1, ...steps.map((s) => s.latency_ms));
  return (
    <ol className="space-y-2">
      {steps.map((step) => (
        <li key={step.seq} className="rounded-md border border-slate-200">
          <button className="flex w-full items-center gap-3 px-3 py-2 text-left" onClick={() => setOpen(open === step.seq ? null : step.seq)}>
            <span className="w-6 text-right font-mono text-xs text-slate-400">{step.seq}</span>
            <span className={cx("h-2.5 w-2.5 shrink-0 rounded-full", STEP_COLOR[step.step_type] ?? "bg-slate-400")} />
            <span className="w-40 shrink-0 text-sm font-medium text-slate-800">{STEP_LABEL[step.step_type] ?? step.step_type}</span>
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-500">{step.name}</span>
            <Badge value={step.status} />
            <span className="hidden w-32 sm:block">
              <span className="block h-1.5 rounded bg-slate-100">
                <span className={cx("block h-1.5 rounded", STEP_COLOR[step.step_type] ?? "bg-slate-400")} style={{ width: `${Math.max(2, (step.latency_ms / max) * 100)}%` }} />
              </span>
            </span>
            <span className="w-16 text-right font-mono text-xs tabular-nums text-slate-600">{step.latency_ms} ms</span>
          </button>
          {open === step.seq && (
            <div className="border-t border-slate-100 bg-slate-50/60 p-3">
              <StepDetails step={step} />
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}
