"use client";
import { useEffect, useRef, useState } from "react";
import { useUi } from "@/components/ui";
import { mergeBoard, replaceBoard, snapshotBoard, useBoard } from "@/lib/board";
import {
  defaultCanvasName, deleteCanvas, getCanvas, listCanvases, saveCanvas, setCurrentCanvas, showToast, useCurrentCanvas,
  useToast, type CanvasSummary,
} from "@/lib/canvases";

const PRIMARY = "rounded-full bg-fg px-3 py-1 text-xs font-medium text-bg transition hover:opacity-85 disabled:opacity-30";
const SECONDARY = "rounded-full border border-line bg-panel px-3 py-1 text-xs transition hover:border-fg disabled:opacity-40";
const figures = (n: number) => `${n} figure${n === 1 ? "" : "s"}`;
const when = (s: string) => {
  const d = new Date(s.includes("T") || s.endsWith("Z") ? s : `${s.replace(" ", "T")}Z`);
  return isNaN(d.getTime()) ? s : d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
};

/** Inline name field: Enter saves, Esc cancels (and does not close the canvas modal). */
function NameField({ initial, action, onSubmit, onCancel }: {
  initial: string; action: string; onSubmit: (name: string) => void; onCancel: () => void;
}) {
  const [v, setV] = useState(initial);
  return (
    <form className="flex items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); onSubmit(v.trim() || initial); }}>
      <input autoFocus value={v} onChange={(e) => setV(e.target.value)} onFocus={(e) => e.currentTarget.select()}
        onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onCancel(); } }} aria-label="Canvas name"
        className="w-48 rounded-full border border-fg bg-panel px-3 py-1 text-xs outline-none" />
      <button type="submit" className={PRIMARY}>{action}</button>
      <button type="button" onClick={onCancel} aria-label="Cancel" className="grid h-6 w-6 place-items-center rounded-full text-muted hover:bg-raised hover:text-fg">✕</button>
    </form>
  );
}

