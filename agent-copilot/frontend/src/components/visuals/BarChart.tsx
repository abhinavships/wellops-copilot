"use client";
import type { VisualProps } from ".";
import { fmtVal, num, titleCase } from "@/lib/format";

export function BarChart({ visual, data, rows }: VisualProps) {
  const e = visual.encodings ?? {};
  const xc = (typeof e.x === "string" ? e.x : data?.columns[0]) ?? "x";
  const yc = (typeof e.y === "string" ? e.y : data?.columns[1]) ?? "y";
  const dir = e.sort === "asc" ? 1 : e.sort === "desc" ? -1 : 0;
  const items = rows.map((r) => ({ label: String(r[xc]), v: num(r[yc]) ?? 0 }));
  if (dir) items.sort((a, b) => (a.v - b.v) * dir);
  const max = Math.max(...items.map((i) => i.v), 1);
  const ann = (visual.annotations ?? []).filter((a) => a.type === "threshold" && num(a.value ?? a.y) !== null);
  return (
    <div>
      <ul className="space-y-2" aria-label={`${titleCase(yc)} by ${titleCase(xc)}`}>
        {items.map((it, i) => (
          <li key={it.label} className="grid grid-cols-[5.5rem_1fr_3rem] items-center gap-2 text-sm sm:grid-cols-[7rem_1fr_3.5rem]">
            <span className="truncate font-mono text-[13px]">{it.label}</span>
            <div className="relative h-6 rounded bg-raised" role="img" aria-label={`${it.label}: ${fmtVal(it.v)}`}>
              <div className="h-6 rounded" style={{ width: `${(it.v / max) * 100}%`, background: `rgb(var(--c1) / ${1 - i * 0.12})` }} />
              {ann.map((a, k) => <div key={k} className="absolute top-0 h-6 border-l-2 border-dashed border-warn" style={{ left: `${(num(a.value ?? a.y)! / max) * 100}%` }} title={String(a.label ?? "")} />)}
            </div>
            <span className="text-right font-semibold tabular-nums">{fmtVal(it.v)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-muted">{titleCase(yc)}{dir ? ` · sorted ${dir < 0 ? "high to low" : "low to high"}` : ""}</p>
    </div>
  );
}
