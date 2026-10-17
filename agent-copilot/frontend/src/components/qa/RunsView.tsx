"use client";
import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { f3, pct, qa, runShort, runStamp, type QaCaseResult, type QaCategory, type QaRun } from "@/lib/qa";
import { AMBER, CategoryChip, Fig, GREY, INK, ScoreBar, Seg, tick, tooltipStyle } from "./parts";

type Filter = "live" | "all";

export function RunsView({ runs, cats, selected, onSelect, onCase }: {
  runs: QaRun[]; cats: QaCategory[]; selected: string | null; onSelect: (id: string) => void; onCase: (run: string, cid: string) => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const shown = useMemo(() => runs.filter((r) => filter === "all" || r.target !== "fake"), [runs, filter]);
  const trend = shown.map((r) => ({ run: runShort(r), id: r.run_id, mean_score: r.mean_score, pass_rate: r.pass_rate, pass_hat_k: r.pass_hat_k, cases: r.n_cases }));
  return (
    <div className="flex flex-col gap-5">
      <Fig n={1} title="score trend across runs" right={<Seg value={filter} onChange={setFilter} options={[{ value: "all", label: "all targets" }, { value: "live", label: "live only" }]} />}>
        <div className="h-[240px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trend} margin={{ top: 8, right: 12, left: 0, bottom: 0 }} onClick={(s) => { const p = (s as { activePayload?: { payload?: { id?: string } }[] })?.activePayload?.[0]?.payload; if (p?.id) onSelect(p.id); }}>
              <CartesianGrid stroke="rgb(var(--line))" strokeDasharray="2 4" vertical={false} />
              <XAxis dataKey="run" tick={tick} axisLine={{ stroke: "rgb(var(--fg))" }} tickLine={false} interval="preserveStartEnd" />
              <YAxis domain={[0, 1]} tick={tick} width={36} axisLine={false} tickLine={false} tickFormatter={(v) => `${Math.round(v * 100)}`} />
              <Tooltip {...tooltipStyle} formatter={(v, n) => [typeof v === "number" ? v.toFixed(3) : String(v), String(n).replace(/_/g, " ")]} />
              <Legend wrapperStyle={{ fontSize: 12, fontFamily: "var(--font-mono)" }} formatter={(v: string) => v.replace(/_/g, " ")} />
              <Line dataKey="mean_score" stroke={INK} strokeWidth={2.25} dot={{ r: 3, fill: INK, strokeWidth: 0 }} activeDot={{ r: 6, cursor: "pointer" }} isAnimationActive />
              <Line dataKey="pass_rate" stroke={AMBER} strokeWidth={2} dot={{ r: 3, fill: AMBER, strokeWidth: 0 }} activeDot={{ r: 6, cursor: "pointer" }} />
              <Line dataKey="pass_hat_k" stroke={GREY} strokeDasharray="4 3" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <p className="mt-2 text-[12px] text-muted">Aggregate = 0.5 facts + 0.25 evidence + 0.25 visual, averaged over cases. The <span className="font-mono">fake</span> target
          answers from computed truth, so it checks the harness and is an upper bound, not a quality claim. Click a point to open that run.</p>
      </Fig>

      <Fig n={2} title={`runs (${shown.length})`}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-left text-[13px]">
            <thead className="label"><tr className="border-b border-line">
              {["run", "target", "model / harness", "cases", "pass rate", "mean score", "pass^k", "leaks", "latency/turn", "git"].map((h) => <th key={h} className="py-1.5 pr-3 font-normal">{h}</th>)}
            </tr></thead>
            <tbody>
              {[...shown].reverse().map((r) => (
                <tr key={r.run_id} onClick={() => onSelect(r.run_id)} aria-selected={selected === r.run_id}
                  className={`cursor-pointer border-b border-line/60 hover:bg-raised/70 ${selected === r.run_id ? "bg-hl/25" : ""}`}>
                  <td className="py-1.5 pr-3 font-mono text-[12px]">{runStamp(r.run_id)}{r.partial ? <span className="ml-1.5 rounded bg-warn/20 px-1 text-[10px] text-warn">PARTIAL</span> : null}</td>
                  <td className="pr-3 font-mono text-[12px]">{r.target}<span className="text-muted">/{r.suite}{r.k && r.k > 1 ? ` k=${r.k}` : ""}</span></td>
                  <td className="max-w-[16rem] truncate pr-3 text-[12px]" title={`${r.model ?? "unrecorded"} via ${r.harness ?? "?"}`}>{r.model ?? <span className="text-muted">unrecorded</span>}<span className="text-muted"> · {r.harness}</span></td>
                  <td className="pr-3 font-mono">{r.n_cases}</td>
                  <td className="pr-3 font-mono">{pct(r.pass_rate)}</td>
                  <td className="pr-3"><ScoreBar v={r.mean_score} /></td>
                  <td className="pr-3 font-mono">{f3(r.pass_hat_k)}</td>
                  <td className={`pr-3 font-mono ${r.leaks ? "font-semibold text-bad" : "text-muted"}`}>{r.leaks}</td>
                  <td className="pr-3 font-mono text-[12px]">{r.latency_mean_s !== null ? `${r.latency_mean_s.toFixed(1)} s` : "–"}</td>
                  <td className="pr-3 font-mono text-[11.5px] text-muted" title={r.git_sha_inferred ? "inferred from the last commit before the run started" : "recorded by the runner"}>{r.git_sha ?? "–"}{r.git_sha_inferred ? "*" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[11.5px] text-muted">* git sha inferred for runs recorded before the runner stored it.</p>
      </Fig>

      {selected && <RunCases runId={selected} cats={cats} onCase={onCase} />}
    </div>
  );
}

function RunCases({ runId, cats, onCase }: { runId: string; cats: QaCategory[]; onCase: (run: string, cid: string) => void }) {
  const [cases, setCases] = useState<QaCaseResult[] | null>(null);
  const [run, setRun] = useState<QaRun | null>(null);
  useEffect(() => { let on = true; setCases(null); qa.run(runId).then((r) => { if (on) { setCases(r.cases); setRun(r); } }).catch(() => on && setCases([])); return () => { on = false; }; }, [runId]);
  const scorers = Object.entries(run?.per_scorer ?? {}).sort();
  return (
    <Fig n={3} title={`run ${runId}`}>
      {run && (
        <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-[12px] text-muted">
          <span>model <b className="font-mono text-fg">{run.model ?? "unrecorded"}</b></span>
          <span>harness <b className="font-mono text-fg">{run.harness}</b></span>
          <span>data <b className="font-mono text-fg">{run.data_version}</b></span>
          <span>retries <b className="font-mono text-fg">{run.retries_total ?? 0}</b></span>
          <span>tokens <b className="font-mono text-fg">{run.tokens_total ?? "–"}</b></span>
          <span>wall <b className="font-mono text-fg">{run.latency_total_s?.toFixed(0)} s</b></span>
        </div>
      )}
      {scorers.length > 0 && (
        <div className="mb-4 grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-5">
          {scorers.map(([n, v]) => <div key={n} className="flex items-center justify-between gap-2"><span className="font-mono text-[11.5px] text-muted">{n}</span><ScoreBar v={v} /></div>)}
        </div>
      )}
      {!cases ? <p className="text-sm text-muted">Loading…</p> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-[13px]">
            <thead className="label"><tr className="border-b border-line">{["case", "k", "title", "score", "failure category"].map((h) => <th key={h} className="py-1.5 pr-3 font-normal">{h}</th>)}</tr></thead>
            <tbody>
              {cases.map((c) => (
                <tr key={c.id} onClick={() => onCase(runId, c.case_id)} className="cursor-pointer border-b border-line/60 hover:bg-raised/70">
                  <td className="py-1.5 pr-3 font-mono text-[12px]">{c.passed ? c.case_id : <span className="hl">{c.case_id}</span>}</td>
                  <td className="pr-3 font-mono text-[11px] text-muted">{c.run_idx + 1}</td>
                  <td className="max-w-[22rem] truncate pr-3">{c.title}</td>
                  <td className="pr-3"><ScoreBar v={c.score} /></td>
                  <td className="pr-3">{c.failure_category ? <CategoryChip cat={c.failure_category} cats={cats} primary /> : <span className="font-mono text-[11px] text-ok">pass</span>}
                    {(c.categories ?? []).length > 1 && <span className="ml-1.5 font-mono text-[10.5px] text-muted">+{(c.categories ?? []).length - 1}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Fig>
  );
}