function SavedMenu({ onClose }: { onClose: () => void }) {
  const { ctx } = useUi();
  const current = useCurrentCanvas();
  const [items, setItems] = useState<CanvasSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const refresh = () => listCanvases(ctx).then(setItems).catch((e: Error) => setErr(e.message));
  useEffect(() => { let ok = true; listCanvases(ctx).then((x) => ok && setItems(x)).catch((e: Error) => ok && setErr(e.message)); return () => { ok = false; }; }, [ctx]);

  const open = async (c: CanvasSummary, alongside: boolean) => {
    try {
      const s = await getCanvas(c.canvas_id, ctx);
      if (alongside) {
        const n = mergeBoard(s.cards);
        showToast(n ? `Added · ${figures(n)} from “${s.name}”` : `Already on the board · ${s.name}`);
      }
      else { replaceBoard(s.cards); setCurrentCanvas({ id: s.canvas_id, name: s.name }); showToast(`Opened · ${s.name}`); }
      onClose();
    } catch (e) { setErr((e as Error).message); }
  };
  const rename = async (c: CanvasSummary, name: string) => {
    setRenaming(null);
    try {
      await saveCanvas({ canvas_id: c.canvas_id, name }, ctx);
      if (current?.id === c.canvas_id) setCurrentCanvas({ id: c.canvas_id, name });
      await refresh();
    } catch (e) { setErr((e as Error).message); }
  };
  const remove = async (c: CanvasSummary) => {
    setConfirm(null);
    try {
      await deleteCanvas(c.canvas_id, ctx);
      if (current?.id === c.canvas_id) setCurrentCanvas(null);
      showToast(`Deleted · ${c.name}`);
      await refresh();
    } catch (e) { setErr((e as Error).message); }
  };

  return (
    <div role="menu" aria-label="Saved canvases" data-testid="saved-canvases"
      className="absolute right-0 top-full z-50 mt-2 w-[340px] rounded-2xl border border-line bg-panel p-2 text-left text-fg shadow-[0_18px_40px_-20px_rgb(0_0_0/.45)]">
      <div className="label px-2 pb-1.5 pt-1">Saved canvases</div>
      {err && <p role="alert" className="px-2 pb-2 text-xs text-bad">{err}</p>}
      {!items && !err && <div className="mx-2 mb-2 h-10 animate-pulse rounded-xl bg-raised" />}
      {items?.length === 0 && <p className="px-2 pb-2 text-xs text-muted">Nothing saved yet. Pin a few figures and press <span className="hl">Save</span>.</p>}
      <ul className="max-h-[50vh] overflow-y-auto">
        {items?.map((c) => (
          <li key={c.canvas_id} className="group rounded-xl px-2 py-1.5 hover:bg-raised/70">
            {renaming === c.canvas_id ? (
              <NameField initial={c.name} action="Rename" onSubmit={(n) => rename(c, n)} onCancel={() => setRenaming(null)} />
            ) : (
              <>
                <button type="button" role="menuitem" onClick={() => open(c, false)} className="block w-full min-w-0 text-left">
                  <span className="block truncate text-[13px] font-medium">
                    {current?.id === c.canvas_id ? <span className="hl">{c.name}</span> : c.name}
                  </span>
                  <span className="label !text-[10px]">{figures(c.card_count)} · {when(c.updated_at)}</span>
                </button>
                <div className="mt-1 flex flex-wrap gap-1">
                  <button type="button" onClick={() => open(c, false)} className={PRIMARY}>Open</button>
                  <button type="button" onClick={() => open(c, true)} className={SECONDARY} title="Add its figures to the current board">Open alongside</button>
                  <button type="button" onClick={() => setRenaming(c.canvas_id)} className="rounded-full px-2 py-1 text-xs text-muted hover:text-fg">Rename</button>
                  <button type="button" onClick={() => (confirm === c.canvas_id ? remove(c) : setConfirm(c.canvas_id))}
                    className={`rounded-full px-2 py-1 text-xs ${confirm === c.canvas_id ? "bg-bad/15 text-bad" : "text-muted hover:text-bad"}`}>
                    {confirm === c.canvas_id ? "Delete?" : "Delete"}
                  </button>
                </div>
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Save / Saved menu / (canvas only) Export PNG. Used in the compare dock header and the canvas modal toolbar. */
export function CanvasMenu({ exportTarget, bar }: { exportTarget?: React.RefObject<HTMLElement | null>; bar?: boolean }) {
  const { ctx } = useUi();
  const cards = useBoard();
  const current = useCurrentCanvas();
  const [naming, setNaming] = useState(false);
  const [menu, setMenu] = useState(false);
  const [busy, setBusy] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const h = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setMenu(false); };
    document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
  }, [menu]);

  const save = async (name: string) => {
    setNaming(false); setBusy(true);
    try {
      const now = snapshotBoard();
      // same name as the open canvas = save over it; a new name = a new canvas
      const r = await saveCanvas({ name, cards: now, canvas_id: current && current.name === name ? current.id : undefined }, ctx);
      setCurrentCanvas({ id: r.canvas_id, name: r.name });
      showToast(`Saved · ${figures(r.card_count)}`);
    } catch (e) { showToast(`Save failed · ${(e as Error).message}`); } finally { setBusy(false); }
  };
  const exportPng = async () => {
    const el = exportTarget?.current; if (!el) return;
    setBusy(true);
    try {
      const { toPng } = await import("html-to-image");
      const url = await toPng(el, { pixelRatio: 2, backgroundColor: getComputedStyle(document.body).backgroundColor, cacheBust: true, style: { margin: "0" }, // the board pulls its content up under the toolbar
        filter: (n) => !(n instanceof HTMLElement && n.dataset.noExport !== undefined) });
      const a = document.createElement("a");
      a.href = url; a.download = `${(current?.name ?? defaultCanvasName()).replace(/[^\w\- ]+/g, "").trim() || "canvas"}.png`; a.click();
    } catch (e) { showToast(`Export failed · ${(e as Error).message}`); } finally { setBusy(false); }
  };

  return (
    <div ref={wrap} className={`pointer-events-auto relative flex items-center gap-1.5 ${bar ? "w-full" : ""}`}>
      {naming ? (
        <NameField initial={current?.name ?? defaultCanvasName()} action="Save" onSubmit={save} onCancel={() => setNaming(false)} />
      ) : (
        <>
          {bar ? (
            <span className="label min-w-0 flex-1 truncate" title={current ? `Open canvas: ${current.name}` : undefined}>
              {current ? <span className="text-fg">{current.name}</span> : "Unsaved board"}
            </span>
          ) : current && <span className="label max-w-[220px] truncate" title={`Open canvas: ${current.name}`}>{current.name}</span>}
          <button type="button" disabled={!cards.length || busy} onClick={() => { setMenu(false); setNaming(true); }} className={PRIMARY} data-testid="canvas-save">Save</button>
          <button type="button" aria-expanded={menu} aria-haspopup="menu" onClick={() => setMenu((m) => !m)} className={SECONDARY} data-testid="canvas-saved">Saved ▾</button>
          {exportTarget && cards.length > 0 && <button type="button" disabled={busy} onClick={exportPng} className={SECONDARY}>Export PNG</button>}
        </>
      )}
      {menu && <SavedMenu onClose={() => setMenu(false)} />}
    </div>
  );
}

/** "Saved · 4 figures": one toast for the whole app, bottom centre. */
export function CanvasToast() {
  const t = useToast();
  if (!t) return null;
  return (
    <div key={t.n} role="status" aria-live="polite" className="turn-in pointer-events-none fixed inset-x-0 bottom-6 z-[60] flex justify-center">
      <span className="rounded-full bg-fg px-4 py-2 font-mono text-[11px] uppercase tracking-[.14em] text-bg shadow-lg">{t.text}</span>
    </div>
  );
}
