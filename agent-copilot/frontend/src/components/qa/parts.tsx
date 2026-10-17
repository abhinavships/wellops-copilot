"use client";
import type { QaCategory } from "@/lib/qa";

export const MONO = "var(--font-mono)";
export const INK = "rgb(var(--c1))";
export const AMBER = "rgb(var(--c3))";
export const RED = "rgb(var(--c2))";
export const GREY = "rgb(var(--c4))";

/** A numbered figure on the paper: mono caption "FIG. n — title", optional controls on the right. */
export function Fig({ n, title, right, children, className = "" }: { n: number | string; title: string; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-line bg-panel/85 p-4 shadow-[0_1px_0_rgb(var(--line))] backdrop-blur-[1px] ${className}`}>
      <header className="label mb-3 flex flex-wrap items-center gap-2 border-b border-line pb-1.5">
        <span className="text-fg">Fig. {n}</span><span>— {title}</span>
        <span className="flex-1" />
        {right}
      </header>
      {children}
    </section>
  );
}

/** Heat cell colour: ink when it fully passes, amber for partial credit, red when it mostly fails. */
export function heat(score: number | null | undefined, passed: boolean): { bg: string; fg: string } {
  if (score === null || score === undefined) return { bg: "transparent", fg: "rgb(var(--muted))" };
  if (passed) return { bg: "rgb(var(--fg))", fg: "rgb(var(--bg))" };
  if (score >= 0.5) return { bg: `rgb(var(--hl) / ${0.35 + 0.6 * score})`, fg: "rgb(var(--fg))" };
  return { bg: `rgb(var(--bad) / ${0.85 - 0.5 * score})`, fg: "#fff" };
}

export const CAT_ABBR: Record<string, string> = {
  run_error: "ERR", memory_leak: "LEAK", ungrounded_claim: "UNGR", wrong_window: "WIN", wrong_number: "NUM",
  correction_not_applied: "CORR", conflict_not_shown: "CONF", invented_history: "HIST", missing_entity: "ENT",
  correction_not_disclosed: "DISC", wrong_visual: "VIS", missing_evidence: "EVID", trajectory: "PATH", other: "?",
};

export function CategoryChip({ cat, cats, primary }: { cat: string; cats?: QaCategory[]; primary?: boolean }) {
  const c = cats?.find((x) => x.key === cat);
  return (
    <span title={c?.description ?? cat}
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-px font-mono text-[10.5px] uppercase tracking-wide ${primary ? "border-fg bg-hl/80 text-fg" : "border-line text-muted"}`}>
      {CAT_ABBR[cat] ?? cat}<span className="normal-case tracking-normal">{c?.label ?? cat.replace(/_/g, " ")}</span>
    </span>
  );
}

export function ScoreBar({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="font-mono text-[11px] text-muted">n/a</span>;
  const pass = v >= 0.999;
  return (
    <span className="inline-flex items-center gap-2">
      <span className="relative h-1.5 w-16 overflow-hidden rounded-full bg-raised">
        <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${Math.max(2, v * 100)}%`, background: pass ? INK : v >= 0.5 ? AMBER : RED }} />
      </span>
      <span className={`font-mono text-[11.5px] ${pass ? "text-fg" : v >= 0.5 ? "text-warn" : "text-bad"}`}>{v.toFixed(2)}</span>
    </span>
  );
}

export function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  return (
    <label className="inline-flex items-center gap-1.5 normal-case tracking-normal">
      <span className="label">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="max-w-[16rem] rounded-md border border-line bg-bg px-2 py-1 font-mono text-[12px] text-fg">
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

export function Seg<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <span role="group" className="inline-flex rounded-md border border-line bg-bg p-0.5 normal-case tracking-normal">
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}
          className={`rounded px-2 py-0.5 font-mono text-[11px] ${o.value === value ? "bg-fg text-bg" : "text-muted hover:text-fg"}`}>{o.label}</button>
      ))}
    </span>
  );
}

export function Json({ v, max = 4000 }: { v: unknown; max?: number }) {
  let s = typeof v === "string" ? v : JSON.stringify(v, null, 1);
  if (typeof v === "string") { try { s = JSON.stringify(JSON.parse(v), null, 1); } catch { /* plain text */ } }
  const cut = s.length > max;
  return <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md border border-line bg-bg p-2 font-mono text-[11px] leading-snug text-fg/85">{cut ? `${s.slice(0, max)}\n… (${s.length - max} more chars)` : s}</pre>;
}

export const tooltipStyle = {
  contentStyle: { background: "rgb(var(--fg))", color: "rgb(var(--bg))", border: "none", borderRadius: 10, fontSize: 12, fontFamily: MONO },
  itemStyle: { color: "rgb(var(--bg))" }, labelStyle: { color: "rgb(var(--bg))", fontWeight: 600 },
};
export const tick = { fontSize: 10.5, fill: "rgb(var(--muted))", fontFamily: MONO };
