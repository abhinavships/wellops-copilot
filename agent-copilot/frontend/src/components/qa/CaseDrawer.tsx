"use client";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { qa, runShort, runStamp, type QaCaseDetail, type QaCategory, type QaHistoryRow, type QaTurn, type TraceCall } from "@/lib/qa";
import { CategoryChip, INK, Json, ScoreBar, tick, tooltipStyle } from "./parts";

const SCORER_ORDER = ["facts", "evidence", "visual", "conflict", "overlay", "leak", "unconfirmed", "no_prior", "memory", "trajectory"];

/** Case drill-down: history across runs, then every turn's question, answer, tool trace, scorer details and failure category. */
export function CaseDrawer({ runId, caseId, cats, onClose, onRun }: { runId: string; caseId: string; cats: QaCategory[]; onClose: () => void; onRun: (run: string) => void }) {
  const [d, setD] = useState<QaCaseDetail | null>(null);
  const [hist, setHist] = useState<QaHistoryRow[]>([]);
  const [k, setK] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let on = true; setD(null); setErr(null); setK(0);
    qa.caseDetail(runId, caseId).then((x) => on && setD(x)).catch((e) => on && setErr(String(e)));
    qa.history(caseId).then((h) => on && setHist(h)).catch(() => undefined);
    return () => { on = false; };
  }, [runId, caseId]);
  useEffect(() => { const h = (e: KeyboardEvent) => e.key === "Escape" && onClose(); window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h); }, [onClose]);
  const cr = d?.runs[k];
  const histData = hist.map((h) => ({ x: `${runStamp(h.run_id)}${h.run_idx ? ` #${h.run_idx + 1}` : ""}`, run: h.run_id, score: h.score, target: h.target }));
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true" aria-label={`Case ${caseId}`}>
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="canvas-modal paper relative flex h-full w-full max-w-4xl flex-col overflow-hidden border-l border-line shadow-2xl">
        <header className="flex items-start gap-3 border-b border-line bg-bg/85 px-5 py-3 backdrop-blur">
          <div className="min-w-0 flex-1">
            <div className="label"><span className="text-fg">Case {caseId}</span> — {runShort({ run_id: runId, target: runId.split("-")[2] ?? null })}</div>
            <h2 className="display mt-0.5 text-xl font-semibold">{d?.title ?? "…"}</h2>
          </div>
          <button type="button" onClick={onClose} className="rounded-full px-3 py-1.5 text-sm text-muted hover:bg-raised hover:text-fg">Close ✕</button>
        </header>
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {err && <p className="text-sm text-bad">{err}</p>}
          {histData.length > 1 && (
            <section className="mb-5">
              <div className="label mb-1">Fig. A — this case across runs (click a point to open that run)</div>
              <div className="h-[110px] rounded-lg border border-line bg-panel/80 p-1">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={histData} margin={{ top: 6, right: 10, left: 0, bottom: 0 }} onClick={(s) => { const p = (s as { activePayload?: { payload?: { run?: string } }[] })?.activePayload?.[0]?.payload; if (p?.run) onRun(p.run); }}>
                    <XAxis dataKey="x" tick={tick} tickLine={false} axisLine={{ stroke: "rgb(var(--fg))" }} interval="preserveStartEnd" />
                    <YAxis domain={[0, 1]} tick={tick} width={28} axisLine={false} tickLine={false} />
                    <Tooltip {...tooltipStyle} formatter={(v) => [typeof v === "number" ? v.toFixed(3) : String(v), "score"]} />
                    <Line dataKey="score" stroke={INK} strokeWidth={2} dot={(p) => {
                      const { cx, cy, payload, index } = p as { cx: number; cy: number; payload: { run: string; score: number }; index: number };
                      const here = payload.run === runId;
                      return <circle key={index} cx={cx} cy={cy} r={here ? 5 : 3} fill={payload.score >= 0.999 ? INK : "rgb(var(--hl))"} stroke={here ? "rgb(var(--bad))" : INK} strokeWidth={here ? 2 : 1} />;
                    }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </section>
          )}
          {d && d.runs.length > 1 && (
            <div className="mb-4 flex items-center gap-1.5"><span className="label">repeat</span>
              {d.runs.map((r, i) => (
                <button key={r.id} type="button" onClick={() => setK(i)} aria-pressed={k === i}
                  className={`rounded px-2 py-0.5 font-mono text-[11px] ${k === i ? "bg-fg text-bg" : "border border-line text-muted"} ${r.passed ? "" : "underline decoration-bad decoration-2"}`}>k{i + 1}</button>
              ))}
            </div>
          )}
          {cr && (
            <>
              <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-panel/85 p-3">
                <span className="label">case score</span><ScoreBar v={cr.score} />
                <span className={`font-mono text-[12px] font-semibold ${cr.passed ? "text-ok" : "text-bad"}`}>{cr.passed ? "PASS" : "FAIL"}</span>
                {cr.leak ? <span className="rounded bg-bad px-1.5 font-mono text-[11px] text-white">LEAK</span> : null}
                <span className="flex flex-wrap gap-1">{(cr.categories ?? []).map((c) => <CategoryChip key={c} cat={c} cats={cats} primary={c === cr.failure_category} />)}</span>
                <span className="ml-auto font-mono text-[11.5px] text-muted">{cr.latency_s?.toFixed(1)} s · {cr.n_turns} turn{cr.n_turns === 1 ? "" : "s"}</span>
              </div>
              <ol className="flex flex-col gap-6">
                {cr.turns.map((t, i) => <TurnView key={t.id} t={t} i={i} cats={cats} />)}
              </ol>
            </>
          )}
          {!d && !err && <p className="text-sm text-muted">Loading…</p>}
        </div>
      </div>
    </div>
  );
}

function TurnView({ t, i, cats }: { t: QaTurn; i: number; cats: QaCategory[] }) {
  const a = t.answer ?? {};
  const meta = (a._meta ?? {}) as Record<string, unknown>;
  const events = (meta.events ?? {}) as Record<string, unknown[]>;
  const gates = ((events["wellops.verification"]?.[0] ?? null) as { gates?: { name: string; ok: boolean; detail?: string | null }[]; retries?: number } | null);
  const scorers = SCORER_ORDER.filter((n) => t.scores[n]).map((n) => [n, t.scores[n]] as const);
  return (
    <li className="turn-in rounded-xl border border-line bg-panel/90 p-4">
      <div className="label mb-2 flex flex-wrap items-center gap-2 border-b border-line pb-1.5">
        <span className="text-fg">Turn {i + 1}</span><span>— {t.turn_id}</span>
        <span className="normal-case tracking-normal">· {t.user} · {t.chat} · clock {t.clock}</span>
        {t.setup ? <span className="rounded bg-raised px-1.5 text-fg">setup (not scored)</span> : null}
        <span className="flex-1" />
        {!t.setup && <span className={t.passed ? "text-ok" : "text-bad"}>{t.passed ? "pass" : "fail"} · {t.score?.toFixed(3)}</span>}
      </div>
      <blockquote className="mb-3 border-l-2 border-fg pl-3 text-[15px] font-medium">“{t.say}”</blockquote>

      {t.taxonomy?.hits?.length ? (
        <div className="mb-3 rounded-lg border border-bad/30 bg-bad/[.05] p-2.5">
          <div className="label mb-1.5">failure category</div>
          <ul className="flex flex-col gap-1.5">
            {t.taxonomy.hits.map((h, j) => (
              <li key={j} className="flex flex-wrap items-start gap-2 text-[12px]">
                <CategoryChip cat={h.category} cats={cats} primary={h.category === t.taxonomy?.primary} />
                <span className="min-w-0 flex-1 break-words font-mono text-[11px] text-muted">{Object.entries(h.evidence).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ")}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {t.error && <div className="mb-3"><div className="label mb-1 text-bad">run error</div><Json v={t.error} max={1200} /></div>}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="min-w-0">
          <div className="label mb-1">answer</div>
          <div className="prose-chat max-h-80 overflow-auto rounded-lg border border-line bg-bg/70 p-3 text-[13.5px]">
            {a.markdown ? <ReactMarkdown>{a.markdown}</ReactMarkdown> : <p className="text-muted">(no answer text)</p>}
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5 text-[11.5px]">
            {(a.visuals ?? []).map((v, j) => <span key={j} className="rounded border border-fg px-1.5 font-mono">▣ {v.type}</span>)}
            {(a.citations ?? []).map((c, j) => <span key={j} className="rounded bg-raised px-1.5 font-mono text-muted">{c.id}</span>)}
            {(a.applied_overlays ?? []).length > 0 && <span className="rounded bg-hl/60 px-1.5 font-mono">overlays: {(a.applied_overlays ?? []).join(", ")}</span>}
            {(a.conflicts ?? []).length > 0 && <span className="rounded bg-warn/20 px-1.5 font-mono text-warn">{(a.conflicts ?? []).length} conflict(s)</span>}
          </div>
          <div className="mt-2 font-mono text-[11px] text-muted">
            {t.latency_s !== null && <>{t.latency_s.toFixed(1)} s</>}{t.model && <> · {t.model}</>}{t.retries !== null && <> · {t.retries} verifier retr{t.retries === 1 ? "y" : "ies"}</>}{t.tokens !== null && <> · {t.tokens} tok</>}
          </div>
          {gates?.gates && (
            <div className="mt-2 flex flex-wrap gap-1">
              {gates.gates.map((g) => <span key={g.name} title={g.detail ?? ""} className={`rounded px-1.5 font-mono text-[10.5px] ${g.ok ? "bg-raised text-muted" : "bg-bad text-white"}`}>{g.ok ? "✓" : "×"} {g.name}</span>)}
            </div>
          )}
        </div>
        <div className="min-w-0">
          <div className="label mb-1">tool trace ({(t.trace ?? []).length})</div>
          <ol className="flex flex-col gap-1">
            {(t.trace ?? []).map((c, j) => <TraceRow key={j} c={c} j={j} />)}
            {!(t.trace ?? []).length && <li className="text-[12px] text-muted">no tool calls</li>}
          </ol>
        </div>
      </div>

      {scorers.length > 0 && (
        <div className="mt-4">
          <div className="label mb-1">scorers</div>
          <table className="w-full text-left text-[12.5px]">
            <tbody>
              {scorers.map(([n, s]) => <ScorerRow key={n} name={n} s={s} />)}
            </tbody>
          </table>
        </div>
      )}
      {t.expect && Object.keys(t.expect).length > 0 && (
        <details className="mt-3"><summary className="label cursor-pointer hover:text-fg">expectation (golden key)</summary><Json v={t.expect} /></details>
      )}
    </li>
  );
}

function TraceRow({ c, j }: { c: TraceCall; j: number }) {
  const [open, setOpen] = useState(false);
  const args = typeof c.args === "string" ? c.args : JSON.stringify(c.args ?? {});
  return (
    <li className="rounded-md border border-line bg-bg/70">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-baseline gap-2 px-2 py-1 text-left">
        <span className="font-mono text-[10.5px] text-muted">{j + 1}</span>
        <span className={`font-mono text-[12px] ${c.error ? "text-bad" : "text-fg"}`}>{c.name}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted">{args}</span>
        <span aria-hidden className="text-[10px] text-muted">{open ? "▴" : "▾"}</span>
      </button>
      {open && <div className="flex flex-col gap-1 px-2 pb-2"><div className="label">args</div><Json v={c.args} max={2000} /><div className="label">result</div><Json v={c.result} max={3000} /></div>}
    </li>
  );
}

function ScorerRow({ name, s }: { name: string; s: { value: number | null; reason: string; details: Record<string, unknown> } }) {
  const [open, setOpen] = useState(false);
  const na = s.value === null;
  const hasDetails = Object.keys(s.details ?? {}).length > 0;
  return (
    <>
      <tr className={`border-b border-line/60 ${na ? "text-muted" : ""}`}>
        <td className="w-28 py-1 pr-3 font-mono text-[12px]">{name}</td>
        <td className="w-32 pr-3"><ScoreBar v={s.value} /></td>
        <td className="pr-3 text-[12px]">{na ? s.reason : s.value !== null && s.value < 0.999 ? <span className="text-bad">{s.reason}</span> : s.reason}</td>
        <td className="w-14 text-right">{hasDetails && !na && <button type="button" onClick={() => setOpen((o) => !o)} className="font-mono text-[11px] text-muted hover:text-fg">{open ? "hide" : "details"}</button>}</td>
      </tr>
      {open && <tr><td colSpan={4} className="pb-2"><Json v={s.details} /></td></tr>}
    </>
  );
}
