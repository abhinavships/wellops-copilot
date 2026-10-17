# QA system: continuous evaluation, failure taxonomy, regression gate, explorer

The eval runner (`evals/evals/run.py`) produces run directories. The QA layer (`evals/evals/qa/`) indexes them in a
SQLite store, classifies every failure deterministically, gates regressions between two runs and serves a read-only
API that the explorer page (`frontend/src/app/qa`) renders.

## Architecture

```
 golden / synthetic cases (YAML)                       frontend  /qa  (Next.js, paper aesthetic)
            │                                           ┌──────────────────────────────────────────┐
            ▼                                           │ Runs: trend chart + run table + cases    │
  evals.run  --target fake|api|hermes                   │ Case × run matrix (heat cells)           │
   │  episode → target.respond → scorers (10)           │ Case drill-down: turns, answer, tool     │
   │  writes results/<run>/partial.jsonl (per case)     │   trace, scorer details, failure category│
   │         results/<run>/results.json  (+ meta:       │ Taxonomy bar chart                       │
   │           git_sha, model, started/finished)        │ Compare two runs (regressions in red)    │
   │  then ingests the run ──────────────┐              └───────────────▲──────────────────────────┘
   ▼                                     ▼                              │ GET (NEXT_PUBLIC_QA_BASE)
 results/<run>/  ──── import (fingerprint: size+mtime, idempotent) ──▶ evals.qa.api  (FastAPI, :8020, read-only)
                                         │                              │  /qa/runs  /qa/runs/{id}
                                         ▼                              │  /qa/runs/{id}/cases/{case}
                          evals.qa.store  (SQLite: results/qa.sqlite)   │  /qa/cases/{id}/history  /qa/matrix
                          runs ─< case_results ─< turns ─< scores       │  /qa/compare?a=&b=  /qa/taxonomy
                          (model, harness, git sha, data_version,  ◀────┘
                           latency, tokens, retries, failure category)
                                         │
                       evals.qa.taxonomy │ classify_turn(scores, trace, verifier events, truth window)
                                         ▼
                          evals.qa.gate  --baseline A --candidate B --margin 0.05
                          per-case + aggregate deltas, noise band, markdown/JSON report, exit 0 / 1 / 2
```

The run directory stays the source of truth. The store is an index that can be rebuilt with
`python -m evals.qa.store import --force`. A run that is still being written (only `partial.jsonl`) is indexed case by
case: the API rescans every 5 s, the explorer polls every 15 s.

### Store schema (`evals/evals/qa/store.py`)

| table | one row per | key columns |
|---|---|---|
| `runs` | eval run | target, suite, k, model, harness, git_sha (+ `git_sha_inferred`), data_version, mean_score, pass_rate, pass^k, pass@k, leaks, latency, tokens, verifier retries, partial, per-scorer means, meta-eval |
| `case_results` | case × repeat (k) | score, passed, leak, `failure_category` (primary), categories, latency, tokens, error |
| `turns` | turn | user, chat, clock, question, setup flag, score, latency, tokens, retries, model, error, taxonomy (with evidence), expectation, answer JSON, tool trace JSON |
| `scores` | turn × scorer | value (NULL = not applicable), reason, details JSON |

Model and harness come from the API's `wellops.usage` / `wellops.verification` events (`hermes-agent → gemini-3.8-flash`).
New runs record `git_sha` and the target model in `results.json → meta`. For older runs the sha is inferred from the last
commit before the run started (flagged with `*` in the UI), and an optional `results/<run>/meta.json` can annotate the model.
`data_version` is read from the answer's `dataset=` assumption.

### Failure taxonomy (`evals/evals/qa/taxonomy.py`)

Only failed turns are classified, and no LLM is involved. Each hit stores the evidence that triggered it, which the drill-down
shows. Categories are listed in priority order; the first one that hits is the **primary** category (the root cause).

| # | category | derived from |
|---|---|---|
| 1 | run error / timeout | turn `error`, `run_error` without an answer event; `timeout` flag from the message |
| 2 | memory leak across users | `leak` scorer = 0 (strings of another principal in the answer) |
| 3 | ungrounded claim | a failing verifier gate in `wellops.verification`, "maximum output retries" (verifier rejected every attempt), or numbers in the markdown that appear in no tool result, the question or the key (skipped when the bridge truncated a result at 2000 chars, and for small integers) |
| 4 | wrong window | the start/end args of windowed tool calls vs the window implied by the case's `truth` args (`since/until`, `days`, `weeks`, `months`, `years`, `year`), tolerance max(3 d, 5 %) |
| 5 | wrong number | `facts.numbers_missing` |
| 6 | correction not applied | `memory` scorer = 0, or an overlay was expected but `applied_overlays` is empty |
| 7 | conflict not shown | `conflict` < 1 or `unconfirmed` = 0 |
| 8 | invented history | `no_prior` = 0 |
| 9 | missing entity | `facts` ids / set members / dates / phrases missing, or extra set members |
| 10 | correction not disclosed | overlay expected and its author not named in the answer |
| 11 | wrong visual | `visual` = 0 |
| 12 | missing evidence | `evidence` must-cite missing or off-target citations |
| 13 | wrong tool path | `trajectory` < 1 (forbidden `downtime_hours`, step budget, expected tool unused) |
| 14 | other | failed with none of the above |

