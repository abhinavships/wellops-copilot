/** Client for the read-only QA explorer API (agent-copilot/evals/evals/qa/api.py). */
export const QA_BASE = (process.env.NEXT_PUBLIC_QA_BASE ?? "http://localhost:8020").replace(/\/$/, "");

export interface QaRun {
  run_id: string; started_at: string | null; target: string | null; suite: string | null; k: number | null;
  model: string | null; harness: string | null; git_sha: string | null; git_sha_inferred: number; data_version: string | null;
  n_cases: number; n_case_runs: number; mean_score: number | null; pass_rate: number | null; pass_hat_k: number | null;
  pass_at_k: number | null; leaks: number; latency_mean_s: number | null; latency_total_s: number | null;
  tokens_total: number | null; retries_total: number | null; partial: number; interrupted: string | null;
  per_scorer: Record<string, number> | null; meta_eval: unknown;
}
export interface QaCaseResult {
  id: number; run_id: string; case_id: string; run_idx: number; suite: string; title: string; score: number; passed: number;
  leak: number; failure_category: string | null; categories: string[] | null; latency_s: number | null; tokens: number | null;
  n_turns: number; error: string | null;
}
export interface QaCategory { key: string; label: string; description: string; priority: number }
export interface TaxonomyCounts { level: string; run_id: string | null; failed: number; primary: Record<string, number>; any: Record<string, number>; examples: Record<string, { run_id: string; case_id: string }[]> }
export interface ScoreRec { value: number | null; reason: string; details: Record<string, unknown> }
export interface TraceCall { name: string; args: unknown; result: unknown; error?: boolean }
export interface TaxHit { category: string; evidence: Record<string, unknown> }
export interface QaTurn {
  id: number; turn_idx: number; turn_id: string; user: string; chat: string; clock: string; say: string; setup: number;
  score: number | null; passed: number | null; leak: number; latency_s: number | null; tokens: number | null; retries: number | null;
  model: string | null; error: string | null; failure_category: string | null;
  taxonomy: { primary: string | null; categories: string[]; hits: TaxHit[] } | null;
  expect: Record<string, unknown> | null; answer: QaAnswer | null; trace: TraceCall[] | null; scores: Record<string, ScoreRec>;
}
export interface QaAnswer {
  markdown?: string; visuals?: { type?: string; title?: string; handle?: string }[]; citations?: { kind?: string; id?: string }[];
  conflicts?: unknown[]; applied_overlays?: string[]; assumptions?: string[]; _meta?: Record<string, unknown>;
}
export interface QaCaseDetail { run_id: string; case_id: string; title: string; runs: (QaCaseResult & { turns: QaTurn[] })[] }
export interface QaHistoryRow { run_id: string; run_idx: number; score: number; passed: number; leak: number; failure_category: string | null; categories: string[]; latency_s: number | null; started_at: string; target: string; model: string | null; git_sha: string | null }
export interface QaMatrix { runs: string[]; cases: { case_id: string; title: string }[]; cells: Record<string, Record<string, { score: number; pass_rate: number; leak: boolean; failure_category: string | null; k: number }>> }
export interface CaseDelta { case_id: string; title: string; base_score: number; cand_score: number; base_pass: number; cand_pass: number; threshold: number; status: "regressed" | "improved" | "unchanged"; reasons: string[]; base_category: string | null; cand_category: string | null; delta: number }
export interface QaCompare {
  baseline: string; candidate: string; margin: number; cases: CaseDelta[]; regressed: boolean; reasons: string[];
  aggregate: Record<string, { baseline: number | null; candidate: number | null; delta: number | null; regressed: boolean }>;
  only_baseline: string[]; only_candidate: string[]; report_md: string; runs: Record<string, QaRun>;
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${QA_BASE}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return (await r.json()) as T;
}
const q = encodeURIComponent;

export const qa = {
  runs: () => get<{ runs: QaRun[] }>("/qa/runs").then((j) => j.runs),
  run: (id: string) => get<QaRun & { cases: QaCaseResult[]; taxonomy: TaxonomyCounts }>(`/qa/runs/${q(id)}`),
  caseDetail: (run: string, cid: string) => get<QaCaseDetail>(`/qa/runs/${q(run)}/cases/${q(cid)}`),
  history: (cid: string) => get<{ history: QaHistoryRow[] }>(`/qa/cases/${q(cid)}/history`).then((j) => j.history),
  matrix: (suite: string, target: string) => get<QaMatrix>(`/qa/matrix?suite=${q(suite)}&target=${q(target)}&limit=40`),
  compare: (a: string, b: string, margin: number) => get<QaCompare>(`/qa/compare?a=${q(a)}&b=${q(b)}&margin=${margin}`),
  taxonomy: (run: string | null, level: "case" | "turn") =>
    get<{ categories: QaCategory[]; counts: TaxonomyCounts }>(`/qa/taxonomy?level=${level}${run ? `&run=${q(run)}` : ""}`),
};

/** "20261017-143700-api-golden" → "17 Oct 14:37" */
export function runStamp(id: string): string {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(id);
  if (!m) return id;
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][+m[2] - 1];
  return `${+m[3]} ${mon} ${m[4]}:${m[5]}`;
}
export const runShort = (r: Pick<QaRun, "run_id" | "target">) => `${runStamp(r.run_id)} · ${r.target ?? "?"}`;
export const pct = (v: number | null | undefined) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`);
export const f3 = (v: number | null | undefined) => (v === null || v === undefined ? "–" : v.toFixed(3));
export const isLive = (r: Pick<QaRun, "target">) => r.target !== "fake";
