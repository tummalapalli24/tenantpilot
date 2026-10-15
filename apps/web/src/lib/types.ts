// Mirrors packages/schemas/api.py (the FastAPI OpenAPI schema is the source of truth).

export type Role = "admin" | "engineer" | "viewer";

export interface User {
  id: string;
  email: string;
  role: Role;
  tenant_ids: string[];
}

export interface TenantConfig {
  system_prompt: string;
  model_name: string;
  temperature: number;
  reasoning_effort: "low" | "medium" | "high";
  max_context_tokens: number;
  retrieval_top_k: number;
  approval_required: boolean;
  updated_at: string;
}

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  status: "active" | "suspended";
  created_at: string;
  config: TenantConfig | null;
}

export interface TenantDetail extends Tenant {
  stats: {
    documents: number;
    indexed_documents: number;
    chunks: number;
    connectors: number;
    enabled_tools: number;
    runs: number;
    runs_awaiting_approval: number;
  };
}

export interface ModelInfo {
  id: string;
  provider: "anthropic" | "offline";
  available: boolean;
  supports_temperature: boolean;
  supports_effort: boolean;
  note: string | null;
}

export interface CatalogTool {
  name: string;
  title: string | null;
  description: string;
  input_schema: Record<string, unknown>;
  read_only: boolean;
  risk: string;
}

export interface TenantTool extends CatalogTool {
  enabled: boolean;
  requires_approval: boolean;
  effective_requires_approval: boolean;
}

export interface Connector {
  id: string;
  name: string;
  connector_type: "rest_api" | "postgres";
  base_url: string | null;
  credentials_secret_ref: string | null;
  config: Record<string, unknown>;
  status: string;
  last_checked_at: string | null;
  created_at: string;
}

export interface DocumentRow {
  id: string;
  filename: string;
  source: string;
  content_type: string;
  size_bytes: number;
  status: "queued" | "processing" | "indexed" | "failed";
  error: string | null;
  chunk_count: number;
  created_at: string;
  indexed_at: string | null;
}

export interface KnowledgeHit {
  chunk_id: string;
  document: string;
  section: string | null;
  score: number;
  content: string;
}

export interface Finding {
  statement: string;
  source_ids: string[];
}

export interface FinalAnswer {
  summary: string;
  findings: Finding[];
  recommended_actions: string[];
  confidence: "low" | "medium" | "high";
  needs_human_followup: boolean;
}

export interface PendingAction {
  tool_call_id: string;
  tool: string;
  arguments: Record<string, unknown>;
  reason: string;
}

export interface AgentRunResponse {
  run_id: string;
  tenant_id: string;
  status: "running" | "awaiting_approval" | "completed" | "failed";
  answer: FinalAnswer | null;
  pending_actions: PendingAction[];
  error: string | null;
  request_id: string | null;
  trace_id: string | null;
  latency_ms: number | null;
  model: string;
  usage: { input_tokens: number; output_tokens: number; cache_read_tokens: number; llm_calls: number };
}

export interface RunSummary {
  id: string;
  tenant_id: string;
  user_id: string | null;
  request: string;
  status: string;
  model: string;
  latency_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
}

export interface RunStep {
  seq: number;
  step_type: string;
  name: string;
  status: string;
  started_at: string;
  latency_ms: number;
  attributes: Record<string, unknown>;
}

export interface ToolCall {
  id: string;
  tool_use_id: string;
  source_id: string | null;
  tool_name: string;
  request_json: Record<string, unknown>;
  response_json: Record<string, unknown> | null;
  status: string;
  requires_approval: boolean;
  approval_status: string | null;
  approved_by: string | null;
  approved_at: string | null;
  latency_ms: number | null;
  error: string | null;
  created_at: string;
}

export interface RunDetail extends RunSummary {
  response: FinalAnswer | null;
  error: string | null;
  request_id: string | null;
  trace_id: string | null;
  cache_read_tokens: number;
  llm_calls: number;
  completed_at: string | null;
  steps: RunStep[];
  tool_calls: ToolCall[];
}

export interface AuditLog {
  id: string;
  actor_email: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  details: Record<string, unknown>;
  request_id: string | null;
  created_at: string;
}
