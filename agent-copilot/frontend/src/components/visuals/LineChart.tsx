"use client";
import { useState } from "react";
import type { VisualProps } from ".";
import { asStrArr, fmtDate, fmtVal, isDateStr, niceTicks, num, titleCase, useWidth } from "@/lib/format";

const COLORS = ["rgb(var(--c1))", "rgb(var(--c3))", "rgb(var(--c4))", "rgb(var(--c2))"];

export function LineChart({ visual, data, rows }: VisualProps) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const e = visual.encodings ?? {};
  const xc = (typeof e.x === "string" ? e.x : data?.columns[0]) ?? "x";
  const ys = asStrArr(e.y).length ? asStrArr(e.y) : (data?.columns ?? []).slice(1, 2);
  const ann = visual.annotations ?? [];
  const thresholds = ann.filter((a) => a.type === "threshold");
  const bands = ann.filter((a) => a.type === "band");

  const isTime = rows.length > 0 && rows.every((r) => isDateStr(r[xc]));
  const xv = rows.map((r, i) => (isTime ? new Date(String(r[xc]).slice(0, 10)).getTime() : num(r[xc]) ?? i));
  const toX = (v: unknown, i = 0): number => (isDateStr(v) ? new Date(v.slice(0, 10)).getTime() : num(v) ?? i);

  const W = Math.max(width, 280), H = 260, m = { l: 40, r: 16, t: 14, b: 30 };
  const allY: number[] = [];
  rows.forEach((r) => { ys.forEach((y) => { const n = num(r[y]); if (n !== null) allY.push(n); }); thresholds.forEach((t) => { const n = typeof t.column === "string" ? num(r[t.column]) : null; if (n !== null) allY.push(n); }); });
  thresholds.forEach((t) => { const n = num(t.value ?? t.y); if (n !== null) allY.push(n); });
  const yMin = Math.min(0, ...allY), yMax = Math.max(...allY, 1) * 1.05;
  const xMin = Math.min(...xv), xMax = Math.max(...xv);
  const sx = (v: number) => m.l + (xMax === xMin ? 0.5 : (v - xMin) / (xMax - xMin)) * (W - m.l - m.r);
  const sy = (v: number) => m.t + (1 - (v - yMin) / (yMax - yMin)) * (H - m.t - m.b);
  const step = xv.length > 1 ? (xMax - xMin) / (xv.length - 1) : 1;
  const path = (col: string) => rows.map((r, i) => { const n = num(r[col]); return n === null ? null : `${sx(xv[i]).toFixed(1)},${sy(n).toFixed(1)}`; }).filter(Boolean).map((p, i) => `${i ? "L" : "M"}${p}`).join(" ");
  const xLabel = (v: number) => (isTime ? fmtDate(new Date(v).toISOString()) : fmtVal(v));
  const tickEvery = Math.max(1, Math.ceil(rows.length / Math.max(2, Math.floor((W - m.l - m.r) / 52))));

  return (
    <div ref={ref} className="relative w-full">
      <ul className="mb-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" aria-label="Legend">
        {ys.map((y, i) => <li key={y} className="flex items-center gap-1.5"><span className="h-0.5 w-4 rounded" style={{ background: COLORS[i % 4] }} />{titleCase(y)}</li>)}
        {thresholds.map((t, i) => <li key={i} className="flex items-center gap-1.5"><span className="w-4 border-t-2 border-dashed border-warn" />{String(t.label ?? "Threshold")}</li>)}
        {bands.map((b, i) => <li key={i} className="flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-sm bg-bad/20" />{String(b.label ?? "Band")}</li>)}
      </ul>
      {width > 0 && (
        <svg width={W} height={H} role="img" aria-label={`${visual.title ?? "Line chart"}: ${ys.join(", ")} by ${xc}`}
          onPointerLeave={() => setHover(null)}
          onPointerMove={(ev) => {
            const bx = ev.currentTarget.getBoundingClientRect().left; const px = ev.clientX - bx;
            let best = 0, bd = Infinity; xv.forEach((v, i) => { const d = Math.abs(sx(v) - px); if (d < bd) { bd = d; best = i; } }); setHover(best);
          }}>
          {niceTicks(yMin, yMax, 5).map((t) => (
            <g key={t}><line x1={m.l} x2={W - m.r} y1={sy(t)} y2={sy(t)} stroke="rgb(var(--line))" strokeWidth={1} />
              <text x={m.l - 6} y={sy(t) + 4} textAnchor="end" fontSize={11} fill="rgb(var(--muted))">{t}</text></g>
          ))}
          {bands.map((b, i) => {
            const from = toX(b.from ?? b.x_from), to = toX(b.to ?? b.x_to);
            const x1 = Math.max(m.l, sx(from - step / 2)), x2 = Math.min(W - m.r, sx(to + step / 2));
            return (<g key={i}><rect x={x1} y={m.t} width={Math.max(0, x2 - x1)} height={H - m.t - m.b} fill="rgb(var(--bad))" opacity={0.14} />
              <text x={(x1 + x2) / 2} y={m.t + 11} textAnchor="middle" fontSize={10.5} fontWeight={600} fill="rgb(var(--bad))">{String(b.label ?? "")}</text></g>);
          })}
          {thresholds.map((t, i) => {
            const col = typeof t.column === "string" ? t.column : null;
            if (col) {
              const d = path(col); const last = [...rows].reverse().find((r) => num(r[col]) !== null);
              return (<g key={i}><path d={d} fill="none" stroke="rgb(var(--warn))" strokeWidth={1.75} strokeDasharray="5 4" />
                {last && <text x={m.l + 6} y={sy(num(rows[0][col]) ?? 0) - 6} fontSize={10.5} fontWeight={600} fill="rgb(var(--warn))">{String(t.label ?? "")}</text>}</g>);
            }
            const v = num(t.value ?? t.y); if (v === null) return null;
            return (<g key={i}><line x1={m.l} x2={W - m.r} y1={sy(v)} y2={sy(v)} stroke="rgb(var(--warn))" strokeWidth={1.75} strokeDasharray="5 4" />
              <text x={m.l + 6} y={sy(v) - 5} fontSize={10.5} fontWeight={600} fill="rgb(var(--warn))">{String(t.label ?? "")}</text></g>);
          })}
          {ys.map((y, i) => <path key={y} d={path(y)} fill="none" stroke={COLORS[i % 4]} strokeWidth={2.25} strokeLinejoin="round" strokeLinecap="round" />)}
          {ys.map((y, i) => rows.map((r, k) => { const n = num(r[y]); return n === null ? null : <circle key={`${y}${k}`} cx={sx(xv[k])} cy={sy(n)} r={hover === k ? 4.5 : 2.5} fill={COLORS[i % 4]} />; }))}
          {rows.map((r, i) => i % tickEvery === 0 && <text key={i} x={sx(xv[i])} y={H - 10} textAnchor="middle" fontSize={11} fill="rgb(var(--muted))">{xLabel(xv[i])}</text>)}
          {hover !== null && <line x1={sx(xv[hover])} x2={sx(xv[hover])} y1={m.t} y2={H - m.b} stroke="rgb(var(--muted))" strokeDasharray="2 3" />}
        </svg>
      )}
      {hover !== null && rows[hover] && (
        <div className="pointer-events-none absolute right-2 top-6 rounded-md border border-line bg-panel/95 px-2 py-1.5 text-xs shadow-lg">
          <div className="font-semibold">{xLabel(xv[hover])}</div>
          {ys.map((y, i) => <div key={y} style={{ color: COLORS[i % 4] }}>{titleCase(y)}: {fmtVal(rows[hover][y])}</div>)}
          {thresholds.map((t, i) => typeof t.column === "string" && <div key={i} className="text-warn">{String(t.label)}: {fmtVal(rows[hover][t.column as string])}</div>)}
        </div>
      )}
    </div>
  );
}
