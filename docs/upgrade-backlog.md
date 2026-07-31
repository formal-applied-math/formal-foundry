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

### J. Statement-integrity pin on the main prove path [no reasoner; small — do with I]

Extend b078f9f's `_probed_conclusion` fidelity guard from the vacuity/disproof
probes to `run_target`'s REAL goal: count a prove pass only if the winning
candidate still asserts the stub's statement (exact-statement match or
elaborated-type hash). Today the gates are pinned but the main path still trusts
whatever file the prover returns — the same reversion loophole, one level up.
This is Nexus's mid-loop check that "permits sorry placeholders but verifies that
the original target theorem statement was not altered." Direct SP1-line
continuation; becomes more load-bearing the day the drafter/prover gets stronger (I).

### K. Cross-tick lessons-learned + diversity injection [no reasoner]

Nexus episode discipline's missing half here: on a failed tick, write a
compressed post-mortem into the target's queue entry (obstruction family, last
error class, approaches tried); the next tick's drafter prompt includes it plus a
rotating diversity instruction ("decompose unsolved goals" / "combine ideas from
prior attempts" / "try a completely new approach" — their stochastic-injection
set). Today `refill-history.jsonl` records outcomes but each retry re-drafts
nearly blind; the cron already retries across ticks, so this converts existing
retries into informed ones at prompt-assembly cost only.

### L. Goal/disproof cache + hard timeouts [no reasoner; volume-gated]

Content-hash of the elaborated goal state → cache proof/disproof outcomes across
attempts and issues (gate probes first: vacuity/disproof re-run from scratch every
attempt today; Nexus caches disproofs too and substitutes on hit). Plus a hard
timeout on every prover/daemon call (elab-timeout exists; make it universal) —
their guard "to prevent the system from stalling on intractable or hallucinated
goals." Value scales with queue volume and with decompose's leaf count; behind
I/J/K until the census says otherwise. Synergy with D (CI pool).

### M. Instance-probe gate on the defs route [drafter task; SP4-adjacent]

Nexus's OEIS anti-misformalization guard, transplanted: for each new `def`, the
draft must include 1–2 concrete-instance `example`s (explicit small vector /
Finset) evaluating the definition against the issue's intended values, proved by
`decide`/`norm_num`-class tactics. Catches semantic slips (sign, normalization,
sup-vs-max, measure choice) that the faithfulness judge and the vacuity/disproof
probes both miss — exactly the class the #73 maxDD `∀c` episode exposed. Emit
into the seed next to the defs; gate on their elaboration like any stub content.

### N. Run-record provenance schema [infra; low-medium]

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

### R. Redundant-hypothesis prober [no reasoner; small — do first]

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

### S. Honour the issue's `location:` in emit [no reasoner; one line]

Both issues named `MathFin/Performance/RatiosExtended.lean`. All four drafts
minted a *new* one-lemma module instead — two targets, four modules, where zero
were asked for. The intent stage did capture it (every emitted file carries
`-- pointers: MathFin/Performance/RatiosExtended.lean`); emit overrode it with a
module named after the PR subject. When the issue declares a `location:` naming
an existing file, append to it; mint a module only when it is absent or missing.

### T. Ground-truth duplicate check before drafting [no reasoner; small]

#161 and #162 each produced two PRs five days apart. `select_issues` filters on
labels only; `next_target` dedupes against `attempted_issues` — a mutable file
written *after* the PR is opened, with no transactional link to the work it
guards, and already repaired once for this class of loss (`e1df178`). Add a
ground-truth query before drafting (open PRs referencing the issue, plus the
presence of a `targets/queue/` entry) and skip when either says the work exists.
Note the standing exposure: a passing tick leaves the issue `status:ready` until
a human merges, so every target awaiting review is re-selectable.

### U. Stamp provenance at run time; migrate the queued magistral entries [no reasoner]

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

### V. Emit hygiene pass [no reasoner; small]

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

**Order:** R → S → T → U → V. R first: highest reproduction rate, no existing
coverage, and it strengthens what it touches rather than only rejecting.

**The bar, from the same round.** The best statement design reviewed was not
ours — formal-mathfin#166, an outside contribution, *derived* its
denominator-nonvanishing condition from `zcb_pos` instead of assuming it,
leaving one hypothesis. That is the exact inverse of R's failure mode, and it is
what the drafter's statement stage should be aiming at.
