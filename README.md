# WellOps Copilot

**A chat copilot over oil-field asset data that refuses to give an answer it cannot prove.**

Ask it which wells are down, what a spill cost, or which inspections are overdue. It answers with numbers computed in SQL, picks its own chart, cites the records behind every figure, and tells you when the sources disagree. Every number in the reply is re-checked against the query result before it reaches the screen.

![Python](https://img.shields.io/badge/python-3.12-blue) ![Pydantic AI](https://img.shields.io/badge/agent-Pydantic%20AI-e92063) ![DuckDB](https://img.shields.io/badge/store-DuckDB-fff000) ![FastAPI](https://img.shields.io/badge/API-FastAPI%20%2B%20AG--UI-009688) ![Next.js](https://img.shields.io/badge/UI-Next.js%2016-black) ![Evals](https://img.shields.io/badge/evals-deterministic%20%2B%20Opik-6f42c1)

Built solo for FlytBase Hack Day (Asset Intelligence Copilot track), judged on backend engineering, evaluation system and generative UI. The dataset: 200 wells, about 40k inspections, 5.7k repairs, 1.5k free-text reports, plus manuals and SOPs.

## Why it is different

Most data copilots trust the model. This one treats the model as the least reliable part of the system.

| Problem | What WellOps does |
|---|---|
| Models invent numbers | **Claims-bound answers.** Each figure is a claim `{value, handle, column, row_key}` that is re-executed against its result. A mismatch sends a diff back to the model, at most twice, and otherwise no answer is emitted. |
| Data lies too | **Trust layer.** A data audit found 24 traps (stale status snapshots, hot wellheads that are idle, corrections stored as notes). Each becomes a data-quality flag that travels with the result. |
| Sources disagree | Conflicts found by tools must be surfaced in the answer. Hiding one fails a gate. |
| Users correct the data | Corrections are bitemporal overlay rows: attributed, undoable, applied for every user, and the original is never modified. |
| One user's chat leaks to another | Private history is filtered in SQL by the session principal, never by an argument the model controls. A leak zeroes the eval score. |
| The test set is unseen | No question-specific code. Thresholds come from rules extracted from manuals with a verbatim-quote check. |

## Architecture

```
L6  Evaluation     golden episodes · synthetic · mutated dataset · deterministic scorers · Opik experiments
L5  Generative UI  AG-UI stream → AnswerSpec visuals bound to result handles → React catalog · X-ray panel
L4  Verification   grounding · claim re-execution · disclosure · conflicts · visual fit → retry ≤ 2
L3  Agent          Pydantic AI · 9 skills loaded on demand · fallback model chain
L2  Tools          well_status · downtime · spills · obligations · rca · repair_costs · evidence_chain · sql · docs · memory
L1  Trust          provenance on every fact · data-quality flags · overlays · rules from manuals
L0  Substrate      one DuckDB file: raw tables · canonical views · report facts · full-text search
```

Each layer talks to the next through a typed contract (`backend/src/wellops/contracts`, mirrored in `frontend/src/contracts.ts`), so a layer can be replaced without rewiring the ones above it.

Large results never pass through the model: tools return a preview plus a **handle**, and the UI fetches full rows by handle.

## Evaluation system

The evals are their own project (`agent-copilot/evals/`), not an afterthought.

- **18 golden episodes**: multi-turn, multi-user, with a simulated clock. One user corrects a record, another re-asks, day 1 rolls into day 2.
- **72 synthetic cases** whose truth is computed from the data, not written by hand.
- **Deterministic scorers**, no LLM judge on facts: facts, evidence precision and recall, visual type, conflict surfaced, overlay disclosed, memory writes, tool trajectory, and a cross-user leak probe.
- **Eval of the evals**: a fault-injection run corrupts correct answers in 10 ways. Every scorer catches 100% of its faults with 0 false alarms on clean answers.
- **Answer-key audit**: each expected answer is recomputed from the data instead of trusted. 199 agree, 0 disagree.
- **Mutated dataset rehearsal**: 12 new wells, a new well type with its own manual, 25 renamed files, a 28-day time shift. It found two real bugs (a new well type silently dropped from status views; duplicate inspection IDs), both fixed and kept as regression checks.

## Results, stated plainly

| Check | Result |
|---|---|
| Golden episodes, offline pipeline | 18 / 18 pass |
| Scorer recall under fault injection | 1.00 on every scorer, 0 false alarms |
| Answer-key audit | 199 agree, 0 disagree |
| Live run on a 2B local model | Too small to score. The two cases that ran produced **no answer**: the verifier rejected all three attempts rather than let an ungrounded one through |

That last row is the design working as intended, and also its cost: a very small model cannot satisfy the claim format within two retries. Live scoring was limited by free-tier model quota (about 50 requests a day).

## Run it

Backend (Python 3.12, [uv](https://docs.astral.sh/uv/)):

```bash
cd agent-copilot/backend && cp ../.env.example ../.env && uv run uvicorn wellops.api.app:app --port 8000
```

Frontend:

```bash
cd agent-copilot/frontend && npm install && npm run dev
```

Open `http://localhost:3000`. With no model key or quota, `npm run dev:mock` replays real tool outputs that passed every verifier gate.

Tests and evals:

```bash
cd agent-copilot/backend && uv run pytest -q
```

```bash
cd agent-copilot/evals && uv run python -m evals.run --target fake --suite golden
```

The hackathon dataset is not included in this repo. Point `DATA_DIR` at it in `.env`.

## Read the thinking

| Document | What is in it |
|---|---|
| [docs/WRITEUP.md](agent-copilot/docs/WRITEUP.md) | The full write-up: architecture, trust layer, memory, evals, limits |
| [DECISIONS.md](agent-copilot/DECISIONS.md) | 21 architecture decision records with alternatives considered |
| [AUDIT.md](agent-copilot/AUDIT.md) | A critical review of my own v2 plan: 18 bugs found on paper, and every decision re-argued |
| [TRADEOFFS.md](agent-copilot/TRADEOFFS.md) | What each choice costs, and the signal that would make me revisit it |
| [DATA.md](agent-copilot/DATA.md) | Data map and the 24 traps |
| [docs/HARNESS.md](agent-copilot/docs/HARNESS.md) | How the eval harness is structured |

## Honest limits

- The capability registry, admission pipeline and prompt optimizer in the plan were cut for time. Skills are static folders.
- The backend does not yet export live traces to Opik; traces currently come from eval runs.
- Live evaluation covers small slices only. The deterministic pipeline is fully tested offline.
