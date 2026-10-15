"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Badge, Button, Card, ErrorBanner, Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { CatalogTool, ModelInfo, Tenant } from "@/lib/types";

function slugify(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 63);
}

export default function NewTenantPage() {
  const router = useRouter();
  const { data: catalog, error: catalogError } = useApi<CatalogTool[]>("/tools/catalog");
  const { data: models } = useApi<ModelInfo[]>("/models");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [apiBaseUrl, setApiBaseUrl] = useState("http://mock-customer-api:8081/acme/v1");
  const [apiKey, setApiKey] = useState("");
  const [tools, setTools] = useState<string[]>(["search_knowledge", "get_customer"]);
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [approval, setApproval] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const defaultModel = models?.find((m) => m.available && m.provider === "anthropic")?.id ?? "offline-heuristic";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const tenant = await api<Tenant>("/tenants", {
        method: "POST",
        body: {
          name,
          slug,
          api_base_url: apiBaseUrl || null,
          api_key: apiKey || null,
          allowed_tools: tools,
          config: { model_name: model || defaultModel, system_prompt: prompt || null, approval_required: approval },
        },
      });
      router.push(`/tenants/${tenant.slug}`);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Create customer</h1>
        <p className="text-sm text-slate-500">
          Onboard a customer in one step: tenant, encrypted API credential, connector, tool permissions and model config.
        </p>
      </div>
      <Card title="Customer">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Tenant name">
            <Input
              required
              value={name}
              placeholder="Globex Facilities"
              onChange={(e) => {
                setName(e.target.value);
                if (!slugTouched) setSlug(slugify(e.target.value));
              }}
            />
          </Field>
          <Field label="Slug" hint="Used in URLs and the X-Tenant-ID header">
            <Input required value={slug} pattern="[a-z0-9][a-z0-9-]*" onChange={(e) => { setSlugTouched(true); setSlug(e.target.value); }} />
          </Field>
        </div>
      </Card>
      <Card title="Customer system (REST API)">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="API base URL" hint="Uses the field-service REST template; edit mappings later under Connectors">
            <Input value={apiBaseUrl} onChange={(e) => setApiBaseUrl(e.target.value)} placeholder="https://api.customer.example/v1" />
          </Field>
          <Field label="API key" hint="Encrypted at rest; never shown again">
            <Input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="acme-demo-key-123" />
          </Field>
        </div>
        <p className="mt-3 text-xs text-slate-500">Knowledge documents are uploaded after creation (Knowledge tab).</p>
      </Card>
      <Card title="Allowed tools (discovered from the MCP server)">
        <ErrorBanner error={catalogError} />
        <div className="space-y-2">
          {catalog?.map((tool) => (
            <label key={tool.name} className="flex items-start gap-3 rounded-md border border-slate-200 p-3">
              <input
                type="checkbox"
                className="mt-1"
                checked={tools.includes(tool.name)}
                onChange={(e) => setTools(e.target.checked ? [...tools, tool.name] : tools.filter((t) => t !== tool.name))}
              />
              <div>
                <div className="flex items-center gap-2 font-mono text-sm">
                  {tool.name} <Badge value={tool.risk} />
                </div>
                <p className="text-xs text-slate-500">{tool.description}</p>
              </div>
            </label>
          ))}
        </div>
      </Card>
      <Card title="Model configuration">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Model">
            <Select value={model || defaultModel} onChange={(e) => setModel(e.target.value)}>
              {models?.map((m) => (
                <option key={m.id} value={m.id} disabled={!m.available}>
                  {m.id} {m.available ? "" : "(needs ANTHROPIC_API_KEY)"}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Human approval for write actions">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={approval} onChange={(e) => setApproval(e.target.checked)} /> required
            </label>
          </Field>
          <div className="sm:col-span-2">
            <Field label="Tenant instructions (optional)" hint="Appended to the platform system prompt">
              <Textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
            </Field>
          </div>
        </div>
      </Card>
      <ErrorBanner error={error} />
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={() => router.back()}>
          Cancel
        </Button>
        <Button type="submit" disabled={busy || !name || !slug}>
          {busy ? "Creating..." : "Create customer"}
        </Button>
      </div>
    </form>
  );
}
