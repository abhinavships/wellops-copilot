# WellOps Copilot: write-up

FlytBase Hack Day, Asset Intelligence Copilot. A chat copilot over an oil-field dataset (200 wells, ~40k
inspections, ~5.7k repairs, ~1.5k free-text reports, manuals and SOPs) that answers with grounded numbers,
picks its own visual, remembers per user, surfaces disagreement between sources, and is built to hold up on the
unseen test dataset. Design records: `DECISIONS.md` (ADRs), `TRADEOFFS.md`, `AUDIT.md`, `DATA.md`.

## 1. Architecture

```
L6 Evaluation    golden episodes · synthetic · mutated dataset · deterministic scorers · Opik experiments
L5 Generative UI AG-UI stream → AnswerSpec visuals bound to result handles → React catalog · X-ray panel
L4 Verification  output_validator: grounding · claim re-execution · disclosure · conflicts · visual fit → ModelRetry ≤ 2
L3 Agent         Pydantic AI · 9 skills (progressive disclosure) · fallback model chain
L2 Tools         well_status · downtime · spills · obligations · rca · repair_costs · evidence_chain · sql · docs · memory
L1 Trust         provenance on every fact · data-quality flags · overlays (corrections, rules, claims) · rules from manuals
L0 Substrate     one DuckDB file: raw tables tagged by dataset_id · canonical views · report facts · FTS
```

Each layer talks to the next through a typed contract (`contracts/__init__.py`, mirrored in `frontend/src/contracts.ts`),
so layers can be swapped independently.

- **Substrate (L0).** Deterministic, idempotent ingest into a single DuckDB file. Domain semantics are SQL views,
  not prompts: *producing ⇔ wellhead − ambient ≥ the type's threshold* (15 °C for T200, 20 °C for T350, read from
  `rules.yaml` with a manual section reference), *downtime = 7 days × non-producing weekly inspections* (the per-job
  `downtime_hours` column is a trap and the evals forbid it), real spill = detection not formally corrected. A graph
  *model* (inspection → repair → report → regulatory event → note) lives in joins behind `evidence_chain`. We
  rejected a graph database because traversals are ≤ 3 hops (ADR-004).
- **Tools (L2).** Curated tools for the common questions plus read-only `sql` over a documented catalog for
  novel ones. Every tool returns a `ToolResult` with the SQL it ran, a result **handle**, citations,
  data-quality flags, applied overlays and conflicts. The model sees a preview and the UI fetches full rows by
  handle (`GET /api/handles/{h}`), so large results never pass through the model.
- **Agent (L3).** Pydantic AI with `RunDeps{principal, chat_id, today, active_dataset}` taken from request headers,
  never from the model. `output_type=AnswerSpec`. Nine playbook skills (`skills/*/SKILL.md`) are loaded on
  demand. The tool list stays fixed, so prompts stay cache-stable. A `FallbackModel` runs a local Ollama model first,
  then free OpenRouter models.
- **API.** FastAPI. `POST /api/agui` streams standard AG-UI events plus `wellops.answer`, `wellops.verification`,
  `wellops.conflict`, `wellops.capability` and `wellops.usage`. The server owns the transcript per chat.
- **UI (L5).** Next.js. A component registry keyed by `Visual.type` (status card, table, line with threshold and
  not-producing bands, bar, map, heatmap, obligation timeline, conflict card, correction diff with Undo, evidence
  chain). An evidence drawer shows the cited record. An X-ray panel shows the tools and skills used, the gate
  results, retries and tokens.

## 2. Trust layer: data is evidence, not truth

The data audit found 24 traps (`DATA.md`): recorded status that is a stale monthly snapshot, hot wellheads that are
idle, corrections stored as notes, notes that disagree with sensors, facts that exist only in report text,
nonstandard report filenames, repairs still in progress at the clock date. We handle them in three places:

1. **Data-quality flags** are rows (`trust/dq.py`), recomputed for any dataset and returned with the tool
   results they affect. For example, `recorded_status_stale` lets the answer say "the system of record says
   active, but the latest reading is 0.4 °C above air".
2. **Overlays.** User corrections, rule overrides and claims are bitemporal assertion rows. Originals are never
   modified, and views apply confirmed overlays as of the simulated clock. Precedence goes by *kind*, not by role:
   an explicit correction beats sensor/record data, which beats an AI estimate. Notes and opinions are evidence
   only and never override a detection (AUDIT B2, B4). Corrections commit immediately, attributed and undoable. A
   claim that contradicts the data is stored as `unconfirmed` and shown next to the evidence (ADR-013).
3. **The verifier (L4)** turns honesty into hard gates. Every number and ID in the markdown must appear in a tool
   result or a claim. Every claim `{value, handle, column, row_key}` is re-executed against its handle and must
   match. Applied overlays must be listed and their author named. Conflicts found by tools must be surfaced. The
   visual must fit the result shape unless the user asked for it. Windowed answers must state their assumptions.
   On failure, `ModelRetry` sends a concise diff back to the model, at most twice. The gate results are streamed
   to the UI.

