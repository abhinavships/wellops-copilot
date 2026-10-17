"use client";
import { useState } from "react";
import type { VisualProps } from ".";
import type { IngestReport, Visual } from "@/contracts";
import { stateTone } from "@/components/ui";
import { fmtVal, num, titleCase } from "@/lib/format";
import { VisualBody } from "@/components/VisualFrame";

/*
 * Generative UI catalog extras. Each element reads either inline data from `encodings` (for answers without a
 * handle) or rows of the bound handle, where an encoding names a column. `pick(e.value, row)` resolves both.
 */
type Row = Record<string, unknown>;
const pick = (spec: unknown, row: Row | undefined): unknown =>
  typeof spec === "string" && row && spec in row ? row[spec] : spec;
const items = (visual: Visual, rows: Row[]): Row[] => (Array.isArray(visual.encodings?.items) ? (visual.encodings!.items as Row[]) : rows);
const TONE: Record<string, string> = { ok: "text-ok", bad: "text-bad", warn: "text-warn", info: "text-accent", neutral: "text-fg" };
const BG: Record<string, string> = { ok: "bg-ok", bad: "bg-bad", warn: "bg-warn", info: "bg-accent", neutral: "bg-muted" };

/** kpi: stat tiles. items [{label, value, unit?, delta?, tone?}] | encodings.values = columns of the first row. */
export function Kpi({ visual, rows }: VisualProps) {
  const e = visual.encodings ?? {};
  const units = (e.units ?? {}) as Record<string, string>;
  const labels = (e.labels ?? {}) as Record<string, string>;
  const tiles: Row[] = Array.isArray(e.items) ? (e.items as Row[])
    : Array.isArray(e.values) ? (e.values as string[]).map((c) => ({ label: labels[c] ?? titleCase(c), value: rows[0]?.[c], unit: units[c] }))
    : rows.map((r) => ({ label: pick(e.label, r), value: pick(e.value, r), unit: e.unit }));
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${tiles.length > 3 ? 140 : 170}px, 1fr))` }}>
      {tiles.map((t, i) => (
        <div key={i} className="rounded-2xl border border-line bg-raised/60 px-4 py-3">
          <div className="text-xs font-medium text-muted">{String(t.label ?? "")}</div>
          <div className={`mt-1 text-3xl font-semibold tabular-nums tracking-tight ${TONE[String(t.tone ?? "neutral")] ?? ""}`}>
            {fmtVal(t.value)}<span className="ml-1 text-base font-normal text-muted">{String(t.unit ?? "")}</span></div>
          {t.delta != null && <div className={`mt-0.5 text-xs ${(num(t.delta) ?? 0) >= 0 ? "text-ok" : "text-bad"}`}>{(num(t.delta) ?? 0) >= 0 ? "▲" : "▼"} {fmtVal(t.delta)}</div>}
          {t.detail != null && <div className="mt-1 text-xs text-muted">{String(t.detail)}</div>}
        </div>
      ))}
    </div>
  );
}

/** gauge: semicircle. encodings {value, max, min?, threshold?, unit?, label?} (numbers or column names). */
export function Gauge({ visual, rows }: VisualProps) {
  const e = visual.encodings ?? {}, r = rows[0];
  const v = num(pick(e.value, r)) ?? 0, min = num(pick(e.min, r)) ?? 0, max = num(pick(e.max, r)) ?? 100;
  const thr = num(pick(e.threshold, r));
  const f = Math.max(0, Math.min(1, (v - min) / (max - min || 1)));
  const arc = (t: number) => { const a = Math.PI * (1 - t); return `${100 + 80 * Math.cos(a)},${100 - 80 * Math.sin(a)}`; };
  const good = thr == null ? true : v >= thr;
  return (
    <figure className="mx-auto max-w-xs text-center">
      <svg viewBox="0 0 200 115" role="img" aria-label={`${String(e.label ?? "value")}: ${v} of ${max}`}>
        <path d={`M${arc(0)} A80 80 0 0 1 ${arc(1)}`} fill="none" stroke="rgb(var(--line))" strokeWidth={14} strokeLinecap="round" />
        <path d={`M${arc(0)} A80 80 0 0 1 ${arc(f)}`} fill="none" stroke={good ? "rgb(var(--ok))" : "rgb(var(--bad))"} strokeWidth={14} strokeLinecap="round" style={{ transition: "all .6s" }} />
        {thr != null && (() => { const t = (thr - min) / (max - min || 1); const [x, y] = arc(t).split(",").map(Number); return <circle cx={x} cy={y} r={4} fill="rgb(var(--warn))" />; })()}
        <text x={100} y={92} textAnchor="middle" fontSize={26} fontWeight={600} fill="rgb(var(--fg))">{fmtVal(v)}{String(e.unit ?? "")}</text>
      </svg>
      <figcaption className="text-xs text-muted">{String(e.label ?? "")}{thr != null && ` · threshold ${fmtVal(thr)}${String(e.unit ?? "")}`}</figcaption>
    </figure>
  );
}

/** progress: bars. items [{label, value, max}] or rows with encodings {label, value, max}. */
export function Progress({ visual, rows }: VisualProps) {
  const e = visual.encodings ?? {};
  return (
    <ul className="space-y-3">
      {items(visual, rows).map((r, i) => {
        const v = num(pick(e.value ?? "value", r)) ?? 0, m = num(pick(e.max ?? "max", r)) ?? 100;
        return (
          <li key={i}>
            <div className="flex justify-between text-sm"><span>{String(pick(e.label ?? "label", r) ?? "")}</span><span className="tabular-nums text-muted">{fmtVal(v)} / {fmtVal(m)}</span></div>
            <div className="mt-1 h-2 overflow-hidden rounded-full bg-line"><div className="h-full rounded-full bg-accent transition-all duration-700" style={{ width: `${Math.min(100, (v / (m || 1)) * 100)}%` }} /></div>
          </li>
        );
      })}
    </ul>
  );
}

/** timeline: vertical events. encodings {date, title, detail?, state?} over rows or items. */
export function Timeline({ visual, rows }: VisualProps) {
  const e = visual.encodings ?? {};
  const list = items(visual, rows);
  return (
    <ol className="relative ml-2 border-l border-line">
      {list.map((r, i) => {
        const st = String(pick(e.state ?? "state", r) ?? "");
        return (
          <li key={i} className="mb-4 ml-4">
            <span className={`absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full ${BG[stateTone(st)] ?? "bg-accent"}`} />
            <div className="text-xs text-muted">{String(pick(e.date ?? "date", r) ?? "")}</div>
            <div className="text-sm font-medium">{String(pick(e.title ?? "title", r) ?? "")}</div>
            {pick(e.detail ?? "detail", r) != null && <div className="text-xs text-muted">{String(pick(e.detail ?? "detail", r))}</div>}
          </li>
        );
      })}
    </ol>
  );
}

/** list: a simple list, or a kanban board when encodings.group names a column. */
export function ListBoard({ visual, rows }: VisualProps) {
  const e = visual.encodings ?? {};
  const list = items(visual, rows);
  const title = (r: Row) => String(pick(e.title ?? Object.keys(r)[0], r) ?? "");
  const sub = (r: Row) => (e.subtitle ? String(pick(e.subtitle, r) ?? "") : "");
  const Item = ({ r }: { r: Row }) => (
    <div className="rounded-xl border border-line bg-panel px-3 py-2 shadow-sm"><div className="text-sm font-medium">{title(r)}</div>{sub(r) && <div className="text-xs text-muted">{sub(r)}</div>}</div>
  );
  if (typeof e.group !== "string") return <div className="space-y-2">{list.map((r, i) => <Item key={i} r={r} />)}</div>;
  const groups = [...new Set(list.map((r) => String(r[e.group as string])))];
  return (
    <div className="flex gap-3 overflow-x-auto pb-1">
      {groups.map((g) => (
        <div key={g} className="w-56 shrink-0 rounded-2xl bg-raised/60 p-2">
          <div className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-muted">{g} · {list.filter((r) => String(r[e.group as string]) === g).length}</div>
          <div className="space-y-2">{list.filter((r) => String(r[e.group as string]) === g).map((r, i) => <Item key={i} r={r} />)}</div>
        </div>
      ))}
    </div>
  );
}

/** compare: side-by-side cards. encodings {title, fields: [cols]} over rows, or items [{title, fields: {k: v}}]. */
export function Compare({ visual, rows }: VisualProps) {
  const e = visual.encodings ?? {};
  const cards = Array.isArray(e.items) ? (e.items as { title: string; fields: Row }[])
    : rows.map((r) => ({ title: String(pick(e.title ?? Object.keys(r)[0], r)),
        fields: Object.fromEntries((Array.isArray(e.fields) ? (e.fields as string[]) : Object.keys(r).slice(1, 6)).map((c) => [titleCase(c), r[c]])) }));
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(170px, 1fr))` }}>
      {cards.map((c, i) => (
        <div key={i} className="rounded-2xl border border-line p-3">
          <div className="mb-2 font-mono text-sm font-semibold">{c.title}</div>
          <dl className="space-y-1 text-sm">{Object.entries(c.fields).map(([k, v]) => <div key={k} className="flex justify-between gap-2"><dt className="text-muted">{k}</dt><dd className="text-right tabular-nums">{fmtVal(v)}</dd></div>)}</dl>
        </div>
      ))}
    </div>
  );
}

