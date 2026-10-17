"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { QA_BASE, pct, qa, runShort, type QaCategory, type QaRun } from "@/lib/qa";
import { CaseDrawer } from "./CaseDrawer";
import { CompareView } from "./CompareView";
import { MatrixView } from "./MatrixView";
import { RunsView } from "./RunsView";
import { TaxonomyView } from "./TaxonomyView";

type Tab = "runs" | "matrix" | "taxonomy" | "compare";
const TABS: { key: Tab; label: string }[] = [{ key: "runs", label: "Runs" }, { key: "matrix", label: "Case × run" }, { key: "taxonomy", label: "Taxonomy" }, { key: "compare", label: "Compare" }];

/** QA explorer: eval runs from the SQLite results store, served by evals.qa.api. Read-only. */
export function QaExplorer() {
  const [runs, setRuns] = useState<QaRun[] | null>(null);
  const [cats, setCats] = useState<QaCategory[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("runs");
  const [selected, setSelected] = useState<string | null>(null);
  const [drill, setDrill] = useState<{ run: string; cid: string } | null>(null);
  const [a, setA] = useState("");
  const [b, setB] = useState("");

  const load = useCallback(() => {
    qa.runs().then((rs) => {
      setRuns(rs); setErr(null);
      const live = rs.filter((r) => r.target !== "fake");
      const fake = rs.filter((r) => r.target === "fake" && r.suite !== "all");
      setSelected((s) => s ?? (live.at(-1) ?? rs.at(-1))?.run_id ?? null);
      // default comparison: the previous live run against the latest one (else the harness ceiling, a fake run)
      setA((x) => x || (live.length > 1 ? live.at(-2) : fake.at(-1) ?? rs[0])?.run_id || "");
      setB((x) => x || (live.at(-1) ?? rs.at(-1))?.run_id || "");
    }).catch((e) => setErr(String(e)));
  }, []);
  useEffect(() => {
    load();
    qa.taxonomy(null, "case").then((t) => setCats(t.categories)).catch(() => undefined);
    const id = window.setInterval(load, 15000); // a run in progress appears case by case
    return () => window.clearInterval(id);
  }, [load]);
  // deep link: /qa#tab=compare
  useEffect(() => { const m = /tab=(\w+)/.exec(window.location.hash); if (m && TABS.some((t) => t.key === m[1])) setTab(m[1] as Tab); }, []);
  const go = (t: Tab) => { setTab(t); window.history.replaceState(null, "", `#tab=${t}`); };
  const onCase = (run: string, cid: string) => setDrill({ run, cid });

  const latest = runs?.filter((r) => r.target !== "fake").at(-1);
  return (
    <div className="paper min-h-dvh">
      <header className="sticky top-0 z-20 border-b border-line/70 bg-bg/70 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3 sm:px-6">
          <h1 className="label"><span className="text-fg">WellOps</span> · QA notebook</h1>
          <nav className="flex gap-1" aria-label="Views">
            {TABS.map((t) => (
              <button key={t.key} type="button" onClick={() => go(t.key)} aria-current={tab === t.key ? "page" : undefined}
                className={`rounded-full px-3 py-1 text-[13px] ${tab === t.key ? "bg-fg text-bg" : "text-muted hover:bg-raised hover:text-fg"}`}>{t.label}</button>
            ))}
          </nav>
          <span className="flex-1" />
          <Link href="/" className="text-[13px] text-muted hover:text-fg">← copilot</Link>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <div className="mb-6">
          <h2 className="display text-3xl font-bold sm:text-4xl">Continuous evaluation</h2>
          <p className="mt-1 max-w-3xl text-[14px] text-muted">
            Every eval run indexed with its model, harness, git sha and data version; each failed case put in a <span className="hl text-fg">deterministic failure category</span>;
            any two runs compared under the same noise-margin rule the CI gate uses.
            {latest && <> Latest live run: <b className="font-mono text-fg">{runShort(latest)}</b>, {latest.n_cases} cases, pass {pct(latest.pass_rate)}, mean {latest.mean_score?.toFixed(3)}{latest.partial ? " (in progress or partial)" : ""}.</>}
          </p>
        </div>
        {err && (
          <div className="rounded-xl border border-bad/40 bg-bad/10 p-4 text-[14px]">
            <p className="font-semibold text-bad">Cannot reach the QA API at {QA_BASE}.</p>
            <p className="mt-1 text-muted">Start it with <code className="rounded bg-raised px-1 font-mono">cd agent-copilot/evals &amp;&amp; uv run uvicorn evals.qa.api:app --port 8020</code>, or set NEXT_PUBLIC_QA_BASE. ({err})</p>
          </div>
        )}
        {runs && !runs.length && <p className="text-muted">No runs indexed yet: run <code className="font-mono">uv run python -m evals.run</code> or <code className="font-mono">python -m evals.qa.store import</code>.</p>}
        {runs && runs.length > 0 && (
          <>
            {tab === "runs" && <RunsView runs={runs} cats={cats} selected={selected} onSelect={setSelected} onCase={onCase} />}
            {tab === "matrix" && <MatrixView runs={runs} cats={cats} onCase={onCase} />}
            {tab === "taxonomy" && <TaxonomyView runs={runs} cats={cats} initialRun={null} onCase={onCase} />}
            {tab === "compare" && <CompareView runs={runs} cats={cats} a={a} b={b} setA={setA} setB={setB} onCase={onCase} />}
          </>
        )}
      </main>
      {drill && <CaseDrawer runId={drill.run} caseId={drill.cid} cats={cats} onClose={() => setDrill(null)}
        onRun={(r) => setDrill((d) => (d ? { ...d, run: r } : d))} />}
    </div>
  );
}
