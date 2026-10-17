"use client";
import { useMemo, useState } from "react";
import { Bar, BarChart, Brush, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { VisualProps } from ".";
import { VISUALS } from ".";
import type { CitationKind, HandleData, Visual } from "@/contracts";
import { useUi, type EvidenceTarget } from "@/components/ui";
import { asStrArr, fmtVal, num, titleCase } from "@/lib/format";

const COLORS = ["rgb(var(--c1))", "rgb(var(--c3))", "rgb(var(--c4))", "rgb(var(--c2))"];
const MONO = "var(--font-mono)";
const ID_COLS: [string, CitationKind][] = [["inspection_id", "inspection"], ["maintenance_id", "maintenance"], ["event_id", "reg_event"], ["note_id", "note"]];

/** Chart types a card may switch between, from the visual and the result shape. */
export function viewsFor(visual: Visual, data: HandleData | null): string[] {
  const shape = data?.shape;
  if (visual.type === "heatmap" || shape === "matrix") return ["heatmap", "table"];
  if (["bar", "line"].includes(visual.type) || shape === "category_measure" || shape === "timeseries") {
    const first = visual.type === "line" || shape === "timeseries" ? "line" : "bar";
    return [first, first === "line" ? "bar" : "line", "table"];
  }
  return data && visual.type !== "table" ? [visual.type, "table"] : [visual.type];
}

/** Evidence for one row: its own record when it has an id column, else the row itself as a grounded value. */
export function rowEvidence(row: Record<string, unknown>, data: HandleData, col?: string): EvidenceTarget {
  for (const [c, kind] of ID_COLS) if (typeof row[c] === "string") return { citation: { kind, id: row[c] as string } };
  const key = data.columns[0];
  const v = col ? row[col] : row[key];
  return { claim: { text: `${row[key]}${col ? ` · ${titleCase(col)}` : ""}`, value: (v ?? "") as string | number | boolean,
    handle: data.handle, column: col ?? key, row_key: { [key]: row[key] } } };
}

function Toggle({ on, children, onClick, title }: { on: boolean; children: React.ReactNode; onClick: () => void; title?: string }) {
  return <button type="button" onClick={onClick} title={title} aria-pressed={on}
    className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${on ? "bg-accent text-accent-fg" : "text-muted hover:text-fg"}`}>{children}</button>;
}

/** Recharts bar/line with tooltips, legend toggles, sort, brush and click-through to evidence. */
export function InteractiveChart({ visual, data, rows, view }: VisualProps & { view: string }) {
  const { openEvidence } = useUi();
  const e = visual.encodings ?? {};
  const cols = data?.columns ?? [];
  const xc = (typeof e.x === "string" ? e.x : cols[0]) ?? "x";
  const numeric = cols.filter((c) => c !== xc && rows.some((r) => typeof r[c] === "number") && !/(^lat$|^lon$|_id$)/.test(c));
  const ys = asStrArr(e.y).length ? asStrArr(e.y) : numeric.slice(0, 1);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<0 | 1 | -1>(e.sort === "desc" ? -1 : e.sort === "asc" ? 1 : 0);
  const sorted = useMemo(() => {
    if (view !== "bar" || !sort) return rows;
    return [...rows].sort((a, b) => ((num(a[ys[0]]) ?? 0) - (num(b[ys[0]]) ?? 0)) * sort);
  }, [rows, view, sort, ys]);
  const bands = (visual.annotations ?? []).filter((a) => a.type === "band");
  const peak = Math.max(...rows.map((r) => num(r[ys[0]]) ?? -Infinity)); // the answer's key item gets the highlighter
  const click = (row: unknown, col?: string) => { if (data && row && typeof row === "object") openEvidence(rowEvidence(row as Record<string, unknown>, data, col)); };
  const legend = (
    <Legend onClick={(o) => { const k = String(o.dataKey); setHidden((h) => { const n = new Set(h); if (n.has(k)) n.delete(k); else n.add(k); return n; }); }}
      formatter={(v: string) => <span style={{ cursor: "pointer", opacity: hidden.has(v) ? 0.4 : 1 }}>{titleCase(v)}</span>} wrapperStyle={{ fontSize: 12 }} />
  );
  const tooltip = <Tooltip cursor={{ fill: "rgb(var(--muted) / .15)", stroke: "rgb(var(--muted))" }} formatter={(v, n) => [fmtVal(v as unknown), titleCase(String(n))]} labelFormatter={(l) => String(l)}
    contentStyle={{ background: "rgb(var(--fg))", color: "rgb(var(--bg))", border: "none", borderRadius: 10, fontSize: 12, fontFamily: MONO }}
    itemStyle={{ color: "rgb(var(--bg))" }} labelStyle={{ color: "rgb(var(--bg))", fontWeight: 600 }} />;
  const axes = (<>
    <CartesianGrid stroke="rgb(var(--line))" strokeDasharray="2 4" vertical={false} />
    <XAxis dataKey={xc} tick={{ fontSize: 10.5, fill: "rgb(var(--muted))", fontFamily: MONO }} axisLine={{ stroke: "rgb(var(--fg))" }} tickLine={false} />
    <YAxis tick={{ fontSize: 10.5, fill: "rgb(var(--muted))", fontFamily: MONO }} width={40} axisLine={false} tickLine={false} />
  </>);
  return (
    <div className="flex h-full min-h-[220px] flex-col">
      {view === "bar" && (
        <div className="mb-1 flex items-center gap-1 text-[11px] text-muted">Sort
          <Toggle on={sort === -1} onClick={() => setSort(sort === -1 ? 0 : -1)} title="Largest first">desc</Toggle>
          <Toggle on={sort === 1} onClick={() => setSort(sort === 1 ? 0 : 1)} title="Smallest first">asc</Toggle>
          <span className="ml-auto">click a bar for its evidence</span>
        </div>
      )}
      {view === "line" && <div className="mb-1 text-right text-[11px] text-muted">drag the brush to zoom · click a point for its evidence</div>}
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%" minHeight={200}>
          {view === "bar" ? (
            <BarChart data={sorted} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              {axes}{tooltip}{legend}
              {ys.map((y, i) => (
                <Bar key={y} dataKey={y} hide={hidden.has(y)} fill={COLORS[i % 4]} radius={[3, 3, 0, 0]} cursor="pointer" maxBarSize={72}
                  onClick={(d) => click((d as { payload?: unknown }).payload, y)} isAnimationActive>
                  {i === 0 && sorted.map((r, k) => <Cell key={k} fill={num(r[y]) === peak ? "rgb(var(--hl))" : COLORS[0]} stroke={num(r[y]) === peak ? "rgb(var(--fg))" : "none"} />)}
                </Bar>))}
            </BarChart>
          ) : (
            <LineChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              {axes}{tooltip}{legend}
              {bands.map((b, i) => <ReferenceArea key={i} x1={String(b.from)} x2={String(b.to)} fill="rgb(var(--hl))" fillOpacity={0.45}
                label={{ value: String(b.label ?? ""), fontSize: 10, fill: "rgb(var(--bad))", position: "insideTop" }} />)}
              {ys.map((y, i) => <Line key={y} dataKey={y} hide={hidden.has(y)} stroke={COLORS[i % 4]} strokeWidth={i === 0 ? 2.25 : 1.75} dot={{ r: 2.5, strokeWidth: 0, fill: COLORS[i % 4] }}
                activeDot={{ r: 6, cursor: "pointer", onClick: (_ev: unknown, p: unknown) => click((p as { payload?: unknown }).payload, y) }} />)}
              {rows.length > 6 && <Brush dataKey={xc} height={18} stroke="rgb(var(--accent))" fill="rgb(var(--raised))" travellerWidth={8} />}
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Any visual, rendered as the chosen view: interactive charts for bar/line, the catalog component otherwise. */
export function SwitchableVisual(props: VisualProps & { view: string }) {
  const { view, visual } = props;
  if (view === "bar" || view === "line") return <InteractiveChart {...props} />;
  const Comp = VISUALS[view as Visual["type"]] ?? VISUALS[visual.type];
  const v = view === "table" && visual.type !== "table" ? { ...visual, type: "table" as const, encodings: {} } : visual;
  return Comp ? <Comp {...props} visual={v} /> : <p className="text-sm text-muted">No renderer for {view}.</p>;
}

export function ViewSwitcher({ views, view, onView }: { views: string[]; view: string; onView: (v: string) => void }) {
  if (views.length < 2) return null;
  return (
    <span role="group" aria-label="Chart type" className="inline-flex rounded-md border border-line bg-bg p-0.5" onPointerDown={(e) => e.stopPropagation()}>
      {views.map((v) => <Toggle key={v} on={v === view} onClick={() => onView(v)}>{titleCase(v)}</Toggle>)}
    </span>
  );
}
