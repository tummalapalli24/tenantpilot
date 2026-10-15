"use client";

import type { User } from "./types";

const TOKEN_KEY = "tp.token";
/** Fired whenever the session changes; RequireAuth redirects to /login when it disappears. */
export const SESSION_EVENT = "tp-session";
const USER_KEY = "tp.user";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public requestId: string | null,
  ) {
    super(message);
  }
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function getSession(): { token: string; user: User } | null {
  const store = storage();
  const token = store?.getItem(TOKEN_KEY);
  const user = store?.getItem(USER_KEY);
  if (!token || !user) return null;
  try {
    return { token, user: JSON.parse(user) as User };
  } catch {
    return null;
  }
}

export function saveSession(token: string, user: User): void {
  storage()?.setItem(TOKEN_KEY, token);
  storage()?.setItem(USER_KEY, JSON.stringify(user));
  window.dispatchEvent(new Event(SESSION_EVENT));
}

export function clearSession(): void {
  storage()?.removeItem(TOKEN_KEY);
  storage()?.removeItem(USER_KEY);
  window.dispatchEvent(new Event(SESSION_EVENT));
}

interface Options {
  method?: string;
  body?: unknown;
  form?: FormData;
  tenant?: string;
}

/** Calls the TenantPilot API (same-origin /api/*). Throws ApiError with the server's detail. */
export async function api<T>(path: string, { method = "GET", body, form, tenant }: Options = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const session = getSession();
  if (session) headers.Authorization = `Bearer ${session.token}`;
  if (tenant) headers["X-Tenant-ID"] = tenant;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  if (res.status === 401 && path !== "/auth/login") {
    clearSession(); // RequireAuth sees the session disappear and routes to /login
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : res.statusText;
    throw new ApiError(message, res.status, res.headers.get("x-request-id"));
  }
  return data as T;
}

export function can(user: User | null | undefined, permission: string): boolean {
  if (!user) return false;
  const matrix: Record<string, string[]> = {
    "tenants:manage": ["admin"],
    "connectors:manage": ["admin"],
    "tools:manage": ["admin"],
    "config:write": ["admin", "engineer"],
    "documents:write": ["admin", "engineer"],
    "agent:run": ["admin", "engineer"],
    "agent:approve": ["admin", "engineer"],
    "audit:read": ["admin", "engineer"],
  };
  return (matrix[permission] ?? ["admin", "engineer", "viewer"]).includes(user.role);
}
