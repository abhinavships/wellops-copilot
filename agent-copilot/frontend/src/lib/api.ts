import type { ChatSummary, Citation, HandleData, IngestReport, RecordData, User } from "@/contracts";
import { MOCK_RECORDS, MOCK_USERS, SCENARIOS } from "@/mock/fixtures";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000").replace(/\/$/, "");
export const MOCK = process.env.NEXT_PUBLIC_MOCK === "1";
export const DEFAULT_TODAY = "2026-10-17"; // = backend settings.clock_today


export interface Ctx { userId: string; chatId: string; today: string }
export const ctxHeaders = (c: Ctx): Record<string, string> => ({
  "Content-Type": "application/json", "X-User-Id": c.userId, "X-Chat-Id": c.chatId, "X-Clock-Today": c.today,
});

async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`);
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return (await r.json()) as T;
}

export async function fetchUsers(): Promise<User[]> {
  if (MOCK) return MOCK_USERS;
  const j = await getJson<{ users: User[] } | User[]>("/api/users");
  return Array.isArray(j) ? j : j.users;
}

export async function fetchChats(principal: string): Promise<ChatSummary[]> {
  if (MOCK) return [];
  const j = await getJson<{ chats: ChatSummary[] } | ChatSummary[]>(`/api/chats?principal=${encodeURIComponent(principal)}`);
  return Array.isArray(j) ? j : j.chats;
}

const handleCache = new Map<string, Promise<HandleData>>();
export function fetchHandle(handle: string, chatId: string): Promise<HandleData> {
  const key = `${chatId}:${handle}`;
  let p = handleCache.get(key);
  if (!p) {
    p = MOCK
      ? (async () => {
          for (const s of SCENARIOS) if (s.handles[handle]) return s.handles[handle];
          throw new Error(`Unknown handle ${handle}`);
        })()
      : getJson<HandleData>(`/api/handles/${encodeURIComponent(handle)}?chat_id=${encodeURIComponent(chatId)}`);
    p.catch(() => handleCache.delete(key));
    handleCache.set(key, p);
  }
  return p;
}

export async function undoAssertion(id: string, c: Ctx): Promise<void> {
  if (MOCK) return;
  const r = await fetch(`${API_BASE}/api/assertions/${encodeURIComponent(id)}/undo`, { method: "POST", headers: ctxHeaders(c) });
  if (!r.ok) throw new Error(`Undo failed: HTTP ${r.status}`);
}

/**
 * Evidence-chain rows use kinds the records API does not know: "report_fact"/"report_compliance" with id
 * "MNT-…#field" are spans inside that maintenance report. Map any row to the citation the API can resolve.
 */
export function citationFor(kind: string, id: string, span?: unknown): Citation {
  if (kind.startsWith("report_")) {
    const sp = Array.isArray(span) && span.length === 2 ? (span as [number, number]) : null;
    return { kind: "report", id: id.split("#")[0], span: sp, label: id.split("#")[1] ?? null };
  }
  return { kind: kind as Citation["kind"], id };
}

/** GET /api/records/{kind}/{id} -> {kind, id, record}; returns the record (one row or several). */
export async function fetchRecord(kind: string, id: string, c: Ctx): Promise<RecordData["record"]> {
  if (MOCK) {
    const rec = MOCK_RECORDS[`${kind}:${id}`];
    if (rec) return rec.record;
    throw new Error("Record not in the fixture set");
  }
  const r = await fetch(`${API_BASE}/api/records/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`, { headers: ctxHeaders(c) });
  if (!r.ok) throw new Error(`Record ${kind}/${id} -> HTTP ${r.status}`);
  return ((await r.json()) as RecordData).record;
}

/** Normalise rows to objects regardless of whether the backend sends arrays or dicts. */
export function rowsAsObjects(h: HandleData): Record<string, unknown>[] {
  const rows = h.rows as unknown[];
  return rows.map((r) => (Array.isArray(r) ? Object.fromEntries(h.columns.map((c, i) => [c, r[i]])) : (r as Record<string, unknown>)));
}

/** POST /api/ingest (multipart `file`) -> IngestReport. Mock mode does not upload; it reports what it saw. */
export async function ingestFile(file: File, c: Ctx): Promise<IngestReport> {
  if (MOCK) return { file: file.name, kind: file.type || "unknown", tables: 0, rows: 0, docs: 0, images: 0,
    summary: `Mock mode: ${file.name} (${Math.round(file.size / 1024)} KB) was not uploaded. Start the API to ingest it.` };
  const fd = new FormData(); fd.append("file", file);
  const h = ctxHeaders(c); delete h["Content-Type"]; // the browser sets the multipart boundary
  const r = await fetch(`${API_BASE}/api/ingest`, { method: "POST", headers: h, body: fd });
  if (!r.ok) throw new Error(`Upload failed: HTTP ${r.status}${r.status === 404 ? " (is /api/ingest deployed?)" : ""}`);
  return (await r.json()) as IngestReport;
}

/** Human-in-the-loop decision on a proof card (no model call). */
export async function resolveDispute(id: string, action: "correct" | "keep", c: Ctx, value?: unknown): Promise<Record<string, unknown>> {
  if (MOCK) return { dispute_id: id, status: action === "keep" ? "kept" : "corrected", assertion_id: "A-mock" };
  const r = await fetch(`${API_BASE}/api/disputes/${encodeURIComponent(id)}/resolve`, {
    method: "POST", headers: ctxHeaders(c), body: JSON.stringify({ action, value }) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
