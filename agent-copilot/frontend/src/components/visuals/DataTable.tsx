"use client";
import { useMemo, useState } from "react";
import type { VisualProps } from ".";
import { Badge, stateTone } from "@/components/ui";
import { asStrArr, fmtVal, num, titleCase } from "@/lib/format";

const isStatusCol = (c: string) => /status|state|result|severity/.test(c);
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
  const sorted = useMemo(() => {
    if (!sort) return rows;
    return [...rows].sort((a, b) => {
      const x = a[sort.col], y = b[sort.col]; const nx = num(x), ny = num(y);
      return (nx !== null && ny !== null ? nx - ny : String(x).localeCompare(String(y))) * sort.dir;
    });
  }, [rows, sort]);

  return (
    <div className="-mx-3 overflow-x-auto px-3">
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} scope="col" aria-sort={sort?.col === c ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                className="whitespace-nowrap border-b border-line px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
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
  );
}
