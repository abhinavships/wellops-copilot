"use client";
import { useState } from "react";
import type { VisualProps } from ".";
import { Badge, CitationChip, useUi } from "@/components/ui";
import { undoAssertion } from "@/lib/api";
import { titleCase } from "@/lib/format";

const fv = (v: unknown) => (typeof v === "number" ? v.toFixed(1) : v == null ? "–" : String(v));

export function CorrectionDiff({ visual, data, rows }: VisualProps) {
  const { ctx } = useUi();
  const e = visual.encodings ?? {};
  const g = (k: string, fb: string) => (typeof e[k] === "string" ? (e[k] as string) : fb);
  const [state, setState] = useState<Record<string, "idle" | "busy" | "undone" | "error">>({});
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="space-y-3">
      {rows.map((r, i) => {
        const aid = String(r[g("assertion_id_col", "assertion_id")] ?? e.assertion_id ?? data?.applied_overlays?.[0] ?? "");
        const st = state[aid] ?? "idle";
        return (
          <div key={i}>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
              <span className="font-mono font-semibold">{String(r.inspection_id ?? r.asset_id ?? "")}</span>
              <span className="text-muted">{titleCase(String(r[g("attribute", "attribute")] ?? ""))}</span>
              {st === "undone" && <Badge tone="neutral">Undone</Badge>}
              {st !== "undone" && <Badge tone="info">Corrected by {String(r[g("author", "author")] ?? "unknown")}</Badge>}
            </div>
            <div className="mt-2 flex items-center gap-3">
              <div className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-bad">Before</div>
                <div className={`text-2xl font-semibold tabular-nums ${st === "undone" ? "" : "line-through decoration-bad/60"}`}>{fv(r[g("before", "original")])}</div>
              </div>
              <span aria-hidden className="text-xl text-muted">→</span>
              <div className={`rounded-lg border px-3 py-2 ${st === "undone" ? "border-line bg-raised opacity-60" : "border-ok/30 bg-ok/10"}`}>
                <div className="text-[10px] font-semibold uppercase tracking-wide text-ok">After</div>
                <div className="text-2xl font-semibold tabular-nums">{fv(r[g("after", "corrected")])}</div>
              </div>
            </div>
            {typeof r.reason === "string" && <p className="mt-2 text-xs text-muted">Reason: {r.reason}</p>}
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {aid && <CitationChip c={{ kind: "assertion", id: aid }} />}
              <button type="button" disabled={!aid || st === "busy" || st === "undone"}
                onClick={async () => {
                  setState((s) => ({ ...s, [aid]: "busy" })); setErr(null);
                  try { await undoAssertion(aid, ctx); setState((s) => ({ ...s, [aid]: "undone" })); }
                  catch (x) { setState((s) => ({ ...s, [aid]: "error" })); setErr((x as Error).message); }
                }}
                className="rounded-md border border-line bg-panel px-3 py-1 text-xs font-semibold hover:border-bad hover:text-bad disabled:cursor-not-allowed disabled:opacity-50">
                {st === "busy" ? "Undoing…" : st === "undone" ? "Undone" : "Undo correction"}
              </button>
              {err && <span role="alert" className="text-xs text-bad">{err}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
