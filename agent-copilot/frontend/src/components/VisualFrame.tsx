"use client";
import { useEffect, useMemo, useState } from "react";
import type { AnswerSpec, HandleData, Visual } from "@/contracts";
import { fetchHandle, rowsAsObjects } from "@/lib/api";
import { Badge, Card, CitationChip, useUi } from "@/components/ui";
import { VISUALS, type VisualProps } from "@/components/visuals";
import { titleCase } from "@/lib/format";

export function VisualFrame({ visual, answer }: { visual: Visual; answer: AnswerSpec }) {
  const { ctx } = useUi();
  const [data, setData] = useState<HandleData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showSql, setShowSql] = useState(false);

  useEffect(() => {
    if (!visual.handle) return;
    let live = true;
    fetchHandle(visual.handle, ctx.chatId).then((d) => live && setData(d)).catch((e: Error) => live && setErr(e.message));
    return () => { live = false; };
  }, [visual.handle, ctx.chatId]);

  const Comp = VISUALS[visual.type] as React.ComponentType<VisualProps> | undefined;
  const rows = useMemo(() => (data ? rowsAsObjects(data) : []), [data]);
  if (visual.type === "none") return null;
  const needsData = visual.type !== "conflict_card";
  const title = visual.title ?? titleCase(visual.type);

  return (
    <Card className="overflow-hidden" >
      <figure className="m-0" aria-label={title}>
        <figcaption className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
          <span className="text-sm font-semibold">{title}</span>
          <span className="flex items-center gap-1.5">
            {visual.requested_by_user && <Badge tone="info" title="You asked for this visual">as requested</Badge>}
            <span className="font-mono text-[10px] uppercase tracking-wide text-muted">{visual.type.replace("_", " ")}{visual.handle ? ` · ${visual.handle}` : ""}</span>
          </span>
        </figcaption>
        <div className="p-3">
          {needsData && !data && !err && <div className="h-32 animate-pulse rounded-lg bg-raised" role="status" aria-label="Loading data" />}
          {err && <p className="text-sm text-bad" role="alert">Could not load data for {visual.handle}: {err}</p>}
          {(data || !needsData) && (Comp
            ? <Comp visual={visual} data={data} rows={rows} answer={answer} />
            : <p className="text-sm text-muted">No renderer for visual type “{visual.type}”.</p>)}
        </div>
        {data && (
          <div className="flex flex-wrap items-center gap-1.5 border-t border-line px-3 py-2">
            {(data.dq_flags ?? []).map((f, i) => <Badge key={i} tone="warn" title={`${f.entity_id}: ${f.kind}`} icon={<span aria-hidden>⚠</span>}>{f.detail}{(data.dq_flags ?? []).length > 1 || rows.length > 1 ? ` (${f.entity_id})` : ""}</Badge>)}
            {(data.notes ?? []).filter((n) => !(answer.assumptions ?? []).some((a) => a.toLowerCase() === n.toLowerCase())).map((n, i) => <Badge key={`n${i}`} tone={/^corrected/i.test(n) ? "info" : "neutral"}>{n}</Badge>)}
            {(data.applied_overlays ?? []).map((o) => <Badge key={o} tone="info" title="Assertion overlay applied to these rows">overlay {o}</Badge>)}
            {(data.citations ?? []).map((c) => <CitationChip key={`${c.kind}${c.id}${c.span?.join("-")}`} c={c} />)}
            {data.sql && <button type="button" onClick={() => setShowSql((s) => !s)} className="ml-auto text-[11px] text-muted underline decoration-dotted hover:text-accent" aria-expanded={showSql}>{showSql ? "hide query" : "show query"}</button>}
          </div>
        )}
        {showSql && data?.sql && <pre className="overflow-x-auto border-t border-line bg-raised px-3 py-2 font-mono text-[11px] text-muted">{data.sql}</pre>}
      </figure>
    </Card>
  );
}
