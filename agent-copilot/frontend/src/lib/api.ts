import type { ChatSummary, HandleData, User } from "@/contracts";
import { MOCK_RECORDS, SCENARIOS } from "@/mock/fixtures";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000").replace(/\/$/, "");
export const MOCK = process.env.NEXT_PUBLIC_MOCK === "1";
export const DEFAULT_TODAY = "2026-10-17";

export const MOCK_USERS: User[] = [
  { user_id: "U-101", name: "Emily Carter", role: "production_supervisor", team_id: "TEAM-SW" },
  { user_id: "U-102", name: "Jake Morales", role: "field_operator", team_id: "TEAM-SW" },
  { user_id: "U-103", name: "Sarah Mitchell", role: "field_inspector", team_id: "TEAM-SW" },
  { user_id: "U-104", name: "Ryan Brooks", role: "field_inspector", team_id: "TEAM-SW" },
  { user_id: "U-201", name: "Michael Turner", role: "production_supervisor", team_id: "TEAM-NE" },
  { user_id: "U-202", name: "Jessica Reed", role: "field_inspector", team_id: "TEAM-NE" },
  { user_id: "U-203", name: "Ashley Bennett", role: "field_inspector", team_id: "TEAM-NE" },
  { user_id: "U-204", name: "Tyler Hughes", role: "field_operator", team_id: "TEAM-NE" },
  { user_id: "U-301", name: "Robert Hayes", role: "maintenance_lead", team_id: "TEAM-MX" },
  { user_id: "U-302", name: "Daniel Foster", role: "maintenance_technician", team_id: "TEAM-MX" },
  { user_id: "U-303", name: "Kevin Price", role: "maintenance_technician", team_id: "TEAM-MX" },
  { user_id: "U-304", name: "Amy Collins", role: "maintenance_technician", team_id: "TEAM-MX" },
  { user_id: "U-305", name: "Megan Ward", role: "maintenance_technician", team_id: "TEAM-MX" },
  { user_id: "U-401", name: "Laura Simmons", role: "hse_officer", team_id: "TEAM-HSE" },
  { user_id: "U-402", name: "Chris Dalton", role: "hse_officer", team_id: "TEAM-HSE" },
];

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

/** Assumed endpoint (not in the contract yet): raw record behind a citation. */
export async function fetchRecord(kind: string, id: string, c: Ctx): Promise<unknown> {
  if (MOCK) {
    const rec = MOCK_RECORDS[`${kind}:${id}`];
    if (rec) return rec;
    throw new Error("Record not in mock set");
  }
  const r = await fetch(`${API_BASE}/api/records/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`, { headers: ctxHeaders(c) });
  if (!r.ok) throw new Error(`Record ${kind}/${id} -> HTTP ${r.status}`);
  return r.json();
}

/** Normalise rows to objects regardless of whether the backend sends arrays or dicts. */
export function rowsAsObjects(h: HandleData): Record<string, unknown>[] {
  const rows = h.rows as unknown[];
  return rows.map((r) => (Array.isArray(r) ? Object.fromEntries(h.columns.map((c, i) => [c, r[i]])) : (r as Record<string, unknown>)));
}
