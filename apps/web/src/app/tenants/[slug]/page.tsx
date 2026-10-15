"use client";

import { useState } from "react";

import { useUser } from "@/components/session";
import { useTenant } from "@/components/tenant-context";
import { Button, Card, ErrorBanner, Field, Input, Select, Stat, Textarea } from "@/components/ui";
import { api, can } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { ModelInfo, TenantConfig } from "@/lib/types";

export default function TenantOverview() {
  const { tenant, reload } = useTenant();
  const user = useUser();
  const { data: models } = useApi<ModelInfo[]>("/models");
  const [draft, setDraft] = useState<TenantConfig | null>(tenant.config);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const editable = can(user, "config:write");
  const model = models?.find((m) => m.id === draft?.model_name);

  if (!draft) return null;
  const set = <K extends keyof TenantConfig>(key: K, value: TenantConfig[K]) => {
    setDraft({ ...draft, [key]: value });
    setSaved(false);
  };

  async function save() {
    if (!draft) return;
    setError(null);
    try {
      await api(`/tenants/${tenant.slug}`, {
        method: "PATCH",
        body: {
          config: {
            system_prompt: draft.system_prompt,
            model_name: draft.model_name,
            temperature: draft.temperature,
            reasoning_effort: draft.reasoning_effort,
            max_context_tokens: draft.max_context_tokens,
            retrieval_top_k: draft.retrieval_top_k,
            approval_required: draft.approval_required,
          },
        },
      });
      setSaved(true);
      reload();
    } catch (err) {
      setError(err);
    }
  }

  const s = tenant.stats;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Documents" value={s.documents} sub={`${s.indexed_documents} indexed - ${s.chunks} chunks`} />
        <Stat label="Connectors" value={s.connectors} />
        <Stat label="Enabled tools" value={s.enabled_tools} />
        <Stat label="Agent runs" value={s.runs} />
        <Stat label="Awaiting approval" value={s.runs_awaiting_approval} />
      </div>
      <Card
        title="Agent configuration"
        actions={
          editable && (
            <Button onClick={save} disabled={saved}>
              {saved ? "Saved" : "Save changes"}
            </Button>
          )
        }
      >
        <div className="grid gap-4 md:grid-cols-3">
          <Field label="Model">
            <Select disabled={!editable} value={draft.model_name} onChange={(e) => set("model_name", e.target.value)}>
              {models?.map((m) => (
                <option key={m.id} value={m.id} disabled={!m.available}>
                  {m.id} {m.available ? "" : "(needs API key)"}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Reasoning effort" hint={model && !model.supports_effort ? "Not used by this model" : undefined}>
            <Select disabled={!editable} value={draft.reasoning_effort} onChange={(e) => set("reasoning_effort", e.target.value as TenantConfig["reasoning_effort"])}>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </Select>
          </Field>
          <Field label="Temperature" hint={model && !model.supports_temperature ? "Ignored: this model rejects sampling params" : undefined}>
            <Input type="number" step="0.1" min={0} max={1} disabled={!editable} value={draft.temperature} onChange={(e) => set("temperature", Number(e.target.value))} />
          </Field>
          <Field label="Context budget (tokens)">
            <Input type="number" min={1000} step={500} disabled={!editable} value={draft.max_context_tokens} onChange={(e) => set("max_context_tokens", Number(e.target.value))} />
          </Field>
          <Field label="Retrieval top-k">
            <Input type="number" min={1} max={20} disabled={!editable} value={draft.retrieval_top_k} onChange={(e) => set("retrieval_top_k", Number(e.target.value))} />
          </Field>
          <Field label="Write actions">
            <label className="flex items-center gap-2 pt-1.5 text-sm">
              <input type="checkbox" disabled={!editable} checked={draft.approval_required} onChange={(e) => set("approval_required", e.target.checked)} />
              require human approval
            </label>
          </Field>
          <div className="md:col-span-3">
            <Field label="Tenant instructions" hint="Appended to TenantPilot's platform prompt inside <tenant_instructions>">
              <Textarea rows={4} disabled={!editable} value={draft.system_prompt} onChange={(e) => set("system_prompt", e.target.value)} />
            </Field>
          </div>
        </div>
        <div className="mt-3">
          <ErrorBanner error={error} />
        </div>
      </Card>
    </div>
  );
}
