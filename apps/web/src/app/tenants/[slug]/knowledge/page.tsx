"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";

import { useUser } from "@/components/session";
import { useTenant } from "@/components/tenant-context";
import { Badge, Button, Card, ErrorBanner, Input, Spinner, formatDate } from "@/components/ui";
import { api, can } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { DocumentRow, KnowledgeHit } from "@/lib/types";

export default function KnowledgePage() {
  const { tenant, reload: reloadTenant } = useTenant();
  const user = useUser();
  const { data: docs, error, reload } = useApi<DocumentRow[]>(`/tenants/${tenant.slug}/documents`);
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<unknown>(null);
  const [uploading, setUploading] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<KnowledgeHit[] | null>(null);
  const [searchError, setSearchError] = useState<unknown>(null);

  // Poll while any document is still being indexed by the background worker.
  const pending = docs?.some((d) => d.status === "queued" || d.status === "processing");
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(() => {
      reload();
      reloadTenant();
    }, 1500);
    return () => clearInterval(timer);
  }, [pending, reload, reloadTenant]);

  async function upload(event: FormEvent) {
    event.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    setUploading(true);
    setUploadError(null);
    try {
      await api(`/tenants/${tenant.slug}/documents`, { method: "POST", form });
      if (fileRef.current) fileRef.current.value = "";
      reload();
      reloadTenant();
    } catch (err) {
      setUploadError(err);
    } finally {
      setUploading(false);
    }
  }

  async function search(event: FormEvent) {
    event.preventDefault();
    setSearchError(null);
    try {
      setHits(await api<KnowledgeHit[]>(`/tenants/${tenant.slug}/knowledge/search?q=${encodeURIComponent(query)}&k=5`));
    } catch (err) {
      setSearchError(err);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div className="space-y-4">
        {can(user, "documents:write") && (
          <Card title="Upload knowledge">
            <form onSubmit={upload} className="flex flex-wrap items-center gap-3">
              <input ref={fileRef} type="file" accept=".md,.markdown,.txt,.pdf,.csv,.json" className="text-sm" />
              <Button type="submit" disabled={uploading}>
                {uploading ? "Uploading..." : "Upload & index"}
              </Button>
            </form>
            <p className="mt-2 text-xs text-slate-500">Markdown, text or PDF up to 5 MB. Chunked by headings, embedded, stored in pgvector.</p>
            <div className="mt-2">
              <ErrorBanner error={uploadError} />
            </div>
          </Card>
        )}
        <Card title="Documents">
          <ErrorBanner error={error} />
          {!docs && !error && <Spinner />}
          <ul className="divide-y divide-slate-100">
            {docs?.map((doc) => (
              <li key={doc.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="truncate font-medium">{doc.filename}</div>
                  <div className="text-xs text-slate-500">
                    {(doc.size_bytes / 1024).toFixed(1)} KB - {doc.chunk_count} chunks - {doc.source} - {formatDate(doc.created_at)}
                  </div>
                  {doc.error && <div className="text-xs text-rose-600">{doc.error}</div>}
                </div>
                <Badge value={doc.status} />
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Card title="Retrieval playground">
        <form onSubmit={search} className="flex gap-2">
          <Input value={query} placeholder="e.g. E-ACT-401 activation token" onChange={(e) => setQuery(e.target.value)} />
          <Button type="submit" disabled={query.trim().length < 2}>
            Search
          </Button>
        </form>
        <p className="mt-2 text-xs text-slate-500">Exactly what the retriever returns for this tenant (other tenants&apos; documents are never visible).</p>
        <div className="mt-3">
          <ErrorBanner error={searchError} />
        </div>
        <ul className="mt-3 space-y-3">
          {hits?.map((hit) => (
            <li key={hit.chunk_id} className="rounded-md border border-slate-200 p-3">
              <div className="flex items-center justify-between text-xs text-slate-500">
                <span className="truncate">
                  {hit.document} &rsaquo; {hit.section}
                </span>
                <span className="font-mono">score {hit.score.toFixed(3)}</span>
              </div>
              <p className="mt-1 line-clamp-4 whitespace-pre-wrap text-sm text-slate-700">{hit.content}</p>
            </li>
          ))}
          {hits?.length === 0 && <li className="text-sm text-slate-500">No matches.</li>}
        </ul>
      </Card>
    </div>
  );
}
