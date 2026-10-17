# Architecture Decision Records (v3)

Format: **Context → Decision → Alternatives → Necessity check → Consequences**.
v2 ADRs that changed are marked *Superseded*. The reasoning is in [`AUDIT.md`](AUDIT.md).
Companion: [`TRADEOFFS.md`](TRADEOFFS.md).

---

## ADR-001 Ontology-constrained generative system — *Accepted*
- **Context:** The final questions and dataset are unseen. Fixed toolsets overfit, and unconstrained agents
  hallucinate.
- **Decision:** Capabilities are acquired and generated at runtime. Every capability must pass an ontology
  check and the eval-based admission pipeline before use.
- **Alternatives:** fixed DAG; free-form autonomous agent.
- **Necessity check:** required by the user's direction and by "holds up on new data".
- **Consequences:** registry, broker and admission pipeline are core components.

## ADR-002 Online runtime = Pydantic AI — *Supersedes v2 ADR-002 (Hermes)*
- **Context:** A multi-user web copilot needs an async runtime that is safe across concurrent runs,
  model-agnostic, with typed outputs, MCP, HITL, UI streaming, evals and tracing.
- **Decision:** **Pydantic AI**: one Agent definition, per-run `RunDeps` (principal, clock,
  active_dataset), MCP toolsets, AG-UI adapter, deferred tools / approval for HITL, `output_validator`
  + `ModelRetry`, history processors, OTel. We add an agentskills toolset (~150 lines).
- **Alternatives:** Hermes (personal agent; not thread-safe; profile-level identity; single memory provider) ·
  Claude Agent SDK (Claude-centric; a CLI process per client) · OpenAI Agents SDK (strong second choice; no
  native AG-UI) · LangGraph (pushes toward fixed graphs) · Google ADK (Gemini/GCP-leaning) · own loop (a day of
  work).
- **Necessity check:** a framework is necessary because hand-writing the loop costs time and earns no score.
  This one maps 1:1 onto our needs.
- **Consequences:** Skill loading is ours. The spike S1 validates AG-UI streaming. Fallback: OpenAI Agents SDK,
  where the toolsets port and the contracts stay unchanged.

## ADR-003 Hermes Agent / Claude Code as the *offline* improvement worker — *Accepted (new)*
- **Context:** Hermes's best features (self-created skills, `skill_manage`, a learning loop) suit an agent that
  edits a repo of skills, which is the Meta-Harness proposer role.
- **Decision:** The offline improver (ACE Curator + GEPA runs) is executed by Hermes or Claude Code on `skills/`
  and prompt files. It never runs inside the serving path.
- **Necessity check:** optional. Cut first if time runs out.

## ADR-004 Single DuckDB substrate with graph tables — *Supersedes v2 ADR-007 (FalkorDB + Graphiti)*
- **Context:** The graph model matters (scope inheritance, evidence chains, contradictions, capability
  dependencies). The data is small and the traversals are shallow and fixed-depth.
- **Decision:** One DuckDB file holds facts, `nodes`/`edges` (bitemporal), canonical views, rules, memory,
  registry, FTS and embeddings. Graph access goes through typed tools (`evidence_chain`, `resolve_param`). DuckPGQ
  is optional for Cypher-like demo queries, behind a flag.
- **Alternatives:** FalkorDB / Neo4j (separate server, two query languages, consistency checks) · Kùzu (reportedly
  archived 2025) · Postgres + Apache AGE (good if we needed concurrent writers) · Graphiti (LLM extraction,
  nondeterministic, redundant for structured notes).
- **Necessity check:** a graph *database* fails the check (no variable-length traversals at scale). A graph
  *model* passes.
- **Consequences:** No cross-store consistency risk. DuckDB is single-writer, which suits our one API process.
  Moving to Postgres + AGE is a storage-adapter swap if multi-process writes are ever needed.

## ADR-005 Capability Registry + in-process Broker + stable meta-tools — *Supersedes v2 ADR-003/004/006*
- **Context:** Skills and MCP servers must be importable and connectable on demand. Mutating the tool list
  breaks prompt caching, and some runtimes freeze it.
- **Decision:** Registry tables in DuckDB. A broker holds a lazy MCP client pool and cached schemas. The agent
  sees a **fixed** surface: `search_capabilities`, `load_skill`, `read_skill_file`, `call_capability`,
  `import_capability`. Ranking is deterministic. When the provider is Claude, the broker also uses `defer_loading` and
  native tool search.
