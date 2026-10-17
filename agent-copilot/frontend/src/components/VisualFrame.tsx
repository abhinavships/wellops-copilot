"use client";
import { useEffect, useMemo, useState } from "react";
import type { AnswerSpec, HandleData, Visual } from "@/contracts";
import { fetchHandle, rowsAsObjects } from "@/lib/api";
import { useUi } from "@/components/ui";
import { SwitchableVisual, ViewSwitcher, viewsFor } from "@/components/visuals/Interactive";
import { expand, setViewOverride, useViewOverride, viewKey } from "@/lib/board";
import { titleCase } from "@/lib/format";

/** Fetch a visual's handle (if any) for the current chat. */
export function useHandle(handle: string | null | undefined, chatId: string) {
  const [data, setData] = useState<HandleData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!handle) return;
    let live = true;
    fetchHandle(handle, chatId).then((d) => live && setData(d)).catch((e: Error) => live && setErr(e.message));
    return () => { live = false; };
  }, [handle, chatId]);
  return { data, err, rows: useMemo(() => (data ? rowsAsObjects(data) : []), [data]) };
}

/** Pick the view: an override from the switcher or chat ("chart" = the first non-table view), else the default. */
export function resolveView(views: string[], override?: string): string {
  if (override === "chart") return views.find((v) => v !== "table") ?? views[0];
  return override && views.includes(override) ? override : views[0];
}

/** The visual itself, no chrome (used inside tabs and the canvas). */
export function VisualBody({ visual, answer }: { visual: Visual; answer: AnswerSpec }) {
  const { ctx } = useUi();
  const { data, err, rows } = useHandle(visual.handle, ctx.chatId);
  const view = resolveView(viewsFor(visual, data), useViewOverride(viewKey(ctx.chatId, visual)));
  if (err) return <p className="text-sm text-bad" role="alert">Could not load data: {err}</p>;
  if (visual.handle && !data) return <div className="h-40 animate-pulse rounded-xl bg-raised" role="status" aria-label="Loading data" />;
  return <SwitchableVisual visual={visual} data={data} rows={rows} answer={answer} view={view} />;
}

/** An inline generated-UI element, styled as a figure in a technical paper: mono caption, ink rules, no card chrome. */
export function VisualFrame({ visual, answer, index = 1 }: { visual: Visual; answer: AnswerSpec; index?: number }) {
  const { ctx } = useUi();
  const { data, err, rows } = useHandle(visual.handle, ctx.chatId);
  const key = viewKey(ctx.chatId, visual);
  const views = viewsFor(visual, data);
  const view = resolveView(views, useViewOverride(key));
  if (visual.type === "none") return null;
  const title = visual.title ?? titleCase(visual.type);
  return (
    <figure className="gen-ui m-0 rounded-2xl border border-line bg-panel/90 px-5 pb-5 pt-3 shadow-[0_1px_0_rgb(var(--line)),0_18px_40px_-28px_rgb(0_0_0/.35)]" aria-label={title} data-testid="visual">
      <figcaption className="flex items-center gap-3 border-b border-fg/80 pb-2">
        <span className="label shrink-0 text-fg">Fig. {index}</span>
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{title}</span>
        <ViewSwitcher views={views} view={view} onView={(v) => setViewOverride(key, v)} />
        <button type="button" onClick={() => expand(ctx.chatId, visual, answer, view)} aria-label="Expand to canvas" title="Open on the canvas"
          className="grid h-7 w-7 place-items-center rounded-full text-muted transition hover:bg-raised hover:text-fg">
          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5 9 7M2.5 13.5 7 9" /></svg>
        </button>
      </figcaption>
      <div className="pt-4">
        {err && <p className="text-sm text-bad" role="alert">Could not load data: {err}</p>}
        {visual.handle && !data && !err && <div className="h-40 animate-pulse rounded-xl bg-raised" role="status" aria-label="Loading data" />}
        {(data || !visual.handle) && <SwitchableVisual visual={visual} data={data} rows={rows} answer={answer} view={view} />}
      </div>
    </figure>
  );
}
