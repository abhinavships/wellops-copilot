"use client";
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { qa, runShort, type QaCategory, type QaRun, type TaxonomyCounts } from "@/lib/qa";
import { AMBER, CAT_ABBR, Fig, INK, Seg, Select, tick, tooltipStyle } from "./parts";

export function TaxonomyView({ runs, cats, initialRun, onCase }: { runs: QaRun[]; cats: QaCategory[]; initialRun: string | null; onCase: (run: string, cid: string) => void }) {
  const [run, setRun] = useState<string>(initialRun ?? "");
  const [level, setLevel] = useState<"case" | "turn">("case");
  const [counts, setCounts] = useState<TaxonomyCounts | null>(null);
  useEffect(() => { let on = true; qa.taxonomy(run || null, level).then((t) => on && setCounts(t.counts)).catch(() => on && setCounts(null)); return () => { on = false; }; }, [run, level, runs.length]);
  const data = cats.filter((c) => c.key !== "other" || counts?.any.other)
    .map((c) => ({ key: c.key, label: c.label, primary: counts?.primary[c.key] ?? 0, any: counts?.any[c.key] ?? 0 }));
  const top = [...data].sort((a, b) => b.primary - a.primary || b.any - a.any)[0];
  return (
    <div className="flex flex-col gap-5">
      <Fig n={5} title="failure taxonomy" right={<>
        <Select label="run" value={run} onChange={setRun} options={[{ value: "", label: "all runs" }, ...[...runs].reverse().map((r) => ({ value: r.run_id, label: runShort(r) }))]} />
        <Seg value={level} onChange={setLevel} options={[{ value: "case", label: "per case" }, { value: "turn", label: "per turn" }]} />
      </>}>
        {!counts ? <p className="text-sm text-muted">Loading…</p> : counts.failed === 0 ? <p className="text-sm text-muted">No failures in this selection.</p> : (
          <>
            <p className="mb-3 text-[13px]">
              {counts.failed} failed {level === "case" ? "case run" : "turn"}{counts.failed === 1 ? "" : "s"}
              {top && top.primary > 0 && <>; most common root cause: <span className="hl font-semibold">{top.label}</span> ({top.primary})</>}.
            </p>
            <div style={{ height: Math.max(260, data.length * 30) }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 0 }} barGap={2}>
                  <CartesianGrid stroke="rgb(var(--line))" strokeDasharray="2 4" horizontal={false} />
                  <XAxis type="number" allowDecimals={false} tick={tick} axisLine={{ stroke: "rgb(var(--fg))" }} tickLine={false} />
                  <YAxis type="category" dataKey="label" width={170} tick={{ ...tick, fill: "rgb(var(--fg))" }} axisLine={false} tickLine={false} />
                  <Tooltip {...tooltipStyle} cursor={{ fill: "rgb(var(--muted) / .12)" }} formatter={(v, n) => [String(v), n === "primary" ? "primary (root cause)" : "any (co-occurring)"]} />
                  <Legend wrapperStyle={{ fontSize: 12, fontFamily: "var(--font-mono)" }} formatter={(v: string) => (v === "primary" ? "primary (root cause)" : "any (co-occurring)")} />
                  <Bar dataKey="primary" fill={INK} radius={[0, 3, 3, 0]} maxBarSize={14} />
                  <Bar dataKey="any" fill={AMBER} radius={[0, 3, 3, 0]} maxBarSize={14} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </>
        )}
      </Fig>
      <Fig n={6} title="categories (deterministic, in priority order)">
        <ol className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
          {cats.map((c) => (
            <li key={c.key} className="flex gap-2 text-[13px]">
              <span className="w-12 shrink-0 font-mono text-[11px] text-muted">{c.priority + 1}. {CAT_ABBR[c.key]}</span>
              <span><b className="font-semibold">{c.label}</b> <span className="text-muted">— {c.description}</span>
                {counts?.examples[c.key]?.length ? (
                  <span className="ml-1 inline-flex flex-wrap gap-1 align-middle">
                    {counts.examples[c.key].filter((e, i, xs) => xs.findIndex((y) => y.case_id === e.case_id) === i).slice(0, 6).map((e, i) => (
                      <button key={i} type="button" onClick={() => onCase(e.run_id, e.case_id)} className="rounded border border-line px-1 font-mono text-[10.5px] hover:bg-raised">{e.case_id}</button>
                    ))}
                  </span>
                ) : null}
              </span>
            </li>
          ))}
        </ol>
        <p className="mt-3 text-[11.5px] text-muted">Classified from scorer details, tool-call arguments and the verifier events in each trace. No LLM judge. The primary category is the highest-priority hit: a wrong date window explains the wrong numbers it produced.</p>
      </Fig>
    </div>
  );
}