- **Alternatives:** dynamic `tools/list_changed` mounting (cache-busting) · gateway-as-MCP-server (extra
  hop) · Thompson-sampled router (no data to learn from; nondeterministic) · the whole catalog in context
  (bloat).
- **Necessity check:** passes. It delivers the user's requirement at the lowest runtime cost.
- **Consequences:** Schemas arrive in tool results (append-only, cache-safe). Broker policy is the single
  enforcement point.

## ADR-006 Admission pipeline instead of live shadow traffic — *Supersedes v2 ADR-005*
- **Decision:** `draft → checked → replayed → active → deprecated`, with static + ontology checks, replay on
  search + held-out eval splits in the sandbox, the held-out acceptance rule, and `CAPABILITY_FREEZE` in the scored run.
- **Necessity check:** passes. A hackathon has no live traffic, so replay is the only real evidence.

## ADR-007 DecisionService with pluggable backends — *Supersedes v2 ADR-008 (LAYA everywhere)*
- **Context:** Calibrated typed decisions matter for feedback kind and scope, implied states and span
  entailment. LAYA's specs are unverified, and its 512-token context doesn't fit whole reports.
- **Decision:** `DecisionService.choice/score/yes_no(question, text, options) → (answer, p)`. Backends:
  LAYA · small LLM with JSON-schema output · classifier on synthetic labels. Spike S2 picks one by accuracy, ECE
  and p95 latency. Entailment runs on spans.
- **Necessity check:** the intent router was removed (the LLM picks skills from cards). The service is kept only
  where calibration pays off.

## ADR-008 Frozen domain semantics (canonical views) — *Accepted*
- **Decision:** Producing, downtime, real spill, class, obligations, explanations by validity window and scope
  resolution are SQL views and small tools, hashed. The agent may write SQL over canonical views only.
- **Alternatives:** raw text-to-SQL; dbt MetricFlow / Cube (overkill for ~6 metrics); LLM over rows.

## ADR-009 Claims-bound answers + deterministic verifier in `output_validator` — *Supersedes v2 ADR-010 online part*
- **Decision:** `AnswerSpec.claims[{value, handle, column, row_key}]`. Hard gates: grounding, re-execution,
  canonical-only, disclosure, conflict surfacing, leak probe, visual fit. Soft checks (DecisionService) use
  per-check thresholds with `min` aggregation. The EvalSpec is query-conditioned. On failure: `ModelRetry` at most 2 times.
- **Alternatives:** LLM-as-judge (cost, hackable); κ product (collapses); LLM-written differential pandas
  (correlated errors).

## ADR-010 Offline evals on pydantic-evals + own multi-principal runners — *Accepted (new)*
- **Decision:** pydantic-evals datasets and evaluators, span-based trajectory scoring over OTel traces, Logfire
  or Phoenix for trace UI. Our additions: simulated-clock multi-principal episodes, synthetic/mutated/harvested
  sets, GateMem triplet, pass^k, Pareto, evaluator meta-eval.
- **Alternatives:** Inspect AI (strong for sandboxed agent evals; second choice) · promptfoo (CI/red-team) ·
  DeepEval / Braintrust.

## ADR-011 Self-improvement = ACE delta curation (skills) + GEPA (prompts) — *Supersedes v2 custom proposer*
- **Decision:** Skills are itemized playbooks. The Curator proposes delta bullets from labelled failures only
  (unlabelled online ACE regressed in its own paper). GEPA optimizes the few prompt components. Both are gated by admission.
- **Alternatives:** custom proposer · whole-skill rewrites (context collapse) · Hermes autonomous skill creation
  in the serving path (ungated).

## ADR-012 Governed memory in-house — *Accepted (refined)*
- **Decision:** `assertions` + `episodes` + `user_profile` tables. The principal comes from `RunDeps` and is never from
  model args.
- **Alternatives:** Letta, Mem0, Zep, Cognee. Isolation under multi-user load is largely unbenchmarked, and GateMem
  shows leaks in retrieval and external memories.

## ADR-013 Feedback commits immediately; confirmation is requested, not required — *Supersedes v2 HITL gate*
- **Context:** Q12–Q15 expect corrections and rules to take effect at once, for everyone.
- **Decision:**
  - **Commit:** explicit corrections and rule overrides commit as `confirmed`, attributed, original kept, with Undo.
  - **Claims:** a status claim contradicted by evidence is stored as `unconfirmed`.
  - **Notes:** only explicit correction kinds can override detections. Role is metadata, never a gate.
