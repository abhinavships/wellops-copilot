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

/** An inline generated-UI element in an answer: title, chart-type switcher, expand to the canvas. */
export function VisualFrame({ visual, answer }: { visual: Visual; answer: AnswerSpec }) {
  const { ctx } = useUi();
  const { data, err, rows } = useHandle(visual.handle, ctx.chatId);
  const key = viewKey(ctx.chatId, visual);
  const views = viewsFor(visual, data);
  const view = resolveView(views, useViewOverride(key));
  if (visual.type === "none") return null;
  const title = visual.title ?? titleCase(visual.type);
  const chrome = !["kpi", "callout"].includes(visual.type);
  return (
    <figure className={`gen-ui m-0 ${chrome ? "rounded-2xl border border-line/70 bg-panel shadow-[0_1px_2px_rgb(0_0_0/.04),0_8px_24px_-12px_rgb(0_0_0/.12)]" : ""}`} aria-label={title} data-testid="visual">
      {chrome && (
        <figcaption className="flex items-center gap-2 px-4 pb-1 pt-3">
          <span className="min-w-0 flex-1 truncate text-sm font-medium">{title}</span>
          <ViewSwitcher views={views} view={view} onView={(v) => setViewOverride(key, v)} />
          <button type="button" onClick={() => expand(ctx.chatId, visual, answer, view)} aria-label="Expand to canvas" title="Open on the canvas"
            className="grid h-7 w-7 place-items-center rounded-lg text-muted transition hover:bg-raised hover:text-fg">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5 9 7M2.5 13.5 7 9" /></svg>
          </button>
        </figcaption>
      )}
      <div className={chrome ? "px-4 pb-4 pt-2" : ""}>
        {err && <p className="text-sm text-bad" role="alert">Could not load data: {err}</p>}
        {visual.handle && !data && !err && <div className="h-40 animate-pulse rounded-xl bg-raised" role="status" aria-label="Loading data" />}
        {(data || !visual.handle) && <SwitchableVisual visual={visual} data={data} rows={rows} answer={answer} view={view} />}
      </div>
    </figure>
  );
}