/** image: encodings {src, alt?, caption?} (generated or uploaded images). */
export function ImageView({ visual }: VisualProps) {
  const e = visual.encodings ?? {};
  if (typeof e.src !== "string") return <p className="text-sm text-muted">No image source.</p>;
  return (
    <figure>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={e.src} alt={String(e.alt ?? visual.title ?? "image")} className="max-h-[480px] w-full rounded-xl object-contain" />
      {e.caption != null && <figcaption className="mt-1 text-xs text-muted">{String(e.caption)}</figcaption>}
    </figure>
  );
}

/** ingest_report: what POST /api/ingest made of an uploaded file. encodings.report = IngestReport. */
export function IngestCard({ visual }: VisualProps) {
  const r = (visual.encodings?.report ?? {}) as IngestReport;
  const stats: [string, unknown][] = [["Tables", r.tables], ["Rows", r.rows], ["Docs", r.docs], ["Images", r.images]];
  const prev = Array.isArray(r.preview) ? (r.preview as Row[]) : [];
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <div className="grid h-10 w-10 place-items-center rounded-xl bg-accent/15 text-accent" aria-hidden>⇪</div>
        <div className="min-w-0"><div className="truncate text-sm font-semibold">{r.file ?? "file"}</div><div className="text-xs text-muted">{r.kind ?? "unknown type"}</div></div>
      </div>
      <div className="grid grid-cols-4 gap-2">
        {stats.map(([k, v]) => <div key={k} className="rounded-xl bg-raised/60 px-3 py-2"><div className="text-[11px] text-muted">{k}</div><div className="text-lg font-semibold tabular-nums">{Array.isArray(v) ? v.length : fmtVal(v ?? 0)}</div></div>)}
      </div>
      {r.summary && <p className="text-sm">{r.summary}</p>}
      {prev.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-line">
          <table className="w-full text-xs"><thead><tr>{Object.keys(prev[0]).map((c) => <th key={c} className="px-2 py-1 text-left font-medium text-muted">{c}</th>)}</tr></thead>
            <tbody>{prev.slice(0, 5).map((p, i) => <tr key={i} className="border-t border-line">{Object.values(p).map((v, j) => <td key={j} className="px-2 py-1">{fmtVal(v)}</td>)}</tr>)}</tbody></table>
        </div>
      )}
    </div>
  );
}

