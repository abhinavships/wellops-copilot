# WellOps Copilot — Master Plan (v4, build-ready)

> FlytBase Hack Day · judged on **backend engineering · evaluation system · generative UI**.
> Read with: [`DATA.md`](DATA.md) (structure, 24 traps, answer-key audit) · [`AUDIT.md`](AUDIT.md) ·
> [`DECISIONS.md`](DECISIONS.md) · [`TRADEOFFS.md`](TRADEOFFS.md).

## 0. Principles
1. **Don't trust the data blindly.** Records go stale, people disagree, and free text holds facts that the tables don't.
   The copilot reasons over *evidence with provenance* and says when sources disagree.
2. **Don't trust the answer key blindly.** Expected answers are hypotheses that we verify (DATA.md §3).
3. **Don't overfit.** No question-specific code. The test set has new wells, possibly new types, and new questions.
4. **Layers with contracts.** Each layer can be replaced without rewiring the layers above it.
5. **Feasible first.** Build a thin vertical slice end to end, then deepen it. Every phase ends demoable.

## 1. Layers

```
L6  EVALUATION      Opik (traces, threads, datasets, experiments, online LLM-judge rules, feedback)
                    + our deterministic scorers as Opik metrics + key-audit + synthetic/mutated sets
L5  GENERATIVE UI   AG-UI stream → A2UI-style specs bound to result handles → React catalog; X-ray panel
L4  VERIFICATION    claims-bound AnswerSpec → output_validator (hard gates) → ModelRetry ≤2
L3  AGENT           Pydantic AI · skills (agentskills.io, progressive disclosure) · capability registry
                    (import skills / connect MCP on demand) · memory tools · sandbox
L2  TOOLS           semantic tools (views) · open SQL over documented catalog · docs/report search ·
                    evidence chain · resolve entity · governed memory · MCP servers (fleet-data, knowledge)
L1  TRUST & KNOWLEDGE  provenance · data-quality flags · cross-source reconciliation · overlays
                    (corrections/claims) · rule registry extracted from manuals (cited)
L0  SUBSTRATE       DuckDB single file: raw tables (dataset_id) · graph edges · canonical views · FTS
```

### L0 Substrate
Deterministic ingest (seconds, idempotent, `dataset_id`-tagged, sample and test side by side).
**Profiler** (`make profile`): re-runs the DATA.md checks on any dataset (schema variants, ID collisions,
gaps, stale status, report drift) and writes `profile_<dataset>.json`. This feeds L1 flags and the runbook.

### L1 Trust & knowledge (this is the "not deterministic over data" layer)
- **Provenance on everything:** each fact carries `source ∈ {sensor, ai_estimate, record, report_text, note,
  user_claim, user_correction, manual, sop}`, its source ID, and its span (for text).
- **Quality flags** become first-class rows in `dq_flags(entity_id, kind, detail)`: stale recorded status, hot-idle,
  negative Δ, RC-01 gap, in-progress repair, report-only fact, nonstandard filename, borderline Δ (|Δ−θ| < 1 °C).
  Tools return the flags with their results, so the agent can say "recorded status is out of date".
- **Reconciliation:** when two sources speak about the same slot (structured record vs report text vs note vs
  user), compute agreement. On disagreement, the answer shows both with their provenance. Precedence: explicit correction ≻
  sensor/record ≻ AI estimate. Claims are unconfirmed until evidence agrees. Feedback notes are evidence, never corrections.
- **Overlays:** corrections, rule overrides and claims are bitemporal assertion rows. Originals are kept, and views apply
  confirmed overlays as of the clock.
- **Rule registry:** extracted from manuals and SOPs **at ingest by an LLM**, each rule with doc#section and a quote
  span, validated by checking the quote exists verbatim. Reviewed and frozen. A new manual in the test set goes through
  the same path. Agents can also read the sections directly (`read_doc`). Rules are accelerators, not the only source.

### L2 Tools (deliberately mixed: curated + open)
| Tool | Why |
|---|---|
| `well_status`, `downtime`, `spills`, `obligations`, `rca_candidates`, `repair_costs`, `inspection_series` | fast, exact, carry lineage + dq flags |
| `sql(query)` over the **documented catalog** (canonical views + raw tables, read-only, row cap, handle) | open-ended questions the curated tools don't cover |
| `read_doc(doc, section)`, `search_reports(q)`, `report_facts(ids)` | manuals, SOPs and free-text facts with spans |
| `evidence_chain(id)` | inspection → repair → report → reg events → notes |
| `resolve_entity(text)` | names, areas, people, dates → IDs |
| memory: `recall_episodes`, `assert_fact`, `list_conflicts` | governed (principal from session) |
| `run_python` | sandboxed analysis over handles |

The same tools are also exposed through our MCP servers (`fleet-data`, `knowledge`), so they work in Claude Code too.

### L3 Agent
- Pydantic AI agent with `RunDeps{principal, clock, active_dataset}`.
- **Skills** (agentskills.io folders, progressive disclosure): seed playbooks (`well-status`, `downtime`, `spills`,
  `compliance`, `rca`, `feedback-intake`, `conflicts`, `memory-recall`, `visual-choice`).
- **Capability registry** (DuckDB tables) with stable meta-tools: `search_capabilities`, `load_skill`,
  `call_capability`, `import_capability` (folder / git / MCP URL or command). Admission: lint + ontology check +
  replay on eval slice before `active`.
- **Feedback:**
  - Classify the message kind and scope (small-LLM structured output first; LAYA optional later).
  - Corrections and rules commit immediately, attributed, with Undo.
  - Contradicted claims become `unconfirmed`, and conflicts are surfaced.

