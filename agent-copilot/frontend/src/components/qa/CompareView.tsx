"use client";
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { f3, qa, runShort, type QaCategory, type QaCompare, type QaRun } from "@/lib/qa";
import { CategoryChip, Fig, INK, RED, ScoreBar, Select, tick, tooltipStyle } from "./parts";

export function CompareView({ runs, cats, a, b, setA, setB, onCase }: {
  runs: QaRun[]; cats: QaCategory[]; a: string; b: string; setA: (v: string) => void; setB: (v: string) => void; onCase: (run: string, cid: string) => void;
}) {
  const [margin, setMargin] = useState(0.05);
  const [res, setRes] = useState<QaCompare | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!a || !b) return;
    let on = true; setErr(null);
    qa.compare(a, b, margin).then((r) => on && setRes(r)).catch((e) => on && (setErr(String(e)), setRes(null)));
    return () => { on = false; };
  }, [a, b, margin]);
  const opts = [...runs].reverse().map((r) => ({ value: r.run_id, label: runShort(r) }));
  const order = { regressed: 0, improved: 1, unchanged: 2 } as const;
  const rows = res ? [...res.cases].sort((x, y) => order[x.status] - order[y.status] || x.delta - y.delta) : [];
  const nReg = rows.filter((r) => r.status === "regressed").length;
  const nImp = rows.filter((r) => r.status === "improved").length;
  return (
    <div className="flex flex-col gap-5">
      <Fig n={7} title="compare two runs (regression gate)" right={<>
        <Select label="baseline" value={a} onChange={setA} options={opts} />
        <Select label="candidate" value={b} onChange={setB} options={opts} />
        <label className="inline-flex items-center gap-1.5 normal-case tracking-normal"><span className="label">margin</span>
          <input type="number" min={0} max={0.5} step={0.01} value={margin} onChange={(e) => setMargin(Math.max(0, Number(e.target.value) || 0))}
            className="w-16 rounded-md border border-line bg-bg px-1.5 py-1 font-mono text-[12px]" /></label>
      </>}>
        {err && <p className="text-sm text-bad">{err}</p>}
        {!res ? <p className="text-sm text-muted">Pick a baseline and a candidate.</p> : (
          <>
            <div className={`mb-4 flex flex-wrap items-center gap-4 rounded-lg border p-3 ${res.regressed ? "border-bad/50 bg-bad/10" : "border-ok/40 bg-ok/10"}`}>
              <span className={`display text-2xl font-bold ${res.regressed ? "text-bad" : "text-ok"}`}>{res.regressed ? "REGRESSION" : "PASS"}</span>
              <span className="text-[13px]">{res.cases.length} common cases · <b className="text-bad">{nReg} regressed</b> · <b className="text-ok">{nImp} improved</b> · gate exits {res.regressed ? 1 : 0}</span>
              <code className="ml-auto hidden rounded bg-raised px-2 py-1 font-mono text-[11px] text-muted lg:block">python -m evals.qa.gate --baseline {res.baseline} --candidate {res.candidate} --margin {res.margin}</code>
            </div>
            <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
              <div>
                <table className="w-full text-left text-[13px]">
                  <thead className="label"><tr className="border-b border-line">{["metric", "baseline", "candidate", "Δ"].map((h) => <th key={h} className="py-1.5 pr-3 font-normal">{h}</th>)}</tr></thead>
                  <tbody>
                    {Object.entries(res.aggregate).map(([k, v]) => (
                      <tr key={k} className="border-b border-line/60">
                        <td className="py-1.5 pr-3 font-mono text-[12px]">{k.replace(/_/g, " ")}</td>
                        <td className="pr-3 font-mono">{f3(v.baseline)}</td><td className="pr-3 font-mono">{f3(v.candidate)}</td>
                        <td className={`pr-3 font-mono ${v.regressed ? "font-semibold text-bad" : (v.delta ?? 0) > 0 ? "text-ok" : "text-muted"}`}>
                          {v.regressed ? <span className="hl">{v.delta?.toFixed(3)}</span> : v.delta === null ? "–" : `${v.delta >= 0 ? "+" : ""}${v.delta.toFixed(3)}`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {(res.only_baseline.length > 0 || res.only_candidate.length > 0) && (
                  <p className="mt-2 text-[12px] text-muted">
                    {res.only_baseline.length > 0 && <>Only in baseline: <span className="font-mono">{res.only_baseline.join(", ")}</span>. </>}
                    {res.only_candidate.length > 0 && <>Only in candidate: <span className="font-mono">{res.only_candidate.join(", ")}</span>.</>}
                  </p>
                )}
              </div>
              <div className="h-[220px]">
                <div className="label mb-1">per-case Δ score (candidate − baseline)</div>
                <ResponsiveContainer width="100%" height="90%">
                  <BarChart data={[...res.cases].sort((x, y) => x.case_id.localeCompare(y.case_id))} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="rgb(var(--line))" strokeDasharray="2 4" vertical={false} />
                    <XAxis dataKey="case_id" tick={tick} axisLine={{ stroke: "rgb(var(--fg))" }} tickLine={false} interval={0} />
                    <YAxis domain={[-1, 1]} tick={tick} width={36} axisLine={false} tickLine={false} />
                    <ReferenceLine y={-res.margin} stroke="rgb(var(--muted))" strokeDasharray="3 3" />
                    <ReferenceLine y={0} stroke="rgb(var(--fg))" />
                    <Tooltip {...tooltipStyle} cursor={{ fill: "rgb(var(--muted) / .12)" }} formatter={(v) => [typeof v === "number" ? v.toFixed(3) : String(v), "Δ score"]} />
                    <Bar dataKey="delta" radius={[2, 2, 2, 2]} maxBarSize={22} cursor="pointer" onClick={(d) => { const p = (d as { payload?: { case_id?: string } }).payload; if (p?.case_id) onCase(res.candidate, p.case_id); }}>
                      {[...res.cases].sort((x, y) => x.case_id.localeCompare(y.case_id)).map((c) => (
                        <Cell key={c.case_id} fill={c.status === "regressed" ? RED : c.status === "improved" ? "rgb(var(--ok))" : INK} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </>
        )}
      </Fig>
      {res && (
        <Fig n={8} title="per case">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-[13px]">
              <thead className="label"><tr className="border-b border-line">{["case", "status", "baseline", "candidate", "Δ", "noise band", "why", "failure (candidate)"].map((h) => <th key={h} className="py-1.5 pr-3 font-normal">{h}</th>)}</tr></thead>
              <tbody>
                {rows.map((d) => (
                  <tr key={d.case_id} onClick={() => onCase(res.candidate, d.case_id)}
                    className={`cursor-pointer border-b border-line/60 hover:bg-raised/70 ${d.status === "regressed" ? "bg-bad/[.06]" : ""}`}>
                    <td className="py-1.5 pr-3 font-mono text-[12px]" title={d.title}>{d.status === "regressed" ? <span className="hl">{d.case_id}</span> : d.case_id}</td>
                    <td className={`pr-3 font-mono text-[11.5px] uppercase ${d.status === "regressed" ? "font-semibold text-bad" : d.status === "improved" ? "text-ok" : "text-muted"}`}>{d.status}</td>
                    <td className="pr-3"><ScoreBar v={d.base_score} /></td>
                    <td className="pr-3"><ScoreBar v={d.cand_score} /></td>
                    <td className={`pr-3 font-mono ${d.delta < 0 ? "text-bad" : d.delta > 0 ? "text-ok" : "text-muted"}`}>{d.delta >= 0 ? "+" : ""}{d.delta.toFixed(3)}</td>
                    <td className="pr-3 font-mono text-[11.5px] text-muted">±{d.threshold.toFixed(3)}</td>
                    <td className="max-w-[16rem] pr-3 text-[12px] text-muted">{d.reasons.join("; ")}</td>
                    <td className="pr-3">{d.cand_category ? <CategoryChip cat={d.cand_category} cats={cats} primary={d.status === "regressed"} /> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Fig>
      )}
    </div>
  );
}
