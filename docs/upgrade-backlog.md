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

**⏰ STANDING DECISION GATE — 2026-09-30 (Labs $0 retires):** decide
keep-Magistral / frontier-decomposer / hybrid on the A/B scoreboard
(`docs/research/ab-decomposer.md`, real targets), refinery-minutes-per-merged-PR,
leaves-closed-per-target per arm, and the actual price sheet. Evidence collection
starts the day the plan's Phase 2 lands.
*Update 2026-07-23: largely pre-empted for the DRAFT stage — R decided a frontier
drafter directly (see §"2026-07-23 — TauCeti/Nexus adoption round"). The 09-30
gate still owns the PROVE-side economics (Leanstral endpoint pricing).*
*Update 2026-07-29: Magistral REMOVED entirely — Claude is now the sole general
reasoner (intent · agentic formalize · faithfulness judge · decompose split);
Leanstral proves. "keep-Magistral" is off the table; the 09-30 gate is now purely
Leanstral prove-side pricing. The intent-fidelity gate was also retired (A/B: 0/62
drift firings + a clean off-arm — no marginal catch over the faithfulness judge).*

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

## Item convention: observed ≠ proposed (2026-07-31)

An item has two parts that are **not** equally reliable, and this file used to write
them at the same confidence:

- the **finding** — a failure that happened, with the artifact it happened on. Evidence.
- the **fix** — a mechanism believed to catch it. A hypothesis until traced.

Item R was written as "delete each binder and re-elaborate; anything that still
compiles was decorative". The finding was solid; the fix was wrong, and wrong in a way
30 seconds of tracing would have caught — the pipeline already had that pass, and on
the very drafts cited it does not fire, because both proofs *use* the guard
(`have hden := h.le`, `field_simp [h]`). It sat here, in the research doc, in a PR body
and in a GitHub comment as though it were established. Confidence was inherited from
the finding it was attached to.

So: **a proposed gate carries a `**Reproduction:**` line** naming the concrete artifact
it fires on and why. If the trace has not been run, tag the heading `[UNTRACED]` and say
so — that is a perfectly good state for an item to be in, it just is not a plan.
`test_backlog_format.py` enforces this; tags for work already done (`[BUILT …]`,
`[LANDED …]`, `[APPLIED …]`, `[DONE …]`) are exempt, since shipped code is its own trace.

---

## Status of every lettered item (swept against the code 2026-08-06)

This backlog had drifted badly behind the repo: of the 17 lettered items, **nine
were carried as pending while already being built**. The drift is not cosmetic —
it cost a wrong read of an external signal during the Axiomatic harvest (item D's
CI verify pool was written up as an outstanding lever when `verify_pool.py` has
shipped it for weeks), and an unbuilt roundtrip was being *claimed* by two module
docstrings. Every row below was checked against the named file, not against a
memory of the last session.

| | Item | Status | Where |
|---|---|---|---|
| A | Harness config | done | `docs/PROVER_SETUP.md` |
| B/B′ | Budget shape + depth retune | applied | `pipeline.toml` |
| C | Statement-side kernel filters | **landed** | `autoformalize.vacuity` / `.disproof` |
| D | Verification throughput | **landed** | `probe/verify_pool.py` + `batch-verify.yml` |
| E | Learned premise retrieval | **landed** | `probe/embed.py`, `retrieval_backend` |
| F | Subgoal decomposition | **landed** | `probe/decompose.py`, `[decompose]` |
| G | Roundtrip + judge | **partial** | judge built; **roundtrip unbuilt** |
| H | Variant warm-up | **not built** | — (correctly pending) |
| I | Frontier drafter | landed | Claude is the sole drafter |
| J | Statement-integrity pin | **landed** | `gate.gate(statement=…)` |
| K | Cross-tick lessons-learned | **landed** (both halves) | draft: `af_routing.load_prior_lessons` + `probe/experience.py`; prove: `probe/experience.py` |
| L | Goal/disproof cache + timeouts | **landed** | `probe/gate_cache.py` |
| M | Instance-probe gate | **landed** | `af_gates.instance_probe_rejection` |
| N | Run-record provenance schema | **landed** | `schema/run_record.v1.json` |
| O–Q | Review-engine adoptions | main repo | not this pipe |
| R–V | 07-31 review round | built | see each entry |