### L4 Verification
`AnswerSpec{blocks, claims[{value, handle, col, row}], visual, conflicts, assumptions}`.
**Hard gates:**
- every number, date and ID is bound to a claim (grounding)
- the bound query re-executes and matches
- every applied overlay is disclosed
- known conflicts are surfaced
- no cross-principal entities leak
- the visual fits the result
- assumptions are stated (window, as-of, dataset)

On failure: `ModelRetry`, at most twice. **Query-conditioned:** which gates are strict depends on the intents detected.

### L5 Generative UI
- **Transport:** AG-UI events.
- **Spec:** a component tree bound to handles (A2UI-style subset).
- **Visual choice:** a deterministic default, overridden by the user's explicit request or by the agent's `render` call.
- **Components:** StatusCard, DataTable, LineChart (threshold + not-producing bands), BarChart, MapView, Heatmap,
  ObligationTimeline, ConflictCard, CorrectionDiff (Undo), EvidenceChain, EvidenceDrawer (span highlight),
  PlanChecklist.
- **Shell:** user picker, chats, and an **X-ray panel** (skills/tools/capabilities used, gates, retries, tokens, $,
  Opik trace link).

### L6 Evaluation (Opik-centred)
- **Tracing:** `logfire.instrument_pydantic_ai()`, exported over OTLP to **Opik** (self-hosted or Comet cloud). Each chat
  is one Opik **thread** (`thread_id`), so multi-turn and multi-user scenarios are inspectable as conversations.
- **Datasets & experiments:** the golden 18 (multi-turn YAML), synthetic (computed truth), mutated dataset, and harvested
  runs are pushed as Opik datasets. Every eval run is an Opik **experiment**, so runs can be compared side by side.
- **Metrics:** our deterministic scorers are implemented as Opik custom metrics:
  - facts (exact / tolerance / set-F1)
  - evidence precision/recall
  - visual type
  - trajectory (right tools/skills, canonical sources)
  - GateMem triplet (utility, leak = 0, forgetting)
  - pass^k, latency, $

  Opik's built-in LLM-judge metrics (hallucination, answer relevance) are used **only** for prose quality.
- **Online evaluation rules:** an LLM-judge plus our gate outcomes are logged as feedback scores on live traces.
  Thumbs and corrections from the UI are logged as human feedback, so they become labelled examples.
- **Key audit:** an eval category `disagrees_with_key`, triaged by a human (DATA.md §3).
- **Optimization (stretch):** Opik's optimizer SDK or GEPA on the system prompt and skill text, plus ACE-style delta
  edits to skills. Gate: no regression on held-out (Q13–Q18 + mutated).

## 2. Feasible schedule (agents in parallel after H1)

| Slot | A: Substrate & trust | B: Agent & memory | C: UI | D: Eval & Opik |
|---|---|---|---|---|
| **H0–H1** | repo skeleton, contracts (pydantic), DuckDB loader for sample | Pydantic AI hello-agent with 1 tool + AG-UI stream | Next.js shell + AG-UI client + user picker | Opik up (cloud or docker); OTLP wired; 18 cases as YAML |
| **H1–H3** | canonical views, dq_flags, profiler, report-fact extractor, rule extraction → rules.yaml | tools layer, AnswerSpec, skills loader + seed skills, entity resolver | catalog: StatusCard, DataTable, Line, Bar, Map, Heatmap, bound to handles | runner (multi-turn, multi-principal, simulated clock) → Opik experiments; deterministic metrics |
| **H3–H5** | evidence_chain, reconciliation, overlays in views; L0 tests vs key | verifier gates + ModelRetry; memory (assertions, episodes); feedback commit + conflicts | ConflictCard, CorrectionDiff, EvidenceDrawer, X-ray | synthetic generator, key-audit report, pass^3, online rules |
| **H5–H7** | mutated dataset + test-set ingest rehearsal | capability registry + import skill / connect MCP + admission | polish, visual-choice override, evidence chain view | full eval sweep, failure triage loop |
| **H7+** | buffer | stretch: LAYA backend, forge | stretch: MCP Apps card | stretch: optimizer run |
| **Final 2h** | runbook §3 | freeze | screenshots | answers + write-up |

**Never cut:** substrate correctness, trust flags and conflict surfacing, principal isolation, verifier hard gates,
visual fit, Opik tracing. **Cut first:** optimizer, forge, LAYA, MCP Apps, provider tool-search fast path.

## 3. Final-2-hours runbook
1. `make ingest DATA=<test> DATASET_ID=test && make profile DATASET_ID=test`, then read the drift report (new types,
   manuals, fields, ID collisions). For a new manual: `make rules-extract`, review, freeze.
2. `ACTIVE_DATASET=test CLOCK_TODAY=<from pack>`.
3. `make eval-smoke` (invariants + 20 synthetic questions on test data, as an Opik experiment).
4. `CAPABILITY_FREEZE=1 make answer-batch QUESTIONS=final.md` → answers + Opik experiment + screenshots → `docs/results.md`.
5. Review uncertainty notes and conflicts, then write page 2.

## 4. Layout
```
agent-copilot/
  backend/  api/ agent/ tools/ trust/ memory/ capabilities/ substrate/ verify/ mcp_servers/ contracts/
  skills/   seed playbooks
  frontend/ Next.js · ag-ui · catalog · xray
  evals/    cases/ synthetic/ mutate/ metrics/ runner/ (→ Opik)
  docs/     writeup.md results.md video-script.md
  dataset/  (git-ignored)
```