/** callout: encodings {tone: info|ok|warn|bad, title?, text}. */
export function Callout({ visual, rows }: VisualProps) {
  const e = visual.encodings ?? {};
  const tone = String(e.tone ?? "info");
  const ring = { ok: "border-ok/40 bg-ok/10", bad: "border-bad/40 bg-bad/10", warn: "border-warn/40 bg-warn/10", info: "border-accent/40 bg-accent/10" }[tone] ?? "border-line";
  return (
    <div className={`rounded-xl border px-4 py-3 ${ring}`} role={tone === "bad" ? "alert" : "note"}>
      {e.title != null && <div className={`text-sm font-semibold ${TONE[tone] ?? ""}`}>{String(pick(e.title, rows[0]))}</div>}
      <div className="text-sm">{String(pick(e.text ?? "", rows[0]) ?? "")}</div>
    </div>
  );
}

/** tabs: encodings.tabs [{label, visual}]: several views of one answer, one shown at a time. */
export function Tabs({ visual, answer }: VisualProps) {
  const tabs = (visual.encodings?.tabs ?? []) as { label: string; visual: Visual }[];
  const [i, setI] = useState(0);
  if (!tabs.length) return null;
  return (
    <div>
      <div role="tablist" className="mb-3 inline-flex rounded-xl bg-raised/70 p-1">
        {tabs.map((t, k) => <button key={k} role="tab" type="button" aria-selected={k === i} onClick={() => setI(k)}
          className={`rounded-lg px-3 py-1 text-sm transition ${k === i ? "bg-panel font-medium shadow-sm" : "text-muted hover:text-fg"}`}>{t.label}</button>)}
      </div>
      <div role="tabpanel"><VisualBody visual={tabs[i].visual} answer={answer} /></div>
    </div>
  );
}
