"use client";
import { useMemo, useState } from "react";
import type { VisualProps } from ".";
import { Badge, stateTone } from "@/components/ui";
import { asStrArr, fmtVal, num, titleCase } from "@/lib/format";

const isStatusCol = (c: string) => /status|state|result|severity|^check$|compliant/.test(c);
// internal plumbing never shown as a column unless the agent asked for it by name
const HIDDEN = new Set(["span", "same_text_count", "rank", "score", "evidence_ids", "threshold_source", "sql", "explanation",
  "lat", "lon", "obligation_id", "trigger_ref", "label", "notes_raw"]);
const empty = (v: unknown) => v === null || v === undefined || v === "" || v === "–";
const MAX_COLS = 8;

export function DataTable({ visual, data, rows }: VisualProps) {
  const cols = useMemo(() => {
    const e = asStrArr(visual.encodings?.columns);
    if (e.length) return e.filter((c) => rows.some((r) => c in r) || !data);
    return (data?.columns ?? []).filter((c) => !HIDDEN.has(c) && rows.some((r) => !empty(r[c]))).slice(0, MAX_COLS);
  }, [visual, data, rows]);
  const [sort, setSort] = useState<{ col: string; dir: 1 | -1 } | null>(
    typeof visual.encodings?.sort === "string" && cols.includes(visual.encodings.sort as string)
      ? { col: visual.encodings.sort as string, dir: -1 } : null);
  const flagged = new Set((data?.dq_flags ?? []).map((f) => f.entity_id));
  // a status-like column with a few values becomes filter chips (Met / Missed, Allowed / Expired ...)
  const facet = useMemo(() => {
    for (const c of cols) {
      if (!isStatusCol(c)) continue;
      const counts = new Map<string, number>();
      rows.forEach((r) => { const v = r[c]; if (typeof v === "string" && v) counts.set(v, (counts.get(v) ?? 0) + 1); });
      if (counts.size >= 2 && counts.size <= 6) return { col: c, counts: [...counts.entries()].sort((a, b) => b[1] - a[1]) };
    }
    return null;
  }, [cols, rows]);
  const [pick, setPick] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return rows.filter((r) => (!pick || !facet || r[facet.col] === pick)
      && (!needle || cols.some((c) => String(r[c] ?? "").toLowerCase().includes(needle))));
  }, [rows, pick, facet, q, cols]);
  const csv = () => {
    const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const body = [cols.map(esc).join(","), ...shown.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([body], { type: "text/csv" }));
    a.download = `${(visual.title ?? "table").replace(/[^\w-]+/g, "_")}.csv`;
    a.click();
  };
  const sorted = useMemo(() => {
    if (!sort) return shown;
    return [...shown].sort((a, b) => {
      const x = a[sort.col], y = b[sort.col]; const nx = num(x), ny = num(y);
      return (nx !== null && ny !== null ? nx - ny : String(x).localeCompare(String(y))) * sort.dir;
    });
  }, [shown, sort]);

  return (
    <div>
    {(facet || rows.length > 8) && (
      <div className="mb-2 flex flex-wrap items-center gap-1.5" onPointerDown={(e) => e.stopPropagation()}>
        {facet && (
          <>
            <button type="button" onClick={() => setPick(null)} aria-pressed={!pick}
              className={`rounded-full border px-2.5 py-0.5 text-xs transition ${!pick ? "border-fg bg-fg text-bg" : "border-line hover:border-fg"}`}>
              All <span className="tabular-nums opacity-70">{rows.length}</span></button>
            {facet.counts.map(([v, n]) => {
              const tone = stateTone(v);
              const on = pick === v;
              const ring = tone === "bad" ? "border-bad/50 text-bad" : tone === "ok" ? "border-ok/50 text-ok" : tone === "warn" ? "border-warn/50 text-warn" : "border-line";
              return (
                <button key={v} type="button" onClick={() => setPick(on ? null : v)} aria-pressed={on}
                  className={`rounded-full border px-2.5 py-0.5 text-xs transition ${on ? "bg-fg text-bg border-fg" : `${ring} hover:bg-raised`}`}>
                  {v} <span className="tabular-nums opacity-70">{n}</span></button>
              );
            })}
          </>
        )}
        {rows.length > 8 && (
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter rows…" aria-label="Filter rows"
            className="ml-auto w-36 rounded-md border border-line bg-bg px-2 py-0.5 text-xs outline-none focus:border-fg" />
        )}
        <button type="button" onClick={csv} title="Download these rows as CSV"
          className={`${rows.length > 8 ? "" : "ml-auto "}rounded-md border border-line px-2 py-0.5 text-xs hover:border-fg`}>CSV ↓</button>
      </div>
    )}
    <div className="-mx-3 max-h-[22rem] overflow-auto px-3">
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} scope="col" aria-sort={sort?.col === c ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                className="sticky top-0 z-[1] whitespace-nowrap border-b border-line bg-bg px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
                <button type="button" onClick={() => setSort((s) => (s?.col === c ? { col: c, dir: (s.dir * -1) as 1 | -1 } : { col: c, dir: 1 }))} className="hover:text-fg">
                  {titleCase(c)}{sort?.col === c ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => (
            <tr key={i} className="border-b border-line/60 last:border-0 align-top">
              {cols.map((c, ci) => {
                const v = r[c];
                const id = String(r[cols[0]] ?? "");
                return (
                  <td key={c} className={`px-2 py-2 ${typeof v === "string" && v.length > 40 ? "min-w-[16rem] max-w-[28rem]" : "whitespace-nowrap"}`}>
                    {isStatusCol(c) && typeof v === "string" ? <Badge tone={stateTone(v)}>{v}</Badge>
                      : ci === 0 ? (
                        <span className="inline-flex flex-wrap items-center gap-1.5 font-mono text-[13px] font-medium">
                          {fmtVal(v)}
                          {flagged.has(id) && <span title="Data-quality flag: recorded status out of date" className="rounded bg-warn/15 px-1 text-[10px] font-sans font-semibold text-warn">⚠ DQ</span>}
                        </span>
                      ) : typeof v === "string" && v.length > 40
                        ? <span className="line-clamp-2" title={v}>{v}</span>
                        : <span className={typeof v === "number" ? "tabular-nums" : ""}>{fmtVal(v)}</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    {rows.length > 8 && <p className="mt-1.5 text-right font-mono text-[11px] text-muted">{shown.length === rows.length ? `${rows.length} rows` : `${shown.length} of ${rows.length} rows`} · scroll or expand ↗</p>}
    </div>
  );
}
