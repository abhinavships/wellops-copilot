"use client";
import { useEffect, useState } from "react";
import { qa, runStamp, type QaCategory, type QaMatrix, type QaRun } from "@/lib/qa";
import { CAT_ABBR, Fig, Seg, heat } from "./parts";

export function MatrixView({ runs, cats, onCase }: { runs: QaRun[]; cats: QaCategory[]; onCase: (run: string, cid: string) => void }) {
  const [suite, setSuite] = useState<"golden" | "synthetic">("golden");
  const [target, setTarget] = useState<string>("");
  const [m, setM] = useState<QaMatrix | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { let on = true; qa.matrix(suite, target).then((x) => on && setM(x)).catch((e) => on && setErr(String(e))); return () => { on = false; }; }, [suite, target, runs.length]);
  const byId = Object.fromEntries(runs.map((r) => [r.run_id, r]));
  const targets = Array.from(new Set(runs.map((r) => r.target ?? "?")));
  return (
    <Fig n={4} title="case × run matrix" right={<>
      <Seg value={suite} onChange={setSuite} options={[{ value: "golden", label: "golden" }, { value: "synthetic", label: "synthetic" }]} />
      <Seg value={target} onChange={setTarget} options={[{ value: "", label: "all" }, ...targets.map((t) => ({ value: t, label: t }))]} />
    </>}>
      {err && <p className="text-sm text-bad">{err}</p>}
      {!m ? <p className="text-sm text-muted">Loading…</p> : !m.cases.length ? <p className="text-sm text-muted">No runs for this filter.</p> : (
        <>
          <div className="overflow-auto">
            <table className="border-separate border-spacing-[3px] text-[12px]">
              <thead>
                <tr>
                  <th className="label sticky left-0 z-10 bg-panel pr-2 text-left font-normal">case</th>
                  {m.runs.map((rid) => (
                    <th key={rid} className="h-28 w-12 align-bottom font-normal" title={rid}>
                      <div className="mx-auto w-4 origin-bottom-left translate-x-3 -rotate-[60deg] whitespace-nowrap font-mono text-[10.5px] text-muted">
                        {runStamp(rid)} <span className="text-fg">{byId[rid]?.target}</span>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {m.cases.map((c) => (
                  <tr key={c.case_id}>
                    <td className="sticky left-0 z-10 max-w-[15rem] truncate bg-panel pr-2" title={c.title}>
                      <span className="font-mono text-fg">{c.case_id}</span> <span className="text-muted">{c.title}</span>
                    </td>
                    {m.runs.map((rid) => {
                      const cell = m.cells[c.case_id]?.[rid];
                      if (!cell) return <td key={rid} className="h-8 w-12 rounded border border-dashed border-line/70" />;
                      const pass = cell.pass_rate >= 0.999;
                      const h = heat(cell.score, pass);
                      const cat = cell.failure_category;
                      const label = cats.find((x) => x.key === cat)?.label;
                      return (
                        <td key={rid} className="p-0">
                          <button type="button" onClick={() => onCase(rid, c.case_id)}
                            title={`${c.case_id} · ${rid}\nscore ${cell.score.toFixed(3)} · pass ${Math.round(cell.pass_rate * 100)}% over k=${cell.k}${label ? `\n${label}` : ""}`}
                            className={`flex h-8 w-12 flex-col items-center justify-center rounded font-mono leading-none transition hover:scale-110 hover:ring-2 hover:ring-fg ${cell.leak ? "ring-2 ring-bad" : ""}`}
                            style={{ background: h.bg, color: h.fg }}>
                            <span className="text-[11px]">{cell.score.toFixed(2).replace(/^0/, "")}</span>
                            {cat && <span className="mt-0.5 text-[8.5px] opacity-80">{CAT_ABBR[cat] ?? "?"}</span>}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-3 text-[11.5px] text-muted">
            <span className="flex items-center gap-1"><span className="h-3 w-5 rounded" style={{ background: heat(1, true).bg }} /> passes</span>
            <span className="flex items-center gap-1"><span className="h-3 w-5 rounded" style={{ background: heat(0.8, false).bg }} /> partial credit</span>
            <span className="flex items-center gap-1"><span className="h-3 w-5 rounded" style={{ background: heat(0.1, false).bg }} /> mostly fails</span>
            <span className="flex items-center gap-1"><span className="h-3 w-5 rounded border border-dashed border-line" /> not run</span>
            <span>· cell = mean aggregate over k runs; the code under it is the primary failure category. Click a cell to drill in.</span>
          </div>
        </>
      )}
    </Fig>
  );
}
