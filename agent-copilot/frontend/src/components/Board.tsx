"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import type { AnswerSpec, HandleData } from "@/contracts";
import { fetchHandle, rowsAsObjects } from "@/lib/api";
import { close, clear, setBoardWidth, setCanvasOpen, toFront, update, useBoard, useCanvasOpen, type BoardCard } from "@/lib/board";
import { SwitchableVisual, ViewSwitcher, viewsFor } from "@/components/visuals/Interactive";
import { titleCase } from "@/lib/format";

const MIN_W = 280, MIN_H = 200;

/** Drag by header / resize by corner: pointer capture, positions committed to the board store on release. */
function useDrag(card: BoardCard, mode: "move" | "resize") {
  const [live, setLive] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const start = useRef<{ px: number; py: number } | null>(null);
  const onPointerDown = (e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest("button,select,input")) return;
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    start.current = { px: e.clientX, py: e.clientY };
    setLive({ x: card.x, y: card.y, w: card.w, h: card.h });
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!start.current) return;
    const dx = e.clientX - start.current.px, dy = e.clientY - start.current.py;
    setLive(mode === "move" ? { x: Math.max(0, card.x + dx), y: Math.max(0, card.y + dy), w: card.w, h: card.h }
      : { x: card.x, y: card.y, w: Math.max(MIN_W, card.w + dx), h: Math.max(MIN_H, card.h + dy) });
  };
  const onPointerUp = () => { if (live) update(card.id, live); start.current = null; setLive(null); };
  return { live, handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp } };
}

function Card({ card }: { card: BoardCard }) {
  const [data, setData] = useState<HandleData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const move = useDrag(card, "move"), size = useDrag(card, "resize");
  const { visual } = card;
  useEffect(() => {
    if (!visual.handle) return;
    let ok = true;
    fetchHandle(visual.handle, card.chatId).then((d) => ok && setData(d)).catch((e: Error) => ok && setErr(e.message));
    return () => { ok = false; };
  }, [visual.handle, card.chatId]);
  const rows = useMemo(() => (data ? rowsAsObjects(data) : []), [data]);
  const views = viewsFor(visual, data);
  const view = card.view && views.includes(card.view) ? card.view : views[0];
  const box = move.live ?? size.live ?? card;
  const answer: AnswerSpec = { markdown: "", conflicts: card.conflicts ?? [] };
  const needsData = visual.type !== "conflict_card";
  return (
    <section aria-label={visual.title ?? titleCase(visual.type)} onPointerDown={() => toFront(card.id)}
      className="board-card absolute flex flex-col overflow-hidden rounded-xl border border-line bg-panel/95 shadow-xl backdrop-blur"
      style={{ left: box.x, top: box.y, width: box.w, height: box.h, zIndex: (card.z ?? 0) + (move.live || size.live ? 1000 : 0) }}>
      <header {...move.handlers} className="flex cursor-grab items-center gap-2 border-b border-line px-3 py-1.5 active:cursor-grabbing" style={{ touchAction: "none" }}>
        <span className="min-w-0 flex-1 truncate text-sm font-semibold">{visual.title ?? titleCase(visual.type)}</span>
        <ViewSwitcher views={views} view={view} onView={(v) => update(card.id, { view: v })} />
        <span className="font-mono text-[10px] text-muted">{visual.handle ?? ""}</span>
        <button type="button" aria-label="Close card" onClick={() => close(card.id)} className="rounded px-1.5 text-muted hover:bg-bad/15 hover:text-bad">✕</button>
      </header>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        {needsData && !data && !err && <div className="h-full animate-pulse rounded-lg bg-raised" role="status" aria-label="Loading data" />}
        {err && <p role="alert" className="text-sm text-bad">Could not load {visual.handle}: {err}</p>}
        {(data || !needsData) && <SwitchableVisual visual={visual} data={data} rows={rows} answer={answer} view={view} />}
      </div>
      <div {...size.handlers} aria-hidden className="absolute bottom-0 right-0 h-4 w-4 cursor-se-resize" style={{ touchAction: "none",
        background: "linear-gradient(135deg, transparent 50%, rgb(var(--muted) / .6) 50%)" }} />
    </section>
  );
}

/** The playboard: a dot-grid canvas of visuals that each answer adds to, arranged by the user. */
export function Board() {
  const cards = useBoard();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const ro = new ResizeObserver(() => setBoardWidth(el.clientWidth)); ro.observe(el); setBoardWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  const extent = cards.reduce((m, c) => ({ w: Math.max(m.w, c.x + c.w + 40), h: Math.max(m.h, c.y + c.h + 40) }), { w: 0, h: 0 });
  return (
    <div ref={ref} className="board-grid relative h-full overflow-auto" data-testid="board">
      <div className="pointer-events-none sticky left-0 top-0 z-20 flex items-center gap-2 px-4 py-2">
        <span className="rounded-md bg-panel/90 px-2 py-1 text-xs font-semibold shadow">Canvas · {cards.length} card{cards.length === 1 ? "" : "s"}</span>
        {cards.length > 0 && <button type="button" onClick={clear} className="pointer-events-auto rounded-md bg-panel/90 px-2 py-1 text-xs text-muted shadow hover:text-bad">Clear</button>}
        <button type="button" onClick={() => setCanvasOpen(false)} data-testid="canvas-close" className="pointer-events-auto ml-auto rounded-md bg-panel/90 px-2.5 py-1 text-xs font-medium shadow hover:text-accent">Close · Esc</button>
      </div>
      {cards.length === 0 && <p className="absolute inset-x-0 top-1/3 text-center text-sm text-muted">Expand a chart from an answer to place it here; drag, resize and switch chart types.</p>}
      <div className="relative" style={{ width: extent.w, height: extent.h, marginTop: -40 }}>
        {cards.map((c) => <Card key={c.id} card={c} />)}
      </div>
    </div>
  );
}

/** The canvas as a fullscreen modal, opened from a chart's expand button. */
export function CanvasModal() {
  const open = useCanvasOpen();
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && setCanvasOpen(false);
    window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h);
  }, [open]);
  if (!open) return null;
  return (
    <div className="canvas-modal fixed inset-0 z-40 bg-bg" role="dialog" aria-modal="true" aria-label="Canvas">
      <Board />
    </div>
  );
}
