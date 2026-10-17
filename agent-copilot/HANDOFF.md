# HANDOFF — resume here

> Written so another Claude session or account can continue without this conversation. Update the
> "Status" table and the "Next actions" list whenever you commit.
> Last updated: 2026-10-17 (session 2). Note: Ollama on 192.168.10.222 answered `/api/tags` with **no models**: recreate `qwen3-vl-8b-32k` before live runs.

## 1. What this is
FlytBase Hack Day "Asset Intelligence Copilot": a chat copilot over an oil-field dataset with
generative UI, grounded answers, two-level memory, conflict honesty, and robustness to an unseen test set.
Judged on **backend engineering, evaluation system, generative UI**. Read in this order:
1. `PLAN.md` (v4, layers L0–L6 + hour schedule)
2. `DATA.md` (data map, 24 traps, answer-key audit)
3. `DECISIONS.md` (ADRs; latest ones are ADR-020 trust layer and ADR-021 Opik)
4. `TRADEOFFS.md`
5. `AUDIT.md` (why v2 → v3: the bugs that would have failed specific questions)

Problem statement and dataset: `dataset/` (git-ignored clone of
github.com/FlytBaseAILabs/flytbase-ahc-swe-asset-intelligence-dataset). Re-clone it if missing:
`git clone --depth 1 <url> agent-copilot/dataset`.

## 2. Environment (not in git — recreate)
- `agent-copilot/.env` (git-ignored). Copy `.env.example` and fill in:
  - `OPENROUTER_API_KEY`: the user's key. The account has **$0 credits**, so only `:free` models work (~50 requests/day).
  - `OPIK_API_KEY` + `OPIK_WORKSPACE=abhinav-patil` (Comet cloud).
  - Ask the user for the keys; they are **not** committed anywhere.
- **Local GPU model (unlimited, no key):** LAN box `rtx5090@192.168.10.222` (RTX 5090 32 GB, Ubuntu, Ollama 0.32.9
  listening on `0.0.0.0:11434`). Ask the user for the SSH password. The SSH isn't needed for inference, only for
  managing models.
  - OpenAI-compatible base: `http://192.168.10.222:11434/v1`.
  - Models there: `gemma4-e2b-32k` (created from gemma4:e2b with num_ctx 32768, tool calling verified) and
    `qwen3-vl-8b-32k` (created from qwen3-vl:8b-instruct; **user asked to use the Qwen setup**: verify tool calling,
    then set `MODEL_LOCAL=ollama:qwen3-vl-8b-32k` and put it first in `MODEL_FALLBACKS`).
  - Also present: `qwen2.5-coder:7b-instruct`, `qwen3-vl:4b`. The user said **don't pull 17 GB models**. Disk has ~28 GB free.
  - Create a context variant with: `curl $B/api/create -d '{"model":"X-32k","from":"X","parameters":{"num_ctx":32768},"stream":false}'`.
- **Free OpenRouter models verified for tool calling:** `nvidia/nemotron-3-super-120b-a12b:free`, `poolside/laguna-s-2.1:free`.
  `google/gemma-4-*:free` was rate-limited upstream.
- **Toolchain:** `uv` (brew) with Python 3.12; Node 26 / npm; Docker available.

## 3. Status (update on every commit)
| Area | State | Where | Verify |
|---|---|---|---|
| Plan/docs | ✅ v4 final | `*.md` | — |
| L0 substrate | ✅ done | `backend/src/wellops/substrate/` (ingest, views macros, report_facts, rules.yaml, schema.sql) | `cd backend && uv run python -m wellops.substrate.ingest && uv run pytest -q tests/test_substrate.py` (10 pass) |
| L1 dq flags | ✅ done | `backend/src/wellops/trust/dq.py` | flags: hot_idle 36, recorded_status_stale 21, inspection_gap 14, repair_in_progress 8, nonstandard filename 2, ref-only-in-text 2 |
| Contracts | ✅ done | `backend/src/wellops/contracts/__init__.py` (TS mirror at `frontend/src/contracts.ts`) | — |
| L2 tools | ✅ done | `backend/src/wellops/tools/` (core, data, docs, entity, obligations) | `uv run pytest -q tests/test_tools.py` |
| Memory | ✅ done | `backend/src/wellops/memory/` (assertions, conflicts, episodes) | — |
| L3 agent + skills | ✅ built (tested with TestModel, not yet run on a real model) | `backend/src/wellops/agent/` (agent, prompt, skills, turn), `skills/*/SKILL.md` (9) | `uv run pytest -q tests/test_agent.py` |
| L4 verifier | ✅ gates + output_validator | `backend/src/wellops/verify/gates.py` | backend `uv run pytest -q` (33 pass) |
| API (FastAPI + AG-UI) | ✅ live-verified ("15 wells not producing" via Nemotron free, verifier retry worked) | `backend/src/wellops/api/app.py`; run `cd backend && uv run uvicorn wellops.api.app:app --port 8000` | `uv run pytest -q tests/test_api.py` |
| L5 UI | ✅ done against mocks (build+lint pass); not yet wired live. Mock fixtures contain made-up IDs (e.g. INS-0001-0211, CTX-0164-002) and say "unconfirmed until you decide" (contradicts ADR-013): never demo mock mode, replace fixtures with real data | `frontend/` | `npm i && npm run dev:mock`; screenshots: `node scripts/screenshots.mjs <dir>` |
| L6 evals | 🟡 WIP (stream D): 18 golden cases (YAML + JSON schema), 60+ synthetic, scorers, Opik metrics, fault injection, key audit | `evals/` (own uv project) | key audit: **agrees 199, disagrees 0, not computable 77** (`evals/results/key_audit.md`) |
| Mutated dataset | ✅ generator + checks; findings fixed in 5a98a76 (new well-type rules extracted from its manual, T200 fallback + dq flag; duplicate inspection ids flagged) | `evals/evals/mutate/` | evals `uv run pytest -q` (25 pass) |
| Eval runner | ✅ `evals/evals/run.py` + Opik sink; never run against a live API | `evals/evals/run.py`, `runner/` | — |

