"use client";

import { type FormEvent, useState } from "react";

import { useUser } from "@/components/session";
import { useTenant } from "@/components/tenant-context";
import { Badge, Button, Card, ErrorBanner, Field, Input, JsonView, Select, Spinner, Textarea, formatDate } from "@/components/ui";
import { api, can } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { Connector } from "@/lib/types";

const TEMPLATE = {
  auth_header: "X-API-Key",
  health_path: "/health",
  operations: {
    get_customer: {
      method: "GET",
      path: "/customers/{customer_id}",
      result_path: "customer",
      field_map: { customer_id: "customer_ref", name: "full_name", status: "account_status" },
    },
  },
};

export default function ConnectorsPage() {
  const { tenant, reload: reloadTenant } = useTenant();
  const user = useUser();
  const { data: connectors, error, reload } = useApi<Connector[]>(`/tenants/${tenant.slug}/connectors`);
  const [testing, setTesting] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, string>>({});
  const [form, setForm] = useState({ name: "", connector_type: "rest_api", base_url: "", credential: "", config: JSON.stringify(TEMPLATE, null, 2) });
  const [formError, setFormError] = useState<unknown>(null);

  async function test(connector: Connector) {
    setTesting(connector.id);
    try {
      const res = await api<{ status: string; latency_ms: number; detail: string }>(`/tenants/${tenant.slug}/connectors/${connector.id}/test`, { method: "POST" });
      setResults({ ...results, [connector.id]: `${res.status} - ${res.detail} - ${res.latency_ms} ms` });
      reload();
    } catch (err) {
      setResults({ ...results, [connector.id]: err instanceof Error ? err.message : "failed" });
    } finally {
      setTesting(null);
    }
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    try {
      await api(`/tenants/${tenant.slug}/connectors`, {
        method: "POST",
        body: { ...form, credential: form.credential || null, config: JSON.parse(form.config) },
      });
      setForm({ ...form, name: "", credential: "" });
      reload();
      reloadTenant();
    } catch (err) {
      setFormError(err);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-5">
      <div className="space-y-4 lg:col-span-3">
        <ErrorBanner error={error} />
        {!connectors && !error && <Spinner />}
        {connectors?.map((c) => (
          <Card
            key={c.id}
            title={
              <span className="flex items-center gap-2">
                <span className="font-mono">{c.name}</span> <Badge value={c.connector_type} /> <Badge value={c.status} />
              </span>
            }
            actions={
              can(user, "config:write") && (
                <Button variant="secondary" disabled={testing === c.id} onClick={() => test(c)}>
                  {testing === c.id ? "Testing..." : "Test connection"}
                </Button>
              )
            }
          >
            <dl className="mb-3 grid gap-1 text-xs text-slate-600">
              <div>
                <dt className="inline text-slate-400">endpoint </dt>
                <dd className="inline font-mono">{c.base_url}</dd>
              </div>
              <div>
                <dt className="inline text-slate-400">credential </dt>
                <dd className="inline font-mono">{c.credentials_secret_ref ?? "none"} (encrypted reference - the secret itself is never returned)</dd>
              </div>
              <div>
                <dt className="inline text-slate-400">last checked </dt>
                <dd className="inline">{formatDate(c.last_checked_at)}</dd>
              </div>
            </dl>
            {results[c.id] && <p className="mb-3 text-xs text-slate-700">Test: {results[c.id]}</p>}
            <details>
              <summary className="cursor-pointer text-xs text-slate-500">operations & field mappings ({Object.keys((c.config.operations as object) ?? {}).join(", ")})</summary>
              <div className="mt-2">
                <JsonView value={c.config} maxHeight="24rem" />
              </div>
            </details>
          </Card>
        ))}
      </div>
      {can(user, "connectors:manage") && (
        <div className="lg:col-span-2">
          <Card title="Add connector">
            <form onSubmit={create} className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <Field label="Name">
                  <Input required value={form.name} placeholder="crm-api" onChange={(e) => setForm({ ...form, name: e.target.value })} />
                </Field>
                <Field label="Type">
                  <Select value={form.connector_type} onChange={(e) => setForm({ ...form, connector_type: e.target.value })}>
                    <option value="rest_api">REST API</option>
                    <option value="postgres">PostgreSQL (read-only)</option>
                  </Select>
                </Field>
              </div>
              <Field label="Base URL / DSN without password">
                <Input required value={form.base_url} placeholder="https://api.customer.example/v1" onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
              </Field>
              <Field label="Credential" hint="API key or DB password - encrypted at rest">
                <Input type="password" value={form.credential} onChange={(e) => setForm({ ...form, credential: e.target.value })} />
              </Field>
              <Field label="Config (operations + field_map)">
                <Textarea rows={12} className="font-mono text-xs" value={form.config} onChange={(e) => setForm({ ...form, config: e.target.value })} />
              </Field>
              <ErrorBanner error={formError} />
              <Button type="submit">Add connector</Button>
            </form>
          </Card>
        </div>
      )}
    </div>
  );
}
