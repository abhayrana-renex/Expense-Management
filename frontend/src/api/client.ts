import type { Paged, TokenResponse } from "./types";

const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000/api";
const ORIGIN = new URL(BASE).origin;

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public errors: Record<string, unknown>[] = [],
  ) {
    super(message);
  }
}

// Tokens live in memory + sessionStorage (cleared when the tab closes).
const store = {
  get access() { return sessionStorage.getItem("ep.access"); },
  get refresh() { return sessionStorage.getItem("ep.refresh"); },
  set(t: TokenResponse) {
    sessionStorage.setItem("ep.access", t.access_token);
    sessionStorage.setItem("ep.refresh", t.refresh_token);
  },
  clear() {
    sessionStorage.removeItem("ep.access");
    sessionStorage.removeItem("ep.refresh");
  },
};
export const tokens = store;

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn; }

let refreshing: Promise<boolean> | null = null;
async function tryRefresh(): Promise<boolean> {
  const rt = store.refresh;
  if (!rt) return false;
  refreshing ??= fetch(`${BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: rt }),
  })
    .then(async (r) => {
      if (!r.ok) return false;
      store.set(await r.json());
      return true;
    })
    .catch(() => false)
    .finally(() => { refreshing = null; });
  return refreshing;
}

type Query = Record<string, string | number | boolean | null | undefined>;

function qs(params?: Query): string {
  if (!params) return "";
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  });
  const out = s.toString();
  return out ? `?${out}` : "";
}

async function raw(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers);
  if (store.access) headers.set("Authorization", `Bearer ${store.access}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (res.status === 401 && retry && !path.startsWith("/auth/login")) {
    if (await tryRefresh()) return raw(path, init, false);
    store.clear();
    onUnauthorized();
  }
  if (!res.ok) {
    let body: { detail?: string; code?: string; errors?: Record<string, unknown>[] } = {};
    try { body = await res.json(); } catch { /* not JSON */ }
    throw new ApiError(res.status, body.code ?? `HTTP_${res.status}`,
      body.detail ?? res.statusText, body.errors ?? []);
  }
  return res;
}

export const api = {
  /** Backend origin, for turning server-relative URLs (signed links) into absolute ones. */
  origin: ORIGIN,
  async get<T>(path: string, params?: Query): Promise<T> {
    return (await raw(path + qs(params))).json();
  },
  /** Body plus the X-Total-Count header. */
  async withTotal<T>(path: string, params?: Query): Promise<{ data: T; total: number }> {
    const res = await raw(path + qs(params));
    return { data: await res.json(), total: Number(res.headers.get("X-Total-Count") ?? 0) };
  },
  async page<T>(path: string, params?: Query): Promise<Paged<T>> {
    const { data, total } = await api.withTotal<T[]>(path, params);
    return { rows: data, total };
  },
  async post<T>(path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
    const res = await raw(path, { method: "POST", body: body === undefined ? undefined
      : JSON.stringify(body), headers });
    return res.status === 204 ? (undefined as T) : res.json();
  },
  async put<T>(path: string, body: unknown): Promise<T> {
    return (await raw(path, { method: "PUT", body: JSON.stringify(body) })).json();
  },
  async patch<T>(path: string, body: unknown): Promise<T> {
    return (await raw(path, { method: "PATCH", body: JSON.stringify(body) })).json();
  },
  async del<T>(path: string): Promise<T> {
    return (await raw(path, { method: "DELETE" })).json();
  },
  async upload<T>(path: string, file: File): Promise<T> {
    const fd = new FormData();
    fd.append("file", file);
    return (await raw(path, { method: "POST", body: fd })).json();
  },
  /** Download an authenticated file (export) and save it. */
  async download(path: string, params?: Query, body?: unknown): Promise<void> {
    const res = await raw(path + qs(params), body === undefined ? undefined
      : { method: "POST", body: JSON.stringify(body) });
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") ?? "";
    const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? "download";
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: name });
    a.click();
    URL.revokeObjectURL(url);
  },
  /** Open a bill using a 60-second signed URL (never puts the JWT in a URL). */
  async openDocument(id: number): Promise<void> {
    const win = window.open("", "_blank");
    const { url } = await api.get<{ url: string }>(`/expenses/documents/${id}/download-url`);
    if (win) win.location.href = ORIGIN + url;
  },
  newIdempotencyKey(): string {
    return crypto.randomUUID();
  },
};