- **Consequences:** fixes AUDIT bugs B1, B2 and B4.

## ADR-014 Notes are matched by validity interval; implied state applies to all free text — *Accepted (new)*
- **Decision:** Explanations require `valid_from ≤ t ≤ coalesce(valid_to, ∞)`. Implied-state tags apply to
  inspection notes, context notes and chat claims. Tags never act as corrections.
- **Consequences:** fixes B3 and B5.

## ADR-015 Dataset scoping — *Accepted (new)*
- **Decision:** `dataset_id` on every row, and `active_dataset` in `RunDeps`. Views filter by it. Assertions record their
  dataset, and an ID collision report is generated at ingest.
- **Consequences:** fixes B7, the final-run double-count risk.

## ADR-016 Generative UI = AG-UI transport + A2UI payloads + MCP Apps passthrough — *Supersedes v2 custom DSL*
- **Decision:** A2UI component trees with data bound to handles, rendered by our React catalog, transported
  over AG-UI. A deterministic shape→component default, overridden by the user's request or the agent's `render`
  choice. Imported MCP servers' `ui://` resources render in sandboxed cards. A composer subagent is used only for
  answers with more than two panels.
- **Alternatives:** OpenUI Lang / custom DSL · LLM-written React · Vercel AI SDK tool-UI.

## ADR-017 Models — *Accepted*
Orchestrator Claude Sonnet 5.5 (Opus 5.5 for hard runs). Open configuration: Gemma 4 / Nous Hermes-family via
OpenRouter or Ollama. Workers: Haiku 5.5 or Gemma 4 26B. DecisionService chosen by spike. Everything reported on
a Pareto chart.

## ADR-018 Stack — *Accepted*
Python 3.12 + `uv`; FastAPI; Pydantic AI; MCP Python SDK (FastMCP) for our servers; DuckDB (+ fts, optional
vss / duckpgq); Next.js + React; MapLibre; Vega-Lite or Recharts; OTel → Logfire or Phoenix.

## ADR-019 Reproducibility — *Accepted*
Injected `Clock`, `active_dataset`, substrate + eval hash, `CAPABILITY_FREEZE`, temperature 0 where the provider
allows, and every answer stored with its trace and capability versions.

## ADR-020 Trust & knowledge layer (L1): data is evidence, not truth — *Accepted (new)*
- **Context:** The data audit (DATA.md) found 24 traps: stale status, hot idle wellheads, corrections, notes that disagree,
  facts that exist only in reports, nonstandard filenames, in-progress repairs. Also, the answer key may contain deliberate errors.
- **Decision:** Every fact carries provenance (source class, ID, span). Data-quality flags are rows returned with tool
  results. Cross-source reconciliation surfaces disagreements. Rules are extracted from manuals with a verbatim-quote check,
  and the agent can always read the source section. The profiler re-checks any loaded dataset.
- **Alternatives:** hard-coded semantics only (overfits the sample); raw RAG (no consistency).
- **Consequences:** answers state their assumptions and sources. The key-audit category in evals.

## ADR-021 Opik as the evaluation & observability platform — *Supersedes ADR-010's Logfire/Phoenix and pydantic-evals core*
- **Context:** The user requested Opik. We want one place for traces, multi-turn threads, datasets, experiments,
  online scoring and human feedback.
- **Decision:** Pydantic AI is instrumented with `logfire.instrument_pydantic_ai()` (`send_to_logfire=False`), and OTLP
  exports to Opik (`/api/v1/private/otel`; cloud or self-hosted). Chats map to Opik threads via `thread_id`.
  Golden, synthetic, mutated and harvested sets are Opik datasets, and each eval run is an Opik experiment. Our
  deterministic scorers are Opik custom metrics. Opik LLM-judge metrics are used only for prose quality. Online
  evaluation rules and UI feedback are logged as feedback scores. The Opik optimizer is a stretch goal.
- **Alternatives:** Logfire / Phoenix + pydantic-evals (fine, but two tools); Inspect AI (strong for sandboxed agent
  evals); Langfuse (similar scope).
- **Consequences:** Self-hosting runs a multi-container stack, so we use Comet cloud by default and docker as fallback.
  Deterministic scoring stays in our code, so evals don't depend on the platform being up.