## 3. Memory

Memory is in-house and has two levels (ADR-012), because published memory layers leave isolation between users
under multi-user load mostly unbenchmarked.

- **Shared knowledge:** `assertions` (corrections, rules for a well type or area, planned downtime, claims). These are
  scoped (fleet → area → well type → well → inspection), attributed, bitemporal and visible to every user. A
  T350 rule set in chat changes the RCA answer for everyone, and Undo supersedes it.
- **Private history:** `episodes` record each turn's utterance, entities and result references. They are filtered
  in SQL by the principal from the session, never by an argument the model controls. `recall_episodes` resolves
  "the three wells we discussed yesterday" for that user only. An empty result makes the agent say there is no
  such earlier conversation instead of guessing.

## 4. Evaluation system

The eval system is its own project (`evals/`) that depends on the backend.

- **Cases.** There are 18 golden **episodes**: multi-turn, multi-user, with a simulated clock. They include setup turns
  (a correction by one user, a re-ask by another, day 1 → day 2 in a new chat). There are also 72 synthetic cases
  whose truth is computed from the substrate. Cases are YAML, validated by a JSON Schema.
- **Scorers** are deterministic, with no LLM judge on facts: facts (exact, tolerance, set-F1 that ignores negated
  mentions), evidence precision/recall, visual type, conflict surfaced, overlay disclosed, unconfirmed claim kept,
  no invented prior conversation, memory write (kind, scope, key, value), trajectory (expected tool used, forbidden
  `downtime_hours` column never touched, step budget), and a **cross-principal leak probe that zeroes the score**.
  Aggregate = 0.5 facts + 0.25 evidence + 0.25 visual. We report pass^k (all k runs pass) as well as pass@k.
- **Eval of the evals.** A fault-injection meta-eval corrupts correct answers in 10 ways (wrong number, dropped
  citation, wrong visual, hidden conflict, leaked entity, forbidden column...). Every scorer catches 100 % of its
  faults, with a 0 % false-alarm rate on clean answers.
- **Answer-key audit.** We do not trust the key blindly. Each expectation is recomputed from the substrate:
  199 agree, 0 disagree, and 77 are behaviours rather than numbers (conflicts, memory, wording). A disagreement is
  triaged by a human as our bug, a key error or an ambiguity, and is never auto-fixed.
- **Targets.** `fake` answers from computed truth to test the pipeline offline. `api` drives the real AG-UI
  endpoint and parses the same event stream the UI consumes. Runs are written to `evals/results/<run>/` and,
  with `--opik`, pushed as Opik datasets and experiments. Each chat is an Opik thread, so multi-user
  episodes read as conversations.

<!-- LIVE-RESULTS -->

## 5. Robustness to an unseen dataset

The test set has "the same format, more wells, more history", and possibly new well types and new questions. We
make no question-specific code paths. Thresholds and deadlines come from `rules.yaml` / `params()`, never from literals.
To rehearse, `evals/mutate` builds a **mutated dataset** with seeded changes: 12 new wells, 6 wells of a new
type T500 with its own manual, 25 renamed report files, the time horizon shifted by 28 days, and optionally
cloned IDs. Invariant checks then run on it: counts match the files on disk, every report resolves after
renames, area sums equal fleet totals, windows nest, and there is one latest status per well. All pass. The
rehearsal also found two real risks. We report them here, and the runbook handles them by hand until the profiler
does it automatically:

- **A new well type is silently dropped from the status views** until a threshold rule exists for it (1,154 T500
  inspections were invisible). The fix path: the manual is ingested as a document, a rule is extracted with a
  verbatim-quote check, and after that the 25 °C threshold applies to all 1,154 rows. The profiler does not yet
  flag unknown well types automatically, so the runbook checks for them by hand.
- **ID collisions.** A cloned well creates 207 inspection IDs shared by two wells. The substrate does not
  fan out rows, but any join on `inspection_id` alone is a contamination risk, so `dataset_id` scoping on every
  view and the collision report at ingest are mandatory.

Final-2-hours runbook (PLAN §3): ingest and profile the test set, read the drift report, extract rules for any new
manual, set `ACTIVE_DATASET` and the clock, run the smoke evals on the new data, then answer with the capability
set frozen.

## 6. Honest limits

- The capability registry, import/admission pipeline, DecisionService and prompt optimizer from the plan were
  cut for time (the planned cut order). Skills are static folders.
- The backend does not yet export OTel traces to Opik. Opik traces currently come from eval runs.
- Free model quota (about 50 requests a day) limited live evaluation. The deterministic pipeline is fully tested
  offline, and live scores come from small slices.
