# Autoformalizer upgrade backlog

Derived from the 2026-07-11 research survey
(`docs/research/2026-07-11-world-class-autoformalization-survey.md`), the
architecture + technical-blog reading catalogued in `docs/overview.md`, and a
pass over the current pipeline code (`probe/`, `pipeline.toml`, `scripts/`).

**Second research input (2026-07-18):** a harvest of the main repo's own
formalization grind history —
`docs/research/2026-07-18-mainrepo-grind-lessons-harvest.md`. Where the
2026-07-11 survey read the external field, this reads *our* thousands of hours of
hand-driven proving. Its headline finding is that the funnel dies at the
**drafter/intent** stages (every prove that reaches the vibe harness passes),
which get none of the shared `patterns.md` authority the prover does. Its ranked
hardening backlog (H1–H12) and trim candidates (T1–T7) live in that doc; the
highest-leverage items are **H1** (equip the drafter with a statement-design
subset of `patterns.md` + pins), **H2/H3** (add "Statement design" and "Repair
table" sections to `patterns.md` — draft content included, ready to paste), **H5**
(a wedged daemon silently passes the fail-open structural gates), and **H8**
(refine the strengthen pass with a sole-implicit-pin guard before it fires in
anger). See the doc for the full ranking and the convergence matrix behind it.

**Execution order of record (2026-07-18):**
`docs/superpowers/plans/2026-07-18-autoformalization-improvements.md` (v3, post
critical review) sequences all of this: Phase 0 discriminating experiment →
Phase 1 trust-hardening + diagnosis → **Phase 2 decomposition loop on Magistral**
(the general-reasoner role filled in-family for now, model-agnostic interface,
A/B'd against Claude centaur sessions — this reopens and resolves-for-now the
"one strategic fork" below) → Phase 3 throughput substrate + field-evidence
tuning → Phase 4 (brainstorm-gated). The v1 ordering in this file's §items stands
as reference; where they disagree, the plan wins.

**⛔ Dropped (R, 2026-07-18): the held-out evaluation/benchmark track ("MathFin-Bench").**
Do not plan, design, or propose it — here or in the plan — until R reopens it. It
had recurred across plans (this backlog's §B/§D caveats, the 2026-07-11 survey
item 5, BIG-LEAP Phase 1); those references below are neutralized in place. Tuning
and the decision gate now read the **live queue** (the obstruction-family report +
the A/B scoreboard on real targets), not a synthetic set.

**⏰ STANDING DECISION GATE — 2026-09-30 (Labs $0 retires):** decide
keep-Magistral / frontier-decomposer / hybrid on the A/B scoreboard
(`docs/research/ab-decomposer.md`, real targets), refinery-minutes-per-merged-PR,
leaves-closed-per-target per arm, and the actual price sheet. Evidence collection
starts the day the plan's Phase 2 lands.

Every item is tagged **[no reasoner]** (Leanstral-native or infra-only) or
**[needs general reasoner]** (requires a *second* engine — a general reasoning
model — because Leanstral is a leaf-prover: it does not architect proofs or judge
natural-language equivalence; the design-of-record says "feed it decomposed
targets, never chapters").

**Decision (R, 2026-07-11):** no general reasoner for now. Build the **[no
reasoner]** items; keep the **[needs general reasoner]** items designed and ready
for when we add a second engine.

**Targets** are the `status:ready` + `type:proof` issues R authors in
`formal-mathfin` — the pipeline follows R's issue backlog, *not* an autonomous
textbook-autoformalization stream (that design-of-record mode is not on the current
roadmap). So every statement is R-curated and faithfulness stays human-at-merge
(this parks §C).

---

## Already shipped — don't re-do

Grounding the survey's backlog against the code, these are done:

- **pass@k fan-out + bounded repair** — `run_target` in `probe.py` samples
  `fanout` whole-proof candidates/round and repairs the best failure for
  `repair_rounds` (survey #1).
- **dependency-closure context packs** — `house_context` walks
  `scout_index.dependency_closure(seed_names, depth)` and injects the closure's
  premises + docstrings, not flat signatures (survey #4 — further than the survey
  assumed).
- **tokens-per-attempt wired** — `pipeline.toml tokens_per_attempt` → `pipeline-tick.sh`
  → `probe.py --max-tokens` (survey #2's lever is plumbed; only the *value* is open — see B).
- **endpoint currency** — `labs-leanstral-1-5` confirmed live; `labs-leanstral-2603`
  retired 2026-06-30, pinned in a `probe.py` comment (survey #9).

---

## [no reasoner] — build these

### A. Harness config — DONE this pass (`docs/PROVER_SETUP.md`)

Two of the survey's harness levers are pure config in the vibe path, no new model:

- **`lean_multi_attempt`** — the REPL-backed multi-candidate tool in lean-lsp-mcp
  (~5× faster candidate checking, maintainer estimate). The agent should use it
  for cheap fan-out on hard targets.
- **self-hosted search endpoints** — point `LOOGLE_URL` / `LEAN_STATE_SEARCH_URL`
  / `LEAN_HAMMER_URL` at local instances to escape the hosted ~3 req/30s limit and
  keep queries private (survey #7). Caveat: a local Loogle build is ~2 GB — verify
  the footprint against the one-Lean-process memory doctrine before standing it up
  (it is host-side, not a Lean env, so it should be fine, but measure).

### B. Budget shape — APPLIED 2026-07-11 (`pipeline.toml`)

Was `fanout=8 × tokens_per_attempt=60_000`; now **`fanout=4 × tokens_per_attempt=120_000`**
(≈ the 500k cap in the primary round). **Rationale:** tokens-per-attempt is
Leanstral's dominant lever — its PutnamBench curve climbs 44 → 244 → 493 → 587
solves at 50k → 200k → 1M → 4M tokens/attempt — and 60k sat near the weak end; this
doubles the per-attempt budget while keeping pass@4 diversity. Repair
(`repair_rounds=2`) still funds escalated hard targets (2M cap). The survey's #2:
*"2–4 attempts × high reasoning budget."*

- **Caveat:** this is a field-evidence default (Leanstral's curve + Delta/Numina's
  depth>breadth finding), not tuned on our own data. The plan's Phase 3.2 applies
  the depth retune (`fanout=2 × repair_rounds≥6`) and watches the **live-queue**
  signal (obstruction-family report + queue outcomes); keep or one-line-revert on
  that. (No held-out bench — dropped per R, see top.)
- Sources: [Leanstral 1.5](https://mistral.ai/news/leanstral-1-5/), survey #2.

### C. Statement-side faithfulness filters — Leanstral-native; DESIGNED, ready to build

The two *cheap, kernel-grade* filters from survey #6. Both are **proving** tasks,
so Leanstral does them with no new model:

- **Hypothesis rejection** — for a target with hypotheses `h₁…hₙ ⊢ Concl`, attempt
  to prove `h₁…hₙ ⊢ False` under a small budget. If it succeeds, the hypotheses are
  contradictory (the theorem is vacuously true) → retire the target.
- **Disproof / disprove-and-retire** — attempt to prove `¬ Concl` (AlphaProof's
  negation transition). If it succeeds, the statement is false as written → retire.

**Design** — a new `probe/faithfulness.py`, reusing `mistral_chat` + `daemon_check`
from `probe.py`:
1. Generate the two probe goals from the stub. The stub format is regular
   (`build_manifest.py` enforces: one `theorem NAME <binders> : Concl := by sorry`),
   so `<binders>`/`Concl` are extractable; construct `theorem NAME_vac <binders> :
   False` and `theorem NAME_disproof <binders> : ¬ (Concl)` in the same import/open
   context. (Robustness note: prefer a small Lean elaboration step for the
   binders over pure text-splitting — a `Concl` can contain a top-level `:`.)
2. Run each through a short pass@k (low budget, e.g. 20k) against the daemon.
3. On a clean proof of either, mark the target `retired: vacuous|false` and skip
   the full proving budget; else pass through.

**Status:** designed, **parked**. Targets are always R-authored `formal-mathfin`
issues (R writes the statement; the stub states it exactly), so faithfulness is
human-at-merge and these filters are only a safety net against an accidentally
vacuous R stub — low value now. They become load-bearing **only** if the pipeline
is ever pointed at *un-curated* statements (the autonomous-textbook-factory mode
from the design-of-record, **not on the current roadmap** — 2026-07-11, R). Kept
designed so that switch would be a build, not a re-discovery: wire **opt-in** (on
for `source: autoform` targets), and it needs one live daemon+Leanstral validation
pass before enabling.

Sources: [Autoformalization with LLMs (Wu 2022)](https://arxiv.org/abs/2205.12615),
AlphaProof disprove-and-retire ([Nature](https://www.nature.com/articles/s41586-025-09833-y)),
DeepSeek-Prover-V2 hypothesis rejection ([2504.21801](https://arxiv.org/abs/2504.21801)).

### D. Verification throughput — infra; CI-side (survey #1's unaddressed half)

pass@k samples k candidates but verification serializes through the one lean-repl
daemon (forced locally by the memory doctrine — one Lean process). The batch-verify
belongs on the 16 GB CI runner, where the doctrine already sends full-environment
batch work:

- **Kimina-style parallel Lean REPLs + LRU env cache** (~10× throughput) as the
  pass@k verification backend on CI.
- or the same via lean-lsp-mcp's `lean_multi_attempt` (REPL-backed).

This is what makes a higher `fanout` — and the decomposition loop's parallel
leaf-proving + faster corpus `ledger verify --exec` sweeps — affordable (plan
Phase 3.1). Sources: [Kimina Lean Server](https://huggingface.co/blog/AI-MO/kimina-prover-rl),
[lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp).

### E. Learned premise retrieval — next rung on the context packs

The current packs use static `const_dep` graph closure. A BM25/embedding retriever
over `index/types.jsonl` would surface library lemmas R *didn't* name in a target's
Pointers — the retrieval-model rung above the graph-closure rung we already have.
No reasoner (a retriever/index is not a general model). Lower priority; the
graph-closure packs are already decent. Source:
[LeanDojo / ReProver](https://arxiv.org/abs/2306.15626).

---

## [needs general reasoner] — for when we add a second engine

These require a general reasoning model *above* Leanstral. Kept here, designed, so
the day we decide to add one there is no re-discovery. All must respect the hard
rule: any model sees only public-corpus + fresh-textbook statements.

### F. Subgoal decomposition (Draft-Sketch-Prove) — the biggest capability jump

The single largest capability step (survey #3), and what the most productive ops in
existence run on ([Gauss](https://www.math.inc/gauss), Aristotle).

- **Design:** `probe/decompose.py` — after a target exhausts its budget, a general
  LLM drafts an informal proof → restructures it into lemma statements with
  individually short proofs → each is autoformalized (reuse the context-pack
  machinery) → Leanstral proves the leaves independently → recompose. Keep proved
  lemmas (Aristotle's keep-and-revise) and enqueue them as first-class targets.
  Manifest needs lemma-DAG support.
- **Why the reasoner:** the draft+restructure step is a general-model job — in
  DeepSeek-Prover-V2 the decomposer is DeepSeek-V3, not the prover. Leanstral
  decomposes *within* a proof (`have` lemmas) but does not architect a blueprint
  for a hard MathFin theorem.
- **Note:** this is really *automating R's decomposition role* — R already writes
  the decomposed issue-targets by hand today (the human "sketch" of DSP).
- Sources: [Draft-Sketch-Prove (2210.12283)](https://arxiv.org/abs/2210.12283),
  [DeepSeek-Prover-V2 (2504.21801)](https://arxiv.org/abs/2504.21801),
  [Aristotle (2510.01346)](https://arxiv.org/abs/2510.01346), Gauss (above).

### G. Roundtrip + judge faithfulness — the general-model half of (C)

Beyond the two kernel-grade filters in C, the LLM-mediated faithfulness checks:
formalize → informalize → re-formalize → equivalence-check, and an LLM
faithfulness judge that filters unaligned statements. **Why the reasoner:**
informalization and semantic-equivalence judgment are not proving tasks. Parked on
the same condition as C (un-curated statements; not on the current roadmap).
Sources: survey #6 (faithfulness metrics), Harmonic's judge (Aristotle).

### H. Variant warm-up (cheap TTRL analogue) — partial reasoner

For a stuck target, generate simplified variants (special case, stronger
hypotheses, n=1/n=2), prove those first, feed them back as lemmas/context next
tick. The proving is Leanstral; the *variant generation* wants a general model, so
this is gated on the same decision as F. Sources:
[AlphaProof, by an author (Schrittwieser)](https://www.julian.ac/blog/2025/11/13/alphaproof-paper/) (Test-Time RL),
survey #8.

---

## The one strategic fork

Items F/G/H all hinge on a single decision: **add a general reasoning model as a
second engine, or keep R as the decomposer/judge?** Adding one unlocks
DSP/DeepSeek-scale decomposition (the biggest capability jump) at the cost of a new
model dependency, cost, and API-traffic surface. Until then — with targets coming
from R's curated `formal-mathfin` issues — the frontier is the **[no reasoner]**
work above: budget shape (applied), then CI-side verification throughput (D) and
learned retrieval (E) as the pipeline scales. C and G stay parked unless we ever
autoformalize un-curated statements.
