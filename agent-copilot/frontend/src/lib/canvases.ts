"use client";
import { useSyncExternalStore } from "react";
import { API_BASE, MOCK, ctxHeaders, forgetHandles, type Ctx } from "@/lib/api";
import type { BoardCard } from "@/lib/board";

/** Saved canvases: named board snapshots stored server-side per user (/api/canvases). */
export interface CanvasSummary { canvas_id: string; name: string; card_count: number; updated_at: string }
export interface SavedCanvas { canvas_id: string; name: string; cards: BoardCard[]; created_at: string; updated_at: string }

const MOCK_KEY = "wellops.canvases.mock"; // NEXT_PUBLIC_MOCK=1 has no backend: keep canvases in localStorage
function mockAll(): SavedCanvas[] {
  try { return JSON.parse(localStorage.getItem(MOCK_KEY) ?? "[]") as SavedCanvas[]; } catch { return []; }
}
function mockPut(all: SavedCanvas[]) { try { localStorage.setItem(MOCK_KEY, JSON.stringify(all)); } catch { /* unavailable */ } }

async function call<T>(path: string, c: Ctx, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, { ...init, headers: ctxHeaders(c) });
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return (await r.json()) as T;
}

export async function listCanvases(c: Ctx): Promise<CanvasSummary[]> {
  if (MOCK) return mockAll().map(({ canvas_id, name, cards, updated_at }) => ({ canvas_id, name, card_count: cards.length, updated_at }));
  return (await call<{ canvases: CanvasSummary[] }>("/api/canvases", c)).canvases;
}

/** Load a canvas. Its figures come back rebound to the chat "canvas-<id>", whose handles the server re-registered. */
export async function getCanvas(id: string, c: Ctx): Promise<SavedCanvas> {
  if (MOCK) {
    const s = mockAll().find((x) => x.canvas_id === id);
    if (!s) throw new Error("Canvas not found");
    return s;
  }
  const s = await call<SavedCanvas>(`/api/canvases/${encodeURIComponent(id)}`, c);
  forgetHandles(`canvas-${id}`); // the canvas chat's handles were just re-registered; drop any stale cached copy
  return s;
}

/** Create (no id) or update a canvas. Omitting `cards` on an update renames it only. */
export async function saveCanvas(body: { name: string; cards?: BoardCard[]; canvas_id?: string }, c: Ctx):
  Promise<{ canvas_id: string; name: string; card_count: number }> {
  if (MOCK) {
    const all = mockAll(), now = new Date().toISOString();
    const prev = all.find((x) => x.canvas_id === body.canvas_id);
    const s: SavedCanvas = prev
      ? { ...prev, name: body.name, cards: body.cards ?? prev.cards, updated_at: now }
      : { canvas_id: body.canvas_id ?? Math.random().toString(36).slice(2, 14), name: body.name, cards: body.cards ?? [], created_at: now, updated_at: now };
    mockPut([s, ...all.filter((x) => x.canvas_id !== s.canvas_id)]);
    return { canvas_id: s.canvas_id, name: s.name, card_count: s.cards.length };
  }
  return call("/api/canvases", c, { method: "POST", body: JSON.stringify(body) });
}

export async function deleteCanvas(id: string, c: Ctx): Promise<void> {
  if (MOCK) { mockPut(mockAll().filter((x) => x.canvas_id !== id)); return; }
  await call(`/api/canvases/${encodeURIComponent(id)}`, c, { method: "DELETE" });
}

export const defaultCanvasName = () =>
  `Canvas — ${new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}`;

// --- which saved canvas the board currently shows (so Save updates it instead of making a copy) -------------
let current: { id: string; name: string } | null = null;
const subs = new Set<() => void>();
export function setCurrentCanvas(v: { id: string; name: string } | null) { current = v; subs.forEach((f) => f()); }
export const useCurrentCanvas = () =>
  useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => current, () => null);

// --- toast ("Saved · 4 figures") shared by the dock and the canvas modal --------------------------------------
let toast: { text: string; n: number } | null = null;
let toastTimer: ReturnType<typeof setTimeout> | undefined;
const toastSubs = new Set<() => void>();
export function showToast(text: string) {
  toast = { text, n: (toast?.n ?? 0) + 1 };
  toastSubs.forEach((f) => f());
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast = null; toastSubs.forEach((f) => f()); }, 2600);
}
export const useToast = () =>
  useSyncExternalStore((f) => { toastSubs.add(f); return () => { toastSubs.delete(f); }; }, () => toast, () => null);
