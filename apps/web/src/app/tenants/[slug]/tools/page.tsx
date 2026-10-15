"use client";

import { useState } from "react";

import { useUser } from "@/components/session";
import { useTenant } from "@/components/tenant-context";
import { Badge, Button, Card, ErrorBanner, JsonView, Spinner } from "@/components/ui";
import { api, can } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { TenantTool } from "@/lib/types";

export default function ToolsPage() {
  const { tenant, reload: reloadTenant } = useTenant();
  const user = useUser();
  const { data: tools, error, setData } = useApi<TenantTool[]>(`/tenants/${tenant.slug}/tools`);
  const [draft, setDraft] = useState<Record<string, { enabled: boolean; requires_approval: boolean }>>({});
  const [saveError, setSaveError] = useState<unknown>(null);
  const editable = can(user, "tools:manage");

  const value = (tool: TenantTool) => draft[tool.name] ?? { enabled: tool.enabled, requires_approval: tool.requires_approval };

  async function save() {
    setSaveError(null);
    try {
      const permissions = Object.entries(draft).map(([tool_name, v]) => ({ tool_name, ...v }));
      setData(await api<TenantTool[]>(`/tenants/${tenant.slug}/tools`, { method: "POST", body: { permissions } }));
      setDraft({});
      reloadTenant();
    } catch (err) {
      setSaveError(err);
    }
  }

  return (
    <Card
      title="Tool governance"
      actions={
        editable && (
          <Button disabled={Object.keys(draft).length === 0} onClick={save}>
            Save permissions
          </Button>
        )
      }
    >
      <p className="mb-4 text-sm text-slate-500">
        Tools are discovered from the MCP server. New tools start disabled for every tenant. Disabled tools are never offered to the model, and the
        MCP server re-checks every call. Write tools always require human approval while the tenant&apos;s approval policy is on.
      </p>
      <ErrorBanner error={error ?? saveError} />
      {!tools && !error && <Spinner />}
      <div className="divide-y divide-slate-100">
        {tools?.map((tool) => {
          const v = value(tool);
          return (
            <div key={tool.name} className="grid gap-3 py-3 md:grid-cols-5">
              <div className="md:col-span-3">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm font-medium">{tool.name}</span>
                  <Badge value={tool.read_only ? "read" : "write"} />
                  {tool.effective_requires_approval && v.enabled && <Badge value="approval required" className="bg-amber-50 text-amber-800 ring-amber-200" />}
                </div>
                <p className="mt-1 text-xs text-slate-500">{tool.description}</p>
                <details className="mt-1">
                  <summary className="cursor-pointer text-xs text-slate-400">input schema</summary>
                  <JsonView value={tool.input_schema} maxHeight="12rem" />
                </details>
              </div>
              <div className="flex items-start gap-6 text-sm md:col-span-2 md:justify-end">
                <label className="flex items-center gap-2">
                  <input type="checkbox" disabled={!editable} checked={v.enabled} onChange={(e) => setDraft({ ...draft, [tool.name]: { ...v, enabled: e.target.checked } })} />
                  enabled
                </label>
                <label className="flex items-center gap-2" title="Force approval even for a read-only tool">
                  <input
                    type="checkbox"
                    disabled={!editable || !tool.read_only}
                    checked={tool.read_only ? v.requires_approval : true}
                    onChange={(e) => setDraft({ ...draft, [tool.name]: { ...v, requires_approval: e.target.checked } })}
                  />
                  needs approval
                </label>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
