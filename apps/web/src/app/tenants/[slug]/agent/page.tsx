"use client";

import Link from "next/link";
import { type FormEvent, useState } from "react";

import { AnswerView } from "@/components/answer";
import { useUser } from "@/components/session";
import { useTenant } from "@/components/tenant-context";
import { Badge, Button, Card, ErrorBanner, Input, JsonView, Spinner, Textarea } from "@/components/ui";
import { api, can } from "@/lib/api";
import type { AgentRunResponse, PendingAction } from "@/lib/types";

const SUGGESTIONS: Record<string, string[]> = {
  acme: [
    "Customer C-104 says their installation failed. What happened and what should we do?",
    "Old Pueblo Hotel (C-106) reports a compressor alarm. Please open a ticket.",
    "Catalina Schools (C-105) says their firmware update is stuck. What's wrong?",
    "What is the response time for a critical ticket?",
  ],
  betacorp: [
    "What's the status of account ACC-2043?",
    "Account ACC-2041 had a temperature excursion, please open a ticket.",
  ],
};

function ApprovalCard({ action, onDecide, busy }: { action: PendingAction; onDecide: (d: "approve" | "reject", comment: string) => void; busy: boolean }) {
  const [comment, setComment] = useState("");
  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-4">
      <div className="flex items-center gap-2 text-sm font-medium text-amber-900">
        <span>Approval required:</span>
        <span className="font-mono">{action.tool}</span>
        {typeof action.arguments.priority === "string" && <Badge value={action.arguments.priority} />}
      </div>
      <p className="mt-1 text-xs text-amber-800">{action.reason}</p>
      <div className="mt-3">
        <JsonView value={action.arguments} maxHeight="12rem" />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Input className="max-w-sm bg-white" placeholder="Comment (optional, saved in the audit log)" value={comment} onChange={(e) => setComment(e.target.value)} />
        <Button disabled={busy} onClick={() => onDecide("approve", comment)}>
          Approve & execute
        </Button>
        <Button variant="secondary" disabled={busy} onClick={() => onDecide("reject", comment)}>
          Reject
        </Button>
      </div>
    </div>
  );
}

export default function AgentConsole() {
  const { tenant, reload } = useTenant();
  const user = useUser();
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<AgentRunResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const allowed = can(user, "agent:run");

  async function run(event?: FormEvent) {
    event?.preventDefault();
    if (!message.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api<AgentRunResponse>("/agent/run", { method: "POST", tenant: tenant.slug, body: { message } }));
      reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function decide(action: PendingAction, decision: "approve" | "reject", comment: string) {
    if (!result) return;
    setBusy(true);
    setError(null);
    try {
      setResult(
        await api<AgentRunResponse>(`/agent/${result.run_id}/approve`, {
          method: "POST",
          tenant: tenant.slug,
          body: { tool_call_id: action.tool_call_id, decision, comment: comment || null },
        }),
      );
      reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-5">
      <div className="space-y-4 lg:col-span-2">
        <Card title="Ask the agent">
          <form onSubmit={run} className="space-y-3">
            <Textarea rows={5} value={message} disabled={!allowed} placeholder="Describe the customer's situation..." onChange={(e) => setMessage(e.target.value)} />
            <Button type="submit" disabled={busy || !allowed || !message.trim()}>
              {busy ? "Running..." : "Run agent"}
            </Button>
            {!allowed && <p className="text-xs text-slate-500">Your role ({user?.role}) can view runs but not start them.</p>}
          </form>
        </Card>
        <Card title="Try">
          <ul className="space-y-2">
            {(SUGGESTIONS[tenant.slug] ?? SUGGESTIONS.acme).map((s) => (
              <li key={s}>
                <button className="text-left text-sm text-indigo-700 hover:underline" onClick={() => setMessage(s)}>
                  {s}
                </button>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <div className="space-y-4 lg:col-span-3">
        <ErrorBanner error={error} />
        {busy && !result && <Spinner label="Agent is working: retrieving knowledge, calling tools..." />}
        {!result && !busy && (
          <div className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
            Answers, approval requests and a link to the full trace appear here.
          </div>
        )}
        {result && (
          <Card
            title={
              <span className="flex items-center gap-2">
                Result <Badge value={result.status} />
              </span>
            }
            actions={
              <Link className="text-sm text-indigo-600 hover:underline" href={`/tenants/${tenant.slug}/runs/${result.run_id}`}>
                View trace
              </Link>
            }
          >
            <div className="space-y-4">
              {result.pending_actions.map((action) => (
                <ApprovalCard key={action.tool_call_id} action={action} busy={busy} onDecide={(d, c) => decide(action, d, c)} />
              ))}
              {result.error && <ErrorBanner error={new Error(result.error)} />}
              {result.answer && <AnswerView answer={result.answer} />}
              <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-100 pt-3 font-mono text-[11px] text-slate-500">
                <span>model {result.model}</span>
                <span>{result.latency_ms} ms</span>
                <span>{result.usage.llm_calls} LLM calls</span>
                <span>
                  {result.usage.input_tokens} in / {result.usage.output_tokens} out tokens
                </span>
                <span>request {result.request_id}</span>
              </div>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