`tests/test_qa.py::test_every_injected_fault_lands_in_its_category` is the meta-eval for the taxonomy. It injects the same
15 faults as the scorer meta-eval into correct answers and asserts each failed turn lands in the expected category. A
second test asserts that clean answers get no category.

### Regression gate (`evals/evals/qa/gate.py`)

Only cases present in both runs are compared. A case **regresses** when any of these holds:

- a cross-principal leak appears (hard fail, no margin);
- its mean score drops by more than the case threshold;
- its pass rate over the k repeats drops by more than the threshold (with k = 1, pass → fail counts in full).

The case threshold is `margin` (default 0.05), widened to 2 × the standard error of the per-repeat scores when k > 1. A
flaky case with a wide spread therefore has to move further before it counts. In aggregate, mean score, pass rate and
pass^k regress when they drop by more than `margin`. Cases missing from the candidate are listed, and they fail the gate
only with `--require-all`. Exit codes: `0` pass, `1` regression, `2` unknown run or no common cases.

## How to run

```bash
cd agent-copilot/evals
# 1. produce runs (each run is indexed into results/qa.sqlite automatically; --no-qa-store to skip)
uv run python -m evals.run --target fake --suite golden          # offline harness check
uv run python -m evals.run --target api  --suite golden --k 3    # live AG-UI endpoint
# 2. (re)index existing run directories, e.g. results copied from another checkout
uv run python -m evals.qa.store import                       # default: evals/results
uv run python -m evals.qa.store list
# 3. gate a candidate against a baseline
uv run python -m evals.qa.gate --baseline latest:fake --candidate latest:api --margin 0.05 --out gate.md
uv run python -m evals.qa.gate --baseline 20261017-143700 --candidate results/20261017-144933-api-golden  # dirs import on the fly
# 4. explorer: API + page
uv run uvicorn evals.qa.api:app --port 8020                  # QA_RESULTS_DIRS=/other/results:... to index more dirs
cd ../frontend && npm run dev                                # open http://localhost:3000/qa (NEXT_PUBLIC_QA_BASE overrides :8020)
```

`QA_DB` moves the store, and `QA_RESULTS_DIRS` (separated by `:`) points the API at other result directories. The API
only reads them. It writes nothing except its own SQLite file.

## The gate in CI

The baseline is the eval run of the last green `main` build, kept as a CI artifact. Each PR runs the same suite and gates
on the result:

```yaml
# .github/workflows/evals.yml (sketch)
jobs:
  evals:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: baseline from main
        uses: dawidd6/action-download-artifact@v6
        with: { workflow: evals.yml, branch: main, name: eval-baseline, path: agent-copilot/evals/baseline }
      - name: run the suite (offline gate always, live gate when the model key is present)
        working-directory: agent-copilot/evals
        run: |
          uv run python -m evals.run --target fake --suite all --k 1
          [ -n "$GEMINI_API_KEY" ] && uv run python -m evals.run --target api --suite golden --k 3 || true
      - name: regression gate
        working-directory: agent-copilot/evals
        run: |
          uv run python -m evals.qa.gate --baseline baseline/$(ls baseline | tail -1) \
            --candidate latest --margin 0.05 --out gate.md --json gate.json
      - if: always()
        run: cat agent-copilot/evals/gate.md >> "$GITHUB_STEP_SUMMARY"
      - if: github.ref == 'refs/heads/main' && success()
        uses: actions/upload-artifact@v4
        with: { name: eval-baseline, path: agent-copilot/evals/results/2026* }
```

- The **offline** (`fake`) gate is deterministic, so any drop means the harness, the scorers or the case files broke. Run it
  with margin 0.
- The **live** gate uses k ≥ 3 so the per-case noise band reflects real variance. Margin 0.05 absorbs small score jitter
  but never a pass turning into a fail or a new leak.
- The markdown report goes into the job summary: aggregate table, the reasons it failed, and per-case deltas with the
  candidate's failure category. A reviewer can then open the same comparison in the explorer (`/qa#tab=compare`).

## What the explorer shows with the current real data (17 Oct)

Eight runs are indexed: three `fake` (harness ceiling, all 1.000), the early `gemma4-e2b` API slice (0/2: verifier retries
exhausted, classified *run error + ungrounded claim*), two Hermes/Nemotron Q01 probes, and two full Hermes + Gemini
3.8 Flash golden runs (`20261017-143700`: 18 cases, mean 0.516, 1/18 pass; `20261017-144933`, still running at import
time: mean 0.667 over 14 cases).

- **Taxonomy:** the most common root cause is **missing entity** (mostly the answer leaving out the inspector who
  disagrees, "out of date", or specific event dates), followed by run errors (Q15–Q18 in run 143700: a Hermes
  `ModuleNotFoundError`, then the API dropping the SSE connection), wrong numbers (Q08 heatmap cells, Q13/Q14), and
  missing evidence (Q03/Q06 cite no inspection or well).
- **Drill-down Q12:** the verifier's `disclosure` and `conflicts` gates still failed after 2 retries. The overlay is
  described in the markdown but `applied_overlays` is empty, no memory write happened, and the visual was `dispute`, which the case does not allow.
- **Compare 143700 → 144933:** gate verdict REGRESSION on Q14 (0.472 → 0.333). Q13 improved (+0.229). Every other
  common case is inside the noise band.
