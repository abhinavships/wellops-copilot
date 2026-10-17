"use client";
import type { VisualProps } from ".";
import { fmtVal, num, titleCase, useWidth } from "@/lib/format";

export function Heatmap({ visual, data, rows }: VisualProps) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const e = visual.encodings ?? {};
  const cols = data?.columns ?? [];
  const xc = (typeof e.x === "string" ? e.x : cols[1]) ?? "x";
  const yc = (typeof e.y === "string" ? e.y : cols[0]) ?? "y";
  const vc = (typeof e.value === "string" ? e.value : cols[2]) ?? "v";
  const xs = [...new Set(rows.map((r) => String(r[xc])))];
  const ys = [...new Set(rows.map((r) => String(r[yc])))];
  const cell = new Map(rows.map((r) => [`${r[yc]}|${r[xc]}`, num(r[vc])]));
  const vals = [...cell.values()].filter((v): v is number => v !== null);
  const max = Math.max(...vals, 1), min = Math.min(...vals, 0);
  const labelW = 52, cw = Math.max(26, Math.floor(((width || 320) - labelW) / Math.max(xs.length, 1)));
  return (
    <div ref={ref} className="w-full">
      <div role="table" aria-label={`${titleCase(vc)} by ${titleCase(yc)} and ${titleCase(xc)}`}>
        <div role="row" className="flex" style={{ paddingLeft: labelW }}>
          {xs.map((x) => <div role="columnheader" key={x} style={{ width: cw }} className="pb-1 text-center text-[11px] text-muted">{x}</div>)}
        </div>
        {ys.map((y) => (
          <div role="row" key={y} className="flex items-center">
            <div role="rowheader" style={{ width: labelW }} className="pr-2 text-right font-mono text-xs font-medium">{y}</div>
            {xs.map((x) => {
              const v = cell.get(`${y}|${x}`) ?? null; const t = v === null ? 0 : (v - min) / (max - min || 1);
              return (
                <div role="cell" key={x} title={`${y} · ${x}: ${fmtVal(v)}`} style={{ width: cw, height: 34, background: `rgb(var(--accent) / ${0.08 + t * 0.85})` }}
                  className="flex items-center justify-center border border-panel text-xs font-semibold tabular-nums">
                  <span style={{ color: t > 0.55 ? "rgb(var(--accent-fg))" : "rgb(var(--fg))" }}>{fmtVal(v)}</span>
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-2 text-[11px] text-muted" style={{ paddingLeft: labelW }}>
        {fmtVal(min)}<span className="h-2 w-24 rounded" style={{ background: "linear-gradient(90deg, rgb(var(--accent) / .08), rgb(var(--accent) / .93))" }} />{fmtVal(max)} · {titleCase(vc)}
      </div>
    </div>
  );
}
