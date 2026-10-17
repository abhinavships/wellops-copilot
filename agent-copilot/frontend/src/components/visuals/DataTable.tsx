"use client";
import { useMemo, useState } from "react";
import type { VisualProps } from ".";
import { Badge, stateTone } from "@/components/ui";
import { asStrArr, fmtVal, num, titleCase } from "@/lib/format";

const isStatusCol = (c: string) => /status|state|result|severity/.test(c);

export function DataTable({ visual, data, rows }: VisualProps) {
  const cols = useMemo(() => {
    const e = asStrArr(visual.encodings?.columns);
    return (e.length ? e : data?.columns ?? []).filter((c) => rows.some((r) => c in r) || !data);
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
      <table className="w-full min-w-[480px] border-collapse text-left text-sm">
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
                  <td key={c} className="px-2 py-2">
                    {isStatusCol(c) && typeof v === "string" ? <Badge tone={stateTone(v)}>{v}</Badge>
                      : ci === 0 ? (
                        <span className="inline-flex flex-wrap items-center gap-1.5 font-mono text-[13px] font-medium">
                          {fmtVal(v)}
                          {flagged.has(id) && <span title="Data-quality flag: recorded status out of date" className="rounded bg-warn/15 px-1 text-[10px] font-sans font-semibold text-warn">⚠ DQ</span>}
                        </span>
                      ) : <span className={typeof v === "number" ? "tabular-nums" : ""}>{fmtVal(v)}</span>}
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
