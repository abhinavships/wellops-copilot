"use client";
import { useState } from "react";
import type { AnswerSpec, Visual } from "@/contracts";
import { UiContext, useUi } from "@/components/ui";
import { SwitchableVisual, ViewSwitcher, viewsFor } from "@/components/visuals/Interactive";
import { useHandle } from "@/components/VisualFrame";
import { clear, close, pin, setCanvasOpen, sourceKey, update, useBoard, type BoardCard } from "@/lib/board";
import { titleCase } from "@/lib/format";

export const DRAG_MIME = "application/x-wellops-visual";
export type DragPayload = { chatId: string; visual: Visual; conflicts?: AnswerSpec["conflicts"] };

/** Start a drag of a generated figure toward the compare dock. */
export function dragStart(e: React.DragEvent, p: DragPayload) {
  e.dataTransfer.setData(DRAG_MIME, JSON.stringify(p));
  e.dataTransfer.effectAllowed = "copy";
}

export function addToCompare(p: DragPayload) {
  pin(p.chatId, sourceKey(p.visual), p.visual, { markdown: "", conflicts: p.conflicts ?? [] });
}

function DockCard({ card, index }: { card: BoardCard; index: number }) {
  const ui = useUi();
  const { data, err, rows } = useHandle(card.visual.handle, card.chatId);
  const views = viewsFor(card.visual, data);
  const view = card.view && views.includes(card.view) ? card.view : views[0];
  const answer: AnswerSpec = { markdown: "", conflicts: card.conflicts ?? [] };
  return (
    // handles resolve in the chat the figure came from, so cards from different chats compare side by side
    <UiContext.Provider value={{ ...ui, ctx: { ...ui.ctx, chatId: card.chatId } }}>
      <section className="board-card rounded-2xl border border-line bg-panel/95 px-4 pb-4 pt-2.5 shadow-[0_14px_30px_-24px_rgb(0_0_0/.45)]">
        <header className="flex items-center gap-2 border-b border-fg/80 pb-1.5">
          <span className="label shrink-0 text-fg">C{index}</span>
          <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{card.visual.title ?? titleCase(card.visual.type)}</span>
          <ViewSwitcher views={views} view={view} onView={(v) => update(card.id, { view: v })} />
          <button type="button" aria-label="Remove from compare" onClick={() => close(card.id)}
            className="grid h-6 w-6 place-items-center rounded-full text-muted hover:bg-bad/15 hover:text-bad">✕</button>
        </header>
        <div className="pt-3">
          {err && <p className="text-sm text-bad" role="alert">Could not load: {err}</p>}
          {card.visual.handle && !data && !err && <div className="h-32 animate-pulse rounded-xl bg-raised" />}
          {(data || !card.visual.handle) && <SwitchableVisual visual={card.visual} data={data} rows={rows} answer={answer} view={view} />}
        </div>
      </section>
    </UiContext.Provider>
  );
}

/** Right-hand compare dock: drop figures from the chat here to see several side by side. */
export function CompareDock({ open, onClose }: { open: boolean; onClose: () => void }) {
  const cards = useBoard();
  const [over, setOver] = useState(false);
  const [wide, setWide] = useState(false);
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setOver(false);
    const raw = e.dataTransfer.getData(DRAG_MIME);
    if (raw) try { addToCompare(JSON.parse(raw) as DragPayload); } catch { /* not ours */ }
  };
  if (!open) return null;
  return (
    <aside aria-label="Compare" data-testid="compare-dock"
      onDragOver={(e) => { if (e.dataTransfer.types.includes(DRAG_MIME)) { e.preventDefault(); setOver(true); } }}
      onDragLeave={() => setOver(false)} onDrop={onDrop}
      className={`hidden shrink-0 flex-col border-l border-line bg-bg/70 backdrop-blur lg:flex ${wide ? "w-[min(52vw,860px)]" : "w-[440px]"} transition-[width]`}>
      <div className="flex items-center gap-2 border-b border-line/70 px-4 py-3">
        <span className="label flex-1"><span className="text-fg">Compare</span> · {cards.length} figure{cards.length === 1 ? "" : "s"}</span>
        <button type="button" onClick={() => setWide((w) => !w)} className="rounded-full px-2.5 py-1 text-xs text-muted hover:bg-raised hover:text-fg">{wide ? "Narrow" : "Side by side"}</button>
        {cards.length > 0 && <button type="button" onClick={() => setCanvasOpen(true)} className="rounded-full px-2.5 py-1 text-xs text-muted hover:bg-raised hover:text-fg">Canvas ⤢</button>}
        {cards.length > 0 && <button type="button" onClick={clear} className="rounded-full px-2.5 py-1 text-xs text-muted hover:bg-raised hover:text-bad">Clear</button>}
        <button type="button" onClick={onClose} aria-label="Close compare" className="grid h-7 w-7 place-items-center rounded-full text-muted hover:bg-raised hover:text-fg">✕</button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {cards.length === 0 ? (
          <div className={`grid h-full min-h-[300px] place-items-center rounded-2xl border-2 border-dashed text-center transition ${over ? "border-fg bg-hl/25" : "border-line"}`}>
            <div className="px-6">
              <div className="display text-2xl font-semibold">Drop figures <span className="hl">here</span></div>
              <p className="mt-2 text-sm text-muted">Drag any chart or card from the chat by its ⠿ handle, or press “+ Compare”. Mix answers from different chats to compare them side by side.</p>
            </div>
          </div>
        ) : (
          <div className={`grid gap-4 ${wide ? "grid-cols-2" : "grid-cols-1"} ${over ? "rounded-2xl outline-dashed outline-2 outline-fg/40" : ""}`}>
            {cards.map((c, i) => <DockCard key={c.id} card={c} index={i + 1} />)}
            <div className={`grid min-h-[90px] place-items-center rounded-2xl border-2 border-dashed text-xs text-muted ${over ? "border-fg bg-hl/25" : "border-line"}`}>drop another figure</div>
          </div>
        )}
      </div>
    </aside>
  );
}