**Genuinely open: H, and the roundtrip half of G.** Both were already gated on
the same condition (un-curated statements, not on the current roadmap), so the
practical effect of this sweep is not new work — it is that the next harvest
reads a backlog that means what it says.

**Keeping it honest**: a LANDED marker is only worth as much as the check behind
it. Each one above names the file that implements it; add the marker in the same
commit as the code, and when an item is partial, say which half.

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
  that.

### B′. Phase 3.2 depth retune, reconciled to the vibe era — APPLIED 2026-07-18 (`pipeline.toml`)

Entry B described the **retired text-loop's** `fanout × tokens_per_attempt` knobs.
Since 2026-07-17 the prove path is the vibe ⇄ lean-lsp harness: **fanout collapsed to
1**, all budget in one deep agentic session bounded by `max_turns`. That migration *is*
the maximal depth move of the Delta/Numina `depth > breadth` finding — the retired knobs
no longer exist (comments only).

- **Applied:** `max_turns 40 → 60` — the live realization of "more depth at equal total
  spend": more repair turns under the **unchanged** `tokens_per_issue_cap` (500k), which
  still bounds actual spend (a session that hits the cap stops regardless of turns).
- **Deferred to CI (Phase 3.1 pool):** the evidence's `fanout=2` refinement (two modest
  deep sessions beat one at fixed budget) needs **two parallel Lean sessions** — the
  local memory doctrine forbids that (two Mathlib envs overcommit the 10 GB box), so it
  is a big-box experiment on the parallel pool, not a config flip. Revisit when the pool
  runs on a ≥16 GB runner.
- **Watch (keep or one-line-revert):** the obstruction report's `prover-max-rounds`
  family and the queue merge-rate over the next several ticks. If `prover-max-rounds`
  shrinks and merge-rate holds/rises, keep `max_turns=60`; if outcomes worsen, revert to
  40. Record the call + its real-queue evidence here.
