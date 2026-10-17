# Plan Audit (v2 → v3)

A critical review of plan v2 (Hermes + FalkorDB + Graphiti + Capability-Gateway-over-MCP + LAYA
routing). Part 1 lists **bugs**: things that would have produced wrong answers or broken at
runtime. Part 2 **interrogates every decision**: is it necessary, what are the strongest
alternatives today, and what is the verdict. v3 of [`PLAN.md`](PLAN.md) and
[`DECISIONS.md`](DECISIONS.md) apply these verdicts.

---

## Part 1 — Bugs found

### 1A. Bugs that would fail scored questions
| # | Bug in v2 | Fails | Fix in v3 |
|---|---|---|---|
| B1 | **HITL-before-commit on shared writes.** v2 held corrections until the user clicked Confirm. Q12 expects the correction recorded *and then* Q2 re-asked **excluding WELL-0164**. Q13 and Q14 also expect immediate effect. | Q12, Q13, Q14, Q15 | User corrections and rules **commit immediately**, attributed and reversible. Conflicts are flagged, and confirmation is *requested*, not required. Only claims contradicted by evidence stay `unconfirmed` (Q17). |
| B2 | **Role-gated "formal" corrections** (only an HSE officer's correction counted as formal). Michael Turner is a production supervisor, and Q15 needs his correction applied for every user. | Q15 | Role is metadata, never a correctness gate. Precedence is by *kind* (explicit correction ≻ sensor ≻ AI estimate), not by role. |
| B3 | **Implied-state tagging only on context notes.** The conflicting "Pump jack not moving." in Q12/Q15 is the **inspection's own `notes` field**, not a `context.jsonl` note. | Q12, Q15 | Tag inspection notes, context notes and chat claims through the same path. |
| B4 | **Feedback notes could act as corrections.** An implied-state tagger reading CTX-0101-002 ("only produced water") as `not_spill`, combined with precedence logic, could cancel a real spill. Q18 says neither note is a formal correction. | Q18, Q4, Q8 | Only `note_type == correction` (dataset) or an explicit user `detection_correction` can override a detection. Everything else is evidence for conflict display only. |
| B5 | **Note validity windows ignored.** Q1's cause comes from CTX-0001-003 because its `valid_from/valid_to` (7–20 Oct) covers the 15 Oct inspection. v2 never matched notes to readings by interval. | Q1, Q2 | Explanation joins require `valid_from ≤ t_inspection ≤ coalesce(valid_to, ∞)`. |
| B6 | **Graphiti LLM extraction of notes.** The notes are already structured (`note_type`, `inspection_id`, `valid_*`). Re-extracting them with an LLM adds nondeterminism, could mis-link a correction (changing spill counts), and defaults to OpenAI. | Q4, Q8, Q18 | Load notes deterministically. Use an LLM only to classify *chat* utterances into typed assertions. |
| B7 | **Test-dataset ID collisions.** The test set has "more wells and more history" in the *same format*, so WELL-0001… almost certainly reappear. "Load alongside" plus `dataset_id` tagging means duplicate wells and doubled counts unless queries are scoped. | final questions | `active_dataset` is part of the session/clock context. Every view filters by it. Assertions carry the `dataset_id` they were made against and re-bind by ID when switching (configurable). |
| B8 | **Window convention unresolved.** Q6 states 16 Jun → 16 Oct with today = 17 Oct, i.e. the window ends *yesterday*, or is inclusive of the last full day. | Q6, Q7, Q10, Q14 | The convention is a tested config: L0 cases pin Q6, Q7 and Q14 before any agent work. |

### 1B. Runtime / architecture bugs
| # | Bug in v2 | Effect | Fix |
|---|---|---|---|
| B9 | **Hermes `AIAgent` not thread-safe → one instance per turn.** Each turn re-runs MCP discovery and skill scanning. Profiles share one Hermes home, a single memory provider is active, and identity is per profile. | slow turns; principal isolation (ADR-011) not guaranteed in a multi-user web server | Online runtime moves to an async, stateless-per-run agent framework (§2.1). |
| B10 | **`pre_verify` hook assumed to gate final answers.** Its documented args (`coding`, `changed_paths`) suggest it is aimed at coding runs. Unverified for chat answers. | the whole online eval gate depended on it | Use the framework's native output validation with retry (`output_validator` → `ModelRetry`). |
| B11 | **Dynamic mounting via `tools/list_changed`.** Changing the tool list mid-session invalidates the prompt cache (Hermes's own tool-search docs note this), Hermes freezes the tool set per session, and the gateway-as-MCP-server adds a second serialization hop. | cost and latency on every mount; fragile | A **stable meta-tool surface** (`search_capabilities`, `load_skill`, `call_capability`): the tool list never changes, and schemas arrive in tool *results* (append-only, so cache-safe). Use provider-native tool search (`defer_loading`) when the model supports it. |
| B12 | **Thompson sampling in the user path.** With a few dozen runs the posterior is the prior, so it adds randomness without information. It also hurts pass^k and reproducibility in the scored run. | nondeterministic answers | Deterministic ranking online. Exploration only in offline eval replays. |
| B13 | **Shadow mode on live traffic.** There is no live traffic in a hackathon, and it doubles cost. | promotion never gets evidence | "Shadow" means **replay on the eval set** (search + held-out). |
| B14 | **LAYA as a hard dependency.** Its specs come only from third-party listings, which disagree with each other (e.g. 29.5K vs 33.2K stars). It has a 512-token context, so claim-vs-report entailment doesn't fit. It wants a GPU. | eval gate and router blocked if it underperforms | A **typed decision service** with pluggable backends (LAYA · small-LLM structured output · local classifier), chosen by a measured spike. Entailment runs on *spans*, not whole reports. |
| B15 | **Differential recompute via LLM-written pandas.** Two LLM paths can share the same wrong semantics (both use `downtime_hours`): correlated error. | false confidence | Answers carry **structured claims bound to query handles**. The verifier re-executes the bound query, and checks that the claim matches the result and that the query used the canonical views. |
| B16 | **κ = Π pₖ** assumes independent checks. A product of many calibrated probabilities collapses and triggers spurious repair loops. | latency, flapping | Hard gates plus `min(pₖ)` over soft checks, with a per-check threshold. |
| B17 | **Three stores (FalkorDB + DuckDB + object store) for a dataset under 1M rows.** Consistency checks, a Redis server, and two query languages for agents to learn. | ops and consistency risk in the final 2 hours | Single **DuckDB** file holds facts plus graph tables (§2.2). |
| B18 | **Generated capabilities read-only until promoted.** That's fine, but forged tools executed agent-written code with no resource limits specified for promotion replays. | runaway eval runs | Sandbox limits apply to replays too (CPU 20 s, 512 MB, no network). |

---

## Part 2 — Every decision interrogated

Format: **Q:** is it necessary? · **Alternatives (current)** · **Verdict**.

### D1. Online agent runtime (was: Hermes)
- **Necessary?** We need a loop, tool calling, MCP client, streaming, HITL hooks and multi-tenancy. We don't want
  to hand-write the loop.
- **Alternatives:**
  | Option | Strength | Weakness for *us* |
  |---|---|---|
  | Hermes Agent | self-creating skills, skills hub, 60+ tools, plugins | personal-agent design (CLI/messaging); not thread-safe; profile-level identity; one memory provider (B9) |
  | Claude Agent SDK | Claude Code's harness as a library: skills, hooks, subagents, in-process MCP | Claude-model-centric; runs a CLI process per client session, which is heavy for many concurrent users |
  | OpenAI Agents SDK | handoffs, guardrails, sessions, tracing, MCP, any model via LiteLLM | no native skills; UI protocol integration is ours to build |
  | **Pydantic AI** | async; model-agnostic; typed outputs; MCP toolsets; **AG-UI adapter**; **deferred tools / tool approval** (HITL); durable execution; `output_validator` + `ModelRetry`; **pydantic-evals**; OTel (Logfire) | no native agentskills loader (about 150 lines to add) |
  | LangGraph | checkpointing, interrupts, mature | nudges toward fixed graphs (against "generative"); heavier abstractions |
  | Google ADK | multi-agent, built-in eval | strongest on Gemini/GCP |
- **Verdict:** **Pydantic AI for the online copilot.** It is the only option that natively covers model-agnostic +
  typed + MCP + AG-UI + HITL + evals + OTel in one async, multi-tenant-friendly package. We add an agentskills
  toolset ourselves.
  **Hermes moves to where it shines:** the *offline* self-improvement worker (skill authoring and the
  ACE curator, §D10) operating on the repo's `skills/` folder, which is the job it was built for. The Claude Agent SDK
  or Claude Code is an equal alternative for that offline role.

### D2. Graph database (was: FalkorDB + Graphiti)
- **Necessary?** We need a graph **model**: scope inheritance, evidence chains, contradictions, capability
  dependencies. Do we need a graph **database**?
  The data is ~200 wells, ~40k inspections, ~5.7k repairs, ~1.5k reports and ~140 notes (the test set adds more,
  same order). Traversals are **shallow and fixed-depth** (inspection→repair→report→reg-event: ≤ 3 hops; scope
  hierarchy: 5 fixed levels). The capability registry is tens to hundreds of nodes. Nothing needs variable-length
  path search at scale.
- **Alternatives:** FalkorDB/Neo4j (Cypher, separate server) · Kùzu (embedded; reportedly archived in 2025) ·
  **DuckDB node/edge tables + joins/recursive CTEs** · DuckPGQ (SQL/PGQ in DuckDB; ISO SQL:2023 standard, but still
  labelled a research project) · Postgres + Apache AGE · Graphiti (an LLM-extracted temporal KG).
- **Verdict:** **No separate graph DB.** One DuckDB file holds facts, `nodes`/`edges` tables (bitemporal) and FTS.
  Graph queries are SQL joins and recursive CTEs behind typed tools. DuckPGQ is an optional nice-to-have for Cypher-like
  demo queries, behind a flag, never on the critical path. **Graphiti dropped** (B6). The graph is still *shown*
  in the UI (evidence-chain view) from the edge tables. Swapping to Postgres+AGE later is a storage-adapter
  change.

### D3. Capability acquisition and routing (was: MCP Capability Gateway + Thompson router)
- **Necessary?** Yes, the dynamic import/connect/generate requirement is core. *How* it is done changes.
- **Alternatives:** dynamic MCP mounting with `list_changed` (B11) · provider tool search with `defer_loading`
  (Claude: regex/BM25 tool search, cache-preserving; similar deferred loading reported for OpenAI) · MCP Registry +
  meta-tools · let the LLM read the whole catalog (bloat).
- **Verdict:** **Capability Registry (DuckDB tables) + an in-process Capability Broker** (MCP client pool with lazy
  connect) + a **stable meta-tool surface**:
  `search_capabilities(query, kinds?)` → ranked cards · `load_skill(name)` / `read_skill_file` (progressive
  disclosure) · `call_capability(id, args)` (schema validated by the broker) · `import_capability(source)` (pending
  admission). When the model supports native tool search, the broker also registers MCP tools with
  `defer_loading` and lets the provider handle search. Ranking is deterministic (BM25 + embedding + intent/entity
  match + smoothed success rate). The skill descriptions themselves let the LLM choose (progressive disclosure was
  designed for exactly that).

### D4. Typed decisions (was: LAYA everywhere)
- **Necessary?** Partly. Calibrated typed decisions matter for **feedback kind and scope**, **implied
  state of notes**, and **span entailment**. Intent routing does *not* need a classifier once skills are
  model-selected.
- **Alternatives:** LAYA (encoder, typed, calibrated; specs unverified) · small LLM with structured output
  (Haiku / Gemma, JSON-schema constrained) · zero-shot encoder classifiers · embeddings + logistic regression
  trained on **synthetic labels we can generate** from the dataset.
- **Verdict:** a `DecisionService` interface. The backend is picked in a spike on our own labeled sets (accuracy,
  calibration ECE, p95 latency). LAYA is the first candidate, and the small-LLM structured output is the guaranteed fallback.

### D5. Memory (was: Hermes memory provider + Graphiti episodes)
- **Necessary?** Yes, and it is a scored axis (Q11, Q15, Q16).
- **Alternatives:** Letta (agent-is-memory runtime) · Mem0 (bolt-on memory layer, graph variant) · Zep/Graphiti ·
  Cognee · our own governed tables. Vendors themselves note that isolation under multi-user load is largely
  **unbenchmarked**, and GateMem shows retrieval and external memories leak.
- **Verdict:** **Own governed memory** in DuckDB: `assertions` (shared, scoped, bitemporal) and `episodes`
  (private), with principal bound from the session and never from model args. It is small, auditable and testable
  for leaks. Mem0 or Letta would add a dependency without solving governance.

### D6. Deterministic domain semantics as frozen tools
- **Necessary?** Yes. "Facts must match", and LLM arithmetic over rows is unreliable. The harness primer's
  frozen-substrate split supports it.
- **Alternatives:** text-to-SQL over raw tables · semantic-layer products (dbt MetricFlow / Cube) · LLM over rows.
- **Verdict:** **Keep**, implemented as SQL views + small Python tools. A metrics-layer product is overkill for 6
  metrics. The agent may still write SQL over the *canonical views* (never raw tables) for novel compositions.

### D7. Answer verification (was: `pre_verify` + κ product + differential pandas)
- **Verdict:** **structured claims bound to query handles** (each number in the answer is
  `{value, handle, column, row_key}`). The `output_validator` checks these deterministically: grounding,
  re-execution of the bound query, canonical views only, overlay disclosure, conflict surfacing and visual fit.
  Soft checks (span entailment) go through the DecisionService. On failure it raises `ModelRetry` with the diff (at most 2).
  No probability product (B16).

### D8. Generative UI (was: custom OpenUI-style DSL + ui-coder subagent)
- **Necessary?** A generated UI is required, but a custom protocol is not.
- **Alternatives:** **AG-UI** (event transport, state sync; Pydantic AI ships an adapter) · **A2UI** (Google's
  declarative, streaming JSONL UI payload; the host controls rendering, so it is safe) · **MCP Apps** (server ships
  `ui://` HTML in sandboxed iframes) · Vercel AI SDK tool-UI · OpenUI Lang · custom DSL. The ecosystem frames
  them as layers: AG-UI is the pipe, A2UI is the content, MCP Apps is server-owned UI.
- **Verdict:** **AG-UI transport + A2UI payloads** rendered by our React component catalog, with data bound to
  result handles. **MCP Apps passthrough** lets an imported MCP server that ships its own UI render inside a sandboxed
  card. That is a real "connect MCP, get UI" story. The separate ui-coder LLM call is **dropped from the default
  path** (it added 2–5 s per answer). A deterministic shape→component default plus the main agent's typed
  `render` choice covers the requirement. A composer subagent is used only for multi-panel answers.

### D9. Evaluation framework (was: custom runner)
- **Alternatives:** **pydantic-evals** (datasets, deterministic, LLM-judge and span-based evaluators over OTel
  traces) · **Inspect AI** (agent evals, sandboxes, multi-turn, Inspect View) · promptfoo (CI, red-team, config-driven)
  · DeepEval / Braintrust / Phoenix (observability-first).
- **Verdict:** **pydantic-evals** as the core: native to the runtime, with span-based evaluators that give
  trajectory scoring from the same OTel traces we already emit. **Logfire or Arize Phoenix** for trace UI. Our
  own additions: multi-principal episode runner with a simulated clock, synthetic/mutated generators, the
  GateMem triplet, pass^k, Pareto. Inspect AI is the alternative if we need heavier sandboxed agent evals.

### D10. Self-improvement (was: custom proposer loop)
- **Alternatives:** **GEPA** (DSPy; reflective evolution of text components with Pareto selection, reported to beat RL
  with far fewer rollouts) · **ACE** (Agentic Context Engineering: Generator / Reflector / Curator growing an itemized
  playbook through delta updates; avoids "context collapse" from full rewrites; offline ACE beat GEPA on AppWorld in
  its paper; *unlabelled online ACE regressed*) · Meta-Harness / Autoresearch (archive + proposer) · Hermes
  self-skill creation.
- **Verdict:** **ACE-style delta curation for skills** (skills are itemized playbooks; the Curator appends or edits
  bullets, never rewrites whole skills), **only with labels from our eval** (online unlabelled ACE regressed).
  **GEPA** for the few prompt components (orchestrator system prompt, claim-format instructions). Both run inside a
  Meta-Harness archive with the held-out acceptance rule and an evaluator hash. The worker that does the editing is
  Hermes, or Claude Code, offline.

### D11. Models
- **Verdict:** unchanged in spirit. Orchestrator: Claude Sonnet 5.5 (Opus 5.5 for hard runs) through any
  OpenAI- or Anthropic-compatible endpoint. Open configuration: Gemma 4 31B / Nous Hermes-family through
  OpenRouter or Ollama. DecisionService backend chosen by spike. Everything is reported on the Pareto chart.

### D12. Things v2 had that survive unchanged
Bitemporal overlays (originals never mutated) · scope specificity resolution with T350→T200 inheritance ·
obligation algebra · handles for large results · lineage-derived citations · injected `Clock` ·
substrate hash · held-out acceptance rule · `CAPABILITY_FREEZE` during the scored run · multi-principal eval
with a leak rate of 0 as a hard gate.

---

## Sources
- Hermes Agent docs: [architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture), [Python library](https://hermes-agent.nousresearch.com/docs/guides/python-library), [hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks), [MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp), [skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills), [tool search](https://hermes-agent.nousresearch.com/docs/zh-Hans/user-guide/features/tool-search)
- Framework comparisons: [morphllm](https://www.morphllm.com/ai-agent-framework), [requesty](https://www.requesty.ai/blog/best-ai-agent-sdks-compared-2026-langchain-crewai-openai-anthropic-google), [Pydantic AI](https://pydantic.dev/pydantic-ai)
- Gen-UI protocols: [CopilotKit comparison](https://copilotkit.ai/blog/the-state-of-agentic-ui-comparing-ag-ui-mcp-ui-and-a2ui-protocols), [A2UI ecosystem](https://a2ui.org/introduction/agent-ui-ecosystem/)
- Graph options: [DuckPGQ](https://duckdb.org/community_extensions/extensions/duckpgq), [Graphiti](https://pypi.org/project/graphiti-core/0.29.1/), [Thoughtworks Radar](https://www.thoughtworks.com/en-ca/radar/platforms/graphiti)
- Tool search: [Anthropic tool reference](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-reference.md), [defer loading guide](https://unified.to/blog/scaling_mcp_tools_with_anthropic_and_openai_defer_loading)
- Self-improvement: [ACE paper](https://arxiv.org/pdf/2510.04618v1), [ACE repo](https://github.com/ace-agent/ace)
- Memory: [GateMem](https://arxiv.org/abs/2606.18829), [Mem0 on benchmarks](https://mem0.ai/library/agent-memory/ai-memory-benchmarks-in-2026), [AIM / MUMBench](https://arxiv.org/pdf/2609.12320)
- Evals: [Inspect AI](https://inspect.aisi.org.uk/llms-full.txt), [promptfoo vs Inspect](https://aicoolies.com/comparisons/promptfoo-vs-inspect-ai)
- LAYA: [listing](https://www.opensourcealternatives.to/item/laya)