## 4. How the streams were briefed (re-brief a new agent with the same scope)
- **B (backend):** tools → memory → Pydantic AI agent (`deps_type=RunDeps`, `output_type=AnswerSpec`, skills toolset
  over `skills/*/SKILL.md`, FallbackModel over `MODEL_FALLBACKS`) → verifier `output_validator` (grounding,
  re-execution, disclosure, conflicts, visual fit → ModelRetry ≤ 2) → FastAPI: `POST /api/agui` (headers
  `X-User-Id`, `X-Chat-Id`, `X-Clock-Today`), `GET /api/handles/{h}?chat_id=`, `GET /api/users`, `GET /api/chats?principal=`,
  `POST /api/assertions/{id}/undo`, `GET /api/health`. Custom AG-UI events: `wellops.answer` (AnswerSpec JSON),
  `wellops.verification`, `wellops.capability`, `wellops.conflict`. Tests use TestModel/FunctionModel (no network).
  Feedback semantics (ADR-013): corrections and rules commit immediately, attributed, original kept, Undo; claims
  contradicted by data are `unconfirmed`; only explicit corrections override detections; role never gates.
- **C (UI):** Next.js + Tailwind, AG-UI client, component registry keyed by `Visual.type`, data fetched by handle,
  evidence drawer with span highlight, conflict/correction cards, X-ray panel, user picker + clock control,
  mock mode with fixtures (a)–(h) from the example questions. Playwright screenshots go to the scratchpad.
- **D (evals):** own uv project depending on `../backend`. Golden cases as multi-turn, multi-principal episodes with
  a simulated clock. Deterministic scorers (also as Opik BaseMetrics), score = 0.5·facts + 0.25·evidence + 0.25·visual,
  pass^k. Targets: `fake` (from computed truth) and `api` (AG-UI). Fault-injection meta-eval. Synthetic generator.
  Mutated dataset (renamed reports, new wells, new type T500, shifted horizon, ID collisions). `--opik` pushes
  datasets and experiments with thread_id per chat.

## 4b. Session 2 team (lead = agent-copilot-hackathon-98)
- 7e: restore Ollama models + tool-call test + `backend/scripts/smoke_turn.py` (owns `.env`, `backend/scripts/`).
- 7b: real fixtures + UI wired to live API + screenshots (owns `frontend/`, `backend/scripts/make_fixtures.py`).
- 53: eval runner fake/api + failure taxonomy + WRITEUP/VIDEO_SCRIPT drafts (owns `evals/`, `docs/WRITEUP.md`, `docs/VIDEO_SCRIPT.md`).
- Gotchas: the API server holds the DuckDB lock, so in-process scripts must use `DB_PATH=/tmp/<name>.duckdb` and ingest first.
  Small models send nested tool args as JSON strings; `Scope`/`AssertionIn` accept them (b9627e0).

## 5. Next actions (in order)
1. Finish stream B: agent + verifier + API. Keep `uv run pytest -q` green in `backend/`.
2. Switch the local model to `qwen3-vl-8b-32k` if it passes the tool-call test (fallback `gemma4-e2b-32k`).
3. Wire the UI to the real API (turn off mock); show screenshots for Q2, Q3, Q6, Q7, Q8, Q12.
4. `evals`: run `--target api --suite golden --k 1` against the live API on the local model; triage failures by
   taxonomy; then `--opik`.
5. Mutated-dataset rehearsal of the final-2-hours runbook (PLAN §3).
6. Stretch (cut first): capability registry import/connect-MCP demo, optimizer, MCP Apps.
7. Write-up (2 pages) + 5-min video script in `docs/`.

## 6. Rules for whoever continues
- Never commit `.env`, keys, or the SSH password. Never hard-code answers to example questions (`DATA.md` §3 policy).
- Thresholds and deadlines come from `rules.yaml` / `params()`, never literals in tools.
- Tools return `contracts.ToolResult` with `sql`, citations, dq_flags and applied_overlays; the UI fetches rows by handle.
- Commit early with the co-author trailer from the system prompt, and update §3 of this file.