- Sources: [Leanstral 1.5](https://mistral.ai/news/leanstral-1-5/), survey #2.

### C. Statement-side faithfulness filters [LANDED — `autoformalize.vacuity`/`disproof`]

The two *cheap, kernel-grade* filters from survey #6. Both are **proving** tasks,
so Leanstral does them with no new model:

- **Hypothesis rejection** — for a target with hypotheses `h₁…hₙ ⊢ Concl`, attempt
  to prove `h₁…hₙ ⊢ False` under a small budget. If it succeeds, the hypotheses are
  contradictory (the theorem is vacuously true) → retire the target.
- **Disproof / disprove-and-retire** — attempt to prove `¬ Concl` (AlphaProof's
  negation transition). If it succeeds, the statement is false as written → retire.

**Shipped**: `autoformalize.vacuity` / `autoformalize.disproof`, run as the kernel
gate battery in the refill phase, with the b078f9f statement pin
(`af_gates._probed_conclusion`) blocking the reverted-goal false positive and
`gate_cache` (item L) substituting a cached verdict on a repeat. Note the scope
of THIS item is only the two kernel-grade probes; the LLM-mediated checks
(back-translation roundtrip, multi-variant drafting) were always item G and the
survey's item 6, and the roundtrip half of those is still unbuilt — see G.

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

### D. Verification throughput — infra; CI-side [LANDED — `probe/verify_pool.py` + `batch-verify.yml`]

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

**Shipped** as `probe/verify_pool.py` (a generic pool scheduling tasks across
reusable workers that hold a warm Lean env; order-preserving, and an OOM/crash
recycles the worker and retries up to `max_recycle`) driven by
`.github/workflows/batch-verify.yml`, **`workflow_dispatch` only** — never on the
cron, because N Lean processes overcommit a small box. It parallelizes
elaboration/verification (the leaf gate, the `ledger verify` sweep), NOT the
agentic prove, which keeps its single lean-lsp slot. The real Lean worker path is
CI-only by construction and cannot be exercised on the dev box; the scheduling and
recycle logic is unit-tested with fakes (`test_verify_pool.py`).
Corroborated externally 2026-08-06: Axiomatic run `max_concurrent_builds: 12` with
no sample-level fan-out at all — parallelism in builds, not in sampling, is the
same call we made ([harvest](research/2026-08-06-axiomatic-harvest.md)).
This entry was missing its LANDED marker until that harvest; the code predates it.

### E. Learned premise retrieval [LANDED — `probe/embed.py`, `retrieval_backend = "embedding"`]

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

### F. Subgoal decomposition (Draft-Sketch-Prove) [LANDED — `probe/decompose.py`, `[decompose] enabled`]

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

### G. Roundtrip + judge faithfulness [PARTIAL — judge BUILT; roundtrip NOT built]

Beyond the two kernel-grade filters in C, the LLM-mediated faithfulness checks:
formalize → informalize → re-formalize → equivalence-check, and an LLM
faithfulness judge that filters unaligned statements. **Why the reasoner:**
informalization and semantic-equivalence judgment are not proving tasks. Parked on
the same condition as C (un-curated statements; not on the current roadmap).
Sources: survey #6 (faithfulness metrics), Harmonic's judge (Aristotle).

**Half shipped, half not, and the docstrings lied about it.** The judge IS built
(`af_drafting.judge_faithfulness` + `af_prompts.JUDGE_SYSTEM`, called at
`autoformalize.py:372`, failing CLOSED on an unparseable verdict). The
**roundtrip is not**: there is no informalize step, no re-formalize, no
equivalence check, and no prompt for any of them — yet `autoformalize.py`'s module
docstring said the drafter "judges faithfulness + roundtrips" and
`AutoformalizeConfig`'s said it "drafts+judges+roundtrips". Both corrected
2026-08-06. A docstring claiming a faithfulness check that does not exist is the
worst class of drift in this repo: the whole gate battery's value is that you can
read off what was actually checked.

**What is actually left here**: formalize → informalize → re-formalize →
equivalence-check. Still gated on the same condition (un-curated statements),
so still not scheduled — but now it is unbuilt-and-labelled rather than
unbuilt-and-claimed.

### H. Variant warm-up (cheap TTRL analogue) — partial reasoner [UNTRACED]

For a stuck target, generate simplified variants (special case, stronger
hypotheses, n=1/n=2), prove those first, feed them back as lemmas/context next
tick. The proving is Leanstral; the *variant generation* wants a general model, so
this is gated on the same decision as F. Sources:
[AlphaProof, by an author (Schrittwieser)](https://www.julian.ac/blog/2025/11/13/alphaproof-paper/) (Test-Time RL),
survey #8.

`[UNTRACED]` per the item convention: the mechanism is taken from the literature, not
from a failure of ours. Before it becomes a plan, trace it against a real
`max_rounds` death in `runs/` and say which variant would have unstuck it — otherwise
we would be building a gate for a problem we have not shown we have.

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

**RESOLVED + LANDED (R, 2026-07-23 → built 2026-07):** a frontier drafter joins the
pipe — Claude via the existing claude.ai subscription. See the adoption round
below (item I). Magistral is now REMOVED entirely: Claude owns intent · agentic
formalize · faithfulness judge · decompose split; Leanstral proves. The 09-30
gate's residual scope is Leanstral prove-side economics only.

---

## 2026-07-23 — TauCeti/Nexus adoption round + the frontier-drafter decision

Sources: AlphaProof Nexus ([arXiv 2605.22763](https://arxiv.org/abs/2605.22763) —
9/353 open Erdős from `formal-conjectures` statements; the decision-relevant
ablation: frontier-LLM *basic* loop solved all 9, small-model basic loops 0/9,
specialist prover alone 0/9 — drafter model class dominates architecture) and the
Tau Ceti review/coordination stack ([TauCetiProject](https://github.com/TauCetiProject),
Lean FRO-incubated; 10 single-angle adversarial rubrics, contest protocol,
full-provenance review records, live-queue meta-review). Recon + position:
memory `project_tauceti_landscape_2026_07_23`. Everything below is mapped against
SP1–SP3 (done; PRs formal-mathfin#163/#164 validated the defs route e2e) so no
item re-does shipped work.

**Already covered by SP1–SP3 — external validation, do not re-add:**
- Vacuity/disproof gates with the statement pin (b078f9f) = Nexus's
  disproof-as-misformalization-detector (their catches on Erdős #125/#741(i) came
  exactly this way). Ours is built and statement-pinned. (The separate intent-fidelity
  gate was retired 2026-07-29 — A/B showed no marginal catch over the faithfulness judge.)
- `max_turns` + `tokens_per_issue_cap` = their bounded episode budgets (5 prover
  calls / 90 edits per episode).
- The depth gate = their #1 prompting-resistant failure mode ("core difficulty
  offloaded into a sorry'd helper that reiterates the target… explicitly prompting
  against this behavior failed"). Machine gates over prompt guidance: confirmed.
- Subset-drop on a killed conjunct = their reroute-to-spec, in lite form.
- K-parallel independent attempts = item D (CI pool); memory doctrine forbids it
  locally, unchanged.

**Skipped deliberately:** landrun-style sandboxing (docker containment already
matches our own-models threat model); anything ulamai (repo stale since
2026-03-27, solo-maintained, no license grant on file).

### I. Frontier drafter — Claude via subscription [LANDED — Claude is the sole drafter; Magistral removed]

Supersedes the 2026-07-19 "Mistral-only, no Claude in the pipe" constraint, by
R's direct decision (no A/B): the Nexus ablation matches our funnel exactly —
every live death is drafter-side (obstruction census: depth-gate 6 +
no-elaborating-draft 5, prover-max-rounds 0), and the grind-lessons harvest's
headline was already "every prove that reaches the vibe harness passes."

- **Slot:** the DRAFT stage only — intent + formalize + emit, where all deaths
  concentrate. One headless `claude -p` session per target produces the
  elaborating stub from the issue + context pack. **Leanstral keeps PROVE**
  (vibe ⇄ lean-lsp harness untouched) — it is not the bottleneck, and
  attribution stays exactly as ruled: `Co-Authored-By: Leanstral` (the prover)
  on autoform PRs, Claude never attributed. Whether the PR body should disclose
  a frontier-assisted draft stage = R's call at first merge.
- **Integration:** `pipeline.toml [drafter] engine = "claude" | "mistral"`, the
  mistral path kept intact as fallback. Reuse untouched: context packs,
  `_prelint_stub` + deterministic emit-repair (A15 noncomputable etc. — hygiene
  is engine-agnostic), the full gate battery (drafter-agnostic by construction —
  the SP1–SP3 payoff: gates don't care who drafted), manifest/queue/open-pr.
  Auth = existing claude.ai subscription login (OAuth in `~/.claude`; verify it
  survives the cron env — no API key, no new secret surface).
- **Caps handling:** subscription usage windows — on a cap error the tick defers
  the target (requeue, no obstruction recorded) or falls back to the mistral
  drafter for that tick. Cron retries absorb it, same as model flakes today.
- **First validation set:** the live stuck families the Mistral-only drafter died
  on — #53, #61, #72, #88, #108 (+ #109/#60 unknown-id-despite-retrieval).
  Success = seeds passing gates and reaching prove; measured by the obstruction
  census (the standing did-it-help signal), not a synthetic bench.
- **Watch:** statement fidelity is still gate-enforced (and J below tightens it);
  a stronger drafter raises the stakes on the fidelity/faithfulness gates, not
  lowers them.

### J. Statement-integrity pin on the main prove path [LANDED — `gate.gate(statement=…)`]

Extend b078f9f's `_probed_conclusion` fidelity guard from the vacuity/disproof
probes to `run_target`'s REAL goal: count a prove pass only if the winning
candidate still asserts the stub's statement (exact-statement match or
elaborated-type hash). Today the gates are pinned but the main path still trusts
whatever file the prover returns — the same reversion loophole, one level up.
This is Nexus's mid-loop check that "permits sorry placeholders but verifies that
the original target theorem statement was not altered." Direct SP1-line
continuation; becomes more load-bearing the day the drafter/prover gets stronger (I).

**Shipped**: `gate.gate(..., statement=stub)` compares `_probed_signature` of the
accepted candidate against the original stub and rejects `statement_altered`;
`vibe_prove._cmd_gate` reads the stub back off the queue to supply it, failing
open to the kernel bar if the scratch file is gone. Deliberately passed as `None`
by the post-accept transforms (strengthen / necessity), which change the signature
on purpose. Independently corroborated 2026-08-06: Axiomatic's Reviewer node
enforces the same two rules, statement-identical plus no `sorry` **in the proposed
body only** — this is field-standard practice, not local overengineering.

### K. Cross-tick lessons-learned + diversity injection [LANDED — both halves; see below]

Nexus episode discipline's missing half here: on a failed tick, write a
compressed post-mortem into the target's queue entry (obstruction family, last
error class, approaches tried); the next tick's drafter prompt includes it plus a
rotating diversity instruction ("decompose unsolved goals" / "combine ideas from
prior attempts" / "try a completely new approach" — their stochastic-injection
set). Today `refill-history.jsonl` records outcomes but each retry re-drafts
nearly blind; the cron already retries across ticks, so this converts existing
retries into informed ones at prompt-assembly cost only.

**Shipped** as `probe/experience.py` + wiring in both `vibe_prove` phases, after
the Axiomatic harvest supplied the missing mechanism
([`docs/research/2026-08-06-axiomatic-harvest.md`](research/2026-08-06-axiomatic-harvest.md)).
Their `ExperienceProcessor` answers the question this item left open — *how* to
carry lessons without the note growing into the prompt-eating log that killed the
idea the first time. The answer is a **rolling** summary: the summariser is fed
the prior notebook plus the new attempt and returns the REPLACEMENT, so the block
stays bounded (4000 chars) while nothing worth keeping is dropped. Recorded on
failures only (`max_rounds`/`fail_gate`; an `error` is an infra miss with no proof
lesson), rendered LAST in the prover task behind the premises (weakest authority),
silent on a cold store, fail-open to a mechanical digest at every step.
Diversity instruction is a deterministic rotation, not a sample — same effect,
reproducible in tests. Off by default; ON in `pipeline.toml` to MEASURE.
**Kill criterion:** `python3 vibe_prove.py experience` — if `retried targets`
stays 0, no attempt has ever read a notebook and this comes back out.

**Correction, same day.** The DRAFT half of this item was already built and I
missed it: `af_routing.load_prior_lessons` + `render_prior_lessons` have fed a
cross-tick post-mortem and a rotating `_DIVERSITY` nudge into the intent prompt
since before the harvest. So `experience.py` shipped the *prove* half of an item
whose own text specifies the drafter — the smaller half, on the census (3 of 22
obstructions are prove-side; all 3 are `cal-bk-144` retried three times).

What the harvest genuinely adds on the draft side is **accumulation**.
`load_prior_lessons` is stateless and derived fresh each tick, and its loop
OVERWRITES per issue: after four failed ticks the drafter sees tick 4's gate
names plus its `last_detail` truncated to 200 chars, and ticks 1–3 are gone.
That is exactly the repeat-offender case the census shows (#53, #72, #73, #108
each die across several families and ticks). The rolling notebook now runs
alongside it, keyed `issue-<n>` in the same store, bounded by re-summarisation
rather than truncation, with `nudge=False` so `render_prior_lessons` keeps
sole ownership of the diversity rotation. Non-verdict families are skipped on
the same `_LESSON_SKIP` rule, and a seeded issue has its notebook retired so a
later regression starts clean.

Both halves are now live; the draft side is where the failures are.

### L. Goal/disproof cache + hard timeouts [LANDED — `probe/gate_cache.py`, `gate_cache = true`]

Content-hash of the elaborated goal state → cache proof/disproof outcomes across
attempts and issues (gate probes first: vacuity/disproof re-run from scratch every
attempt today; Nexus caches disproofs too and substitutes on hit). Plus a hard
timeout on every prover/daemon call (elab-timeout exists; make it universal) —
their guard "to prevent the system from stalling on intractable or hallucinated
goals." Value scales with queue volume and with decompose's leaf count; behind
I/J/K until the census says otherwise. Synergy with D (CI pool).

### M. Instance-probe gate on the defs route [LANDED — `af_gates.instance_probe_rejection`]

Nexus's OEIS anti-misformalization guard, transplanted: for each new `def`, the
draft must include 1–2 concrete-instance `example`s (explicit small vector /
Finset) evaluating the definition against the issue's intended values, proved by
`decide`/`norm_num`-class tactics. Catches semantic slips (sign, normalization,
sup-vs-max, measure choice) that the faithfulness judge and the vacuity/disproof
probes both miss — exactly the class the #73 maxDD `∀c` episode exposed. Emit
into the seed next to the defs; gate on their elaboration like any stub content.

### N. Run-record provenance schema [LANDED — `schema/run_record.v1.json` + `probe/provenance.py`]

TauCetiData's shape, applied to our telemetry: one record per
(target × attempt × stage × model) with `prompt_sha`, `diff/candidate_sha`,
token usage, cost, and content-addressed transcripts; SQLite derived, never
authored. `runs/*.jsonl` + `refill-history.jsonl` already hold most fields —
this formalizes them so the obstruction census, the 09-30 price-sheet decision,
and any future meta-review all read one substrate. Schema files versioned like
theirs (`schema/*.v1.json`).

### O–Q. [main repo] Review-engine adoptions (home: values-review cadence, not this pipe)

Recorded here so the adoption round has one ledger; execution belongs to
`formal-mathfin`'s values-review/tests lanes:

- **O. Lens-decomposed adversarial review + contest protocol** — run the 8 lenses
  as independent single-lens reviewers with TauCeti's verdict semantics
  (block vs request-changes, blocking-first order) and contest-with-quoted-evidence,
  scoreboard comment per review. Their 10 rubrics are Apache-licensed prior art;
  adapt in our idiom, cite the source (external-source-not-template rule).
- **P. Meta-review on the live queue** — any rubric/lens/prompt change ships with
  a production-vs-shadow paired judgment (both presentation orders, cross-family
  judge panel, human escalation on splits). This is the "did the review change
  help" instrument, and it is live-queue by construction — consistent with the
  no-synthetic-bench doctrine.
- **Q. olean-level expose check** — augment
  `test_mathfin_module_files_expose_public_section` (textual today) with an
  artifact-level check à la TauCeti's `lake exe module-system` ("checked on the
  `.olean`, so a stray comment cannot fool it").

**Order:** I (the decision) with J folded in → K → then L/M/N as the census
directs. O–Q slot into the next values-review session main-repo side.

---

## 2026-07-31 — open-PR review round: the shipped artifacts, read back

Evidence: `docs/research/2026-07-31-pr-review-harvest.md`. Source is the four
autoform PRs standing open on `formal-mathfin` (#163/#164/#165/#167 for issues
#161/#162), read against the issues that seeded them. Every item below is a
measured gap between what a target asked for and what the pipeline shipped, not a
projection. All are **[no reasoner]**. The four PRs were consolidated into
formal-mathfin#169 (merged).

### R. Necessity prober [no reasoner; BUILT 2026-07-31 — `probe/strengthen.py`]

**Reproduction:** formal-mathfin#163 `gainToPain_nonneg_of_pain_pos` and #164
`upCapture_scale_invariant`.

*Traced.* Both guards pass the free filter: nothing else in either signature mentions
`h`, so dropping it leaves a well-formed statement and the binder reaches the re-prove
stage. And the discriminating fact — keeping the original proof after the drop FAILS on
both, because `have hden := h.le` and `field_simp [h]` reference the binder. That is
what separates this gate from `strengthen_candidate` and from a plain deletion probe,
and it is what the first writing of this item got wrong.

*Traced (daemon, v4.32.0) — and it caught a real gap in the shipped sweep.*

| reduced goal | result |
|---|---|
| #161 `0 ≤ gainToPain s r` | bare `positivity` **fails** ("failed to prove positivity/nonnegativity"); `unfold gainToPain; positivity` **closes** it |
| #162 `upCapture up (c • p) b = c * upCapture up p b` | `simp [upCapture]` **leaves** `(∑ c * p x) / ∑ b i = c * ((∑ p i) / ∑ b i)` — the scalar is still inside the sum and the division is unassociated |

So the sweep as first shipped fired on one of the two cases the gate was built for. The
unfold turns out to be load-bearing (positivity cannot see through a def to the
`posPart`/`negPart` sums underneath), and the list was missing an ALGEBRA slot carrying
`← Finset.mul_sum` + `mul_div_assoc` — the two rewrites the merged `upCapture_smul`
proof uses. Both are generic ring/BigOperators facts, so the slot stays general rather
than being that proof pasted in. Added, and pinned by
`test_the_sweep_carries_the_algebra_rewrites_the_trace_showed_it_needs`.

Worth noting what the convention bought here: the design was right and the *tactic list*
was half-wrong, which no amount of re-reading the design would have surfaced. Running
the trace cost ~20 minutes of daemon time and changed the shipped code.

**Correction to the first writing of this item.** It said "delete each binder and
re-elaborate with the same proof term; anything that still compiles was decorative".
That is the pass the pipeline **already had** — `autoformalize.strengthen_candidate`,
keyed on the elaborator's unused-variable warnings — and neither it nor a deletion
probe catches the cases cited below, because in both drafts the proof genuinely *uses*
the guard (`have hden := h.le`; `field_simp [h]`). No warning fires, and deleting the
binder while keeping the proof simply breaks it.

The distinction the gate actually has to draw is **used by this proof** vs **needed by
this theorem**. That requires re-proving without the hypothesis, which is what shipped:

- free filter — drop the binder, elaborate the statement alone with `sorry`. A binder
  the rest of the signature depends on (`{ι : Type*}` under `(s : Finset ι)`) fails
  here and never costs anything further.
- re-prove — a fixed tactic sweep (`positivity` → `simp [defs]` → `unfold; positivity`
  → `field_simp; ring` → `grind`) on what survives. **Daemon-only and zero tokens**,
  because the gate phase holds the Lean slot and the vibe harness is down; every tactic
  in the sweep is one the house rules allow in a merged source.
- accept only on a full re-gate, greedily, keeping the last gated state — so a
  strengthened statement is never held to a weaker bar than the original, and a failure
  anywhere keeps the proved original (fail-open).

Runs after the warning-driven strip, in `vibe_prove._cmd_gate`; `NECESSITY=0` disables.
It is a strengthening pass, not a rejection: what it emits is a strictly more general
theorem, so a target that trips it is improved rather than killed. Second-order, the
same probe shape catches over-strong typeclass assumptions.

**Open follow-up:** the sweep is a fixed tactic list, so it will miss hypotheses whose
removal needs a real proof. The general form is to feed the reduced statement back as a
target for the next vibe round; that needs orchestration across the daemon↔lsp flip and
is not built.

### R′. Original framing, superseded by R above

All 4/4 drafts asserted a hypothesis their own proof never used (`0 < ∑ r⁻` on
gain-to-pain nonnegativity, `∑ b ≠ 0` on upside-capture homogeneity). Neither
issue asked for it — #161 even names the closing lemmas — so this is drafter
invention. Both conclusions hold unconditionally, because in Lean `x / 0 = 0` and
`mul_div_assoc` carries no side condition. **Every existing gate passed it**: a
weaker theorem type-checks exactly as happily as a strong one, so kernel,
axioms, and judge are all blind here.

Build: after the proof elaborates, delete each signature binder in turn and
re-elaborate with the same proof term. Anything that still compiles was
decorative — drop it, re-run, emit the stronger statement. Model-free, seconds
per target on the daemon.

Not a re-litigation of the retired intent-fidelity gate (284e41f): that one asked
a semantic question two models can agree on while both miss an inert hypothesis;
this asks a syntactic question the elaborator settles. Second-order, it also
catches over-strong typeclass assumptions — `patterns.md`'s minimal-typeclass
rule made enforceable.

### S. Honour the issue's `location:` in emit [BUILT 2026-07-31]

**Reproduction:** issues #161/#162 both carry
`location: MathFin/Performance/RatiosExtended.lean`; the four emitted stubs carry it in
their `-- pointers:` header and set `-- main-module:` to a fresh module anyway. With
`extract_location` + `placement.append`, emit resolves to the named module and
`splice_into_module` merges into it instead of writing over it.

Both issues named `MathFin/Performance/RatiosExtended.lean`. All four drafts
minted a *new* one-lemma module instead — two targets, four modules, where zero
were asked for. The intent stage did capture it (every emitted file carries
`-- pointers: MathFin/Performance/RatiosExtended.lean`); emit overrode it with a
module named after the PR subject. When the issue declares a `location:` naming
an existing file, append to it; mint a module only when it is absent or missing.

### T. Ground-truth duplicate check before drafting [BUILT 2026-07-31]

**Reproduction:** on 2026-07-25 `pipeline_state.json` held no `cal-bk-161` attempt while
formal-mathfin#163 was open with `closes #161` in its body. `pr_claimed` matches that
body and skips the target; `queue_claimed` catches it from
`targets/queue/cal-bk-161.entry.json` even with `gh` unavailable.

#161 and #162 each produced two PRs five days apart. `select_issues` filters on
labels only; `next_target` dedupes against `attempted_issues` — a mutable file
written *after* the PR is opened, with no transactional link to the work it
guards, and already repaired once for this class of loss (`e1df178`). Add a
ground-truth query before drafting (open PRs referencing the issue, plus the
presence of a `targets/queue/` entry) and skip when either says the work exists.
Note the standing exposure: a passing tick leaves the issue `status:ready` until
a human merges, so every target awaiting review is re-selectable.

### U. Stamp provenance at run time; migrate the queued magistral entries [BUILT 2026-07-31]

**Reproduction:** `targets/queue/cal-bk-161.entry.json` carried
`statement_source: magistral-autoform` on 2026-07-31, two days after magistral left the
pipeline, with issue #161 still open and therefore re-selectable.
`sanitize_provenance` rewrites the fields and the prose `formalization_scope` claim at
the corpus boundary; `test_splice.py` fails if any queued entry names a retired drafter.

`targets/queue/cal-bk-161.entry.json` and `cal-bk-162.entry.json` still carry
`statement_source: magistral-autoform` / `statement_model: magistral-medium`,
baked in at enqueue. Magistral left on 2026-07-29; both entries are still queued
and both issues still open, so a re-pick emits a magistral claim for a
Claude-drafted artifact — a falsified record one tick away. `cal-bk-56` already
uses the generic `"autoform"`, so the convention moved and the old entries were
never migrated. Fix both halves: migrate the stale entries, and have the
producing stage stamp provenance from the resolved model id rather than the
target carrying it.

**Main-repo half — R's decision, not a patch.** `tools/formalization_yaml.py`
(:164, :238, :240) hardcodes "statement specified by Magistral" into the
generated public AI-disclosure file, and `tests/test_formalization_yaml.py`
(:113-115) asserts it. Accurate for today's corpus, false for the next entry. Not
mechanical, because the drafter is now Claude and standing policy is that Claude
is never attributed.

### V. Emit hygiene pass [BUILT 2026-07-31]

**Reproduction:** all four drafts opened `MeasureTheory ProbabilityTheory` and
`scoped NNReal ENNReal` on targets with no measure theory; #163/#167 added
`scoped BigOperators`, a no-op on the current Mathlib; #165 put `@[simp]` on two
`example`s and bound `(S : Type*)` explicitly. `trim_unused_opens` removes the first
three by elaboration; prelint A16/A17 fix the last two by parse.

Deterministic, all visible in the drafts: prune unused opens from the preamble
template (`open MeasureTheory ProbabilityTheory` / `open scoped NNReal ENNReal`
in all four, on targets with no measure theory; `open scoped BigOperators`, a
no-op on the current Mathlib, in two); lint attributes on `example`
(`PerformanceRatios.lean` puts `@[simp]` on two, inert); feed the destination
module's existing declaration names into the naming step
(`upCapture_scale_invariant` named a homogeneity claim after the three genuinely
*invariant* sibling lemmas in the same namespace); bind type arguments
implicitly (`gainToPain (S : Type*) (finset_S : Finset S)` forces
`gainToPain S finset_S r` at every call site).

**Status (2026-07-31): R–V all built.** Where they live:

| item | code | tests |
|---|---|---|
| R necessity prober | `probe/strengthen.py`, wired in `vibe_prove._cmd_gate` (`NECESSITY=0` off) | `probe/test_strengthen.py` |
| S location/splice | `assemble.splice_into_module` + `autoformalize.extract_location` + `-- append:` header | `probe/test_splice.py` |
| T duplicate backstop | `pipeline_lib.{queue_claimed,pr_claimed}` via `next_target(claimed_fn=…)` (`GH_GROUND_TRUTH=0` off) | `probe/test_pipeline_lib.py` |
| U provenance | `assemble.sanitize_provenance` at the corpus boundary; 4 queued entries + manifest migrated | `probe/test_splice.py` |
| V emit hygiene | `autoformalize.trim_unused_opens` (post-gate) + `_prelint_stub` A16/A17 | `probe/test_emit_hygiene.py` |

The main-repo half of U (`tools/formalization_yaml.py`'s hardcoded disclosure) is
handled separately — see that repo's `tools/formalization_yaml.py`, now derived from
per-entry provenance rather than pinned to a named pipeline.

**The bar, from the same round.** The best statement design reviewed was not
ours — formal-mathfin#166, an outside contribution, *derived* its
denominator-nonvanishing condition from `zcb_pos` instead of assuming it,
leaving one hypothesis. That is the exact inverse of R's failure mode, and it is
what the drafter's statement stage should be aiming at.
