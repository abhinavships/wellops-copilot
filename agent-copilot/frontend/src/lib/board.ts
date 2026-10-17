"use client";
import { useSyncExternalStore } from "react";
import type { AnswerSpec, Visual } from "@/contracts";

/** A visual placed on the playboard. Client state only (persisted to localStorage). */
export interface BoardCard {
  id: string; // `${chatId}:${source}` so the same visual is never placed twice
  chatId: string; // handles resolve per chat
  visual: Visual;
  conflicts?: AnswerSpec["conflicts"]; // conflict_card renders from the answer, not a handle
  x: number; y: number; w: number; h: number;
  view?: string; // chart-type override chosen on the card
  z?: number; // stacking order (raised on touch; the array order never changes mid-drag)
  added: number;
}

const KEY = "wellops.board";
const W = 460, H = 340, GAP = 20;
let cards: BoardCard[] = [];
let loaded = false;
const subs = new Set<() => void>();

function load() {
  if (loaded || typeof window === "undefined") return;
  loaded = true;
  try { cards = JSON.parse(localStorage.getItem(KEY) ?? "[]") as BoardCard[]; } catch { cards = []; }
}
function save(next: BoardCard[]) {
  cards = next;
  try { localStorage.setItem(KEY, JSON.stringify(cards)); } catch { /* storage unavailable */ }
  subs.forEach((f) => f());
}

const overlaps = (a: { x: number; y: number; w: number; h: number }, b: BoardCard) =>
  a.x < b.x + b.w + GAP && b.x < a.x + a.w + GAP && a.y < b.y + b.h + GAP && b.y < a.y + a.h + GAP;

/** Next free slot scanning rows left to right, in a grid sized to the board width. */
function freeSpot(w: number, h: number, width: number): { x: number; y: number } {
  const cols = Math.max(1, Math.floor((width - GAP) / (W + GAP)));
  for (let row = 0; row < 200; row++)
    for (let col = 0; col < cols; col++) {
      const p = { x: GAP + col * (W + GAP), y: GAP + row * (H + GAP), w, h };
      if (!cards.some((c) => overlaps(p, c))) return p;
    }
  return { x: GAP, y: GAP };
}

let boardWidth = 1000;
export const setBoardWidth = (w: number) => { if (w > 0) boardWidth = w; };

export function pin(chatId: string, source: string, visual: Visual, answer?: AnswerSpec): boolean {
  load();
  const id = `${chatId}:${source}`;
  if (visual.type === "none" || cards.some((c) => c.id === id)) return false;
  const tall = visual.type === "table" || visual.type === "evidence_chain" || visual.type === "obligation_timeline";
  const w = W, h = tall ? H + 80 : H;
  const p = freeSpot(w, h, boardWidth);
  save([...cards, { id, chatId, visual, conflicts: visual.type === "conflict_card" ? answer?.conflicts : undefined, ...p, w, h, added: Date.now() }]);
  return true;
}
/** Stable per-chat key for a visual: handles are unique within a chat. */
export const sourceKey = (v: Visual) => `${v.type}:${v.handle ?? JSON.stringify(v.encodings ?? {})}`;
export const isPinned = (chatId: string, source: string) => { load(); return cards.some((c) => c.id === `${chatId}:${source}`); };
export const update = (id: string, patch: Partial<BoardCard>) => save(cards.map((c) => (c.id === id ? { ...c, ...patch } : c)));
export const close = (id: string) => save(cards.filter((c) => c.id !== id));
export const clear = () => save([]);
export const toFront = (id: string) => {
  const top = Math.max(0, ...cards.map((c) => c.z ?? 0));
  const c = cards.find((x) => x.id === id);
  if (c && cards.some((x) => x.id !== id && (x.z ?? 0) >= (c.z ?? 0))) update(id, { z: top + 1 });
};

const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
const EMPTY: BoardCard[] = [];
export function useBoard(): BoardCard[] {
  return useSyncExternalStore(subscribe, () => { load(); return cards; }, () => EMPTY);
}

// --- per-visual chart-type overrides (inline answers and the canvas share them) ------------------------------
const views = new Map<string, string>();
const viewSubs = new Set<() => void>();
let viewTick = 0;
export const viewKey = (chatId: string, v: Visual) => `${chatId}:${sourceKey(v)}`;
export function setViewOverride(key: string, view: string) { views.set(key, view); viewTick++; viewSubs.forEach((f) => f()); }
export function useViewOverride(key: string): string | undefined {
  useSyncExternalStore((f) => { viewSubs.add(f); return () => { viewSubs.delete(f); }; }, () => viewTick, () => 0);
  return views.get(key);
}

// --- fullscreen canvas modal (the board) ----------------------------------------------------------------------
let canvasOpen = false;
const canvasSubs = new Set<() => void>();
export function setCanvasOpen(v: boolean) { canvasOpen = v; canvasSubs.forEach((f) => f()); }
export const useCanvasOpen = () =>
  useSyncExternalStore((f) => { canvasSubs.add(f); return () => { canvasSubs.delete(f); }; }, () => canvasOpen, () => false);
/** Open a visual on the canvas: place it if new, raise it, show the modal. */
export function expand(chatId: string, v: Visual, answer?: AnswerSpec, view?: string) {
  pin(chatId, sourceKey(v), v, answer);
  if (view) update(`${chatId}:${sourceKey(v)}`, { view }); // open in the view chosen inline
  toFront(`${chatId}:${sourceKey(v)}`);
  setCanvasOpen(true);
}

/** "make it a bar chart", "as a line", "show as table", "turn into graph" -> a view name, else null. */
export function chartIntent(text: string): string | null {
  const t = text.toLowerCase().trim();
  if (t.length > 60 || !/\b(make|show|turn|switch|change|convert|display|render|view|as|into|to)\b/.test(t)) return null;
  const m = t.match(/\b(bar|line|table|heat ?map|graph|chart|plot)s?\b/);
  if (!m || /\b(which|what|how|why|when|who)\b/.test(t)) return null;
  return { bar: "bar", line: "line", table: "table", heatmap: "heatmap", "heat map": "heatmap" }[m[1]] ?? "chart";
}
