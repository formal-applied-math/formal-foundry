# ML4TP Zulip harvest — what the field's best autoformalization/proving setups do, and where we stand

Date: 2026-07-17. Source: the leanprover Zulip **#Machine Learning for Theorem
Proving** channel (506 topics), 57 threads read in full + channel-wide searches
(`Leanstral`, `Mistral`, `Herald`, `finance`, `probability`, `Vibe`, `OpenGauss`,
`back-translation`), mined by four parallel harvest agents plus direct reads.
Complements — does not replace — `docs/research/2026-07-11-world-class-autoformalization-survey.md`
and `docs/upgrade-backlog.md`. Every finding below is tagged against the backlog:
**[known]** (already shipped/applied), **[parked]** (designed, deferred by the
"no general reasoner" decision), or **[new]** (this harvest adds it).

Grounded against our own code (`probe/`, `pipeline.toml`,
`docs/leanstral-architecture.md`) as of `fix/daemon-elab-timeout`. Where a harvest
agent over-escalated, the correction is noted inline.

---

## TL;DR — the honest answer to "is this overengineered / a poor effort?"

Partly, but not the way it feels. The pipeline is **sophisticated and largely
aligned** with the field (pass@k+repair, dependency-closure context packs, the
tokens-per-attempt retune, `leanstral-1-5`, kernel-replay verification, the parked
disprove filters — all correct and current). It is not a naive effort.

The zero-PR outcome is best explained by three things, in order of confidence:

1. **The per-theorem feedback loop is ~1–2 orders of magnitude too shallow.** Every
   one of the 13 proving systems surveyed that reports nonzero success runs a deep
   per-theorem loop that feeds the *actual compiler error + goal state* back into
   the next attempt — ax-prover-base defaults to **50 rounds**, Numina-Lean-Agent
   up to **20** (and **$50–1000 / 34 wall-clock-hours** on a single hard problem),
   Aristotle "iterates the feedback loop" as one of three headline scaling axes.
   Ours is `fanout=4 × repair_rounds=2`. It is a real loop (the "no loop at all"
   read is wrong), just far too shallow, and Delta Prover's measured scaling law
   says **depth beats breadth at equal budget** — so our budget shape leans the
   wrong way.

2. **The cron runs the wrong harness.** Our own architecture doc labels
   `vibe ⇄ lean-lsp-mcp` the "**trained-for**" production mode, but the 3-day tick
   runs the **text-loop `/chat/completions` calibration harness**. Leanstral 1.5
   was RL-trained on *agentic, tool-driving, filesystem-navigating* proof search;
   the cron never runs it that way.

3. **The one lever the field says matters most is the one we deferred.** The
   decomposition middle (sketch → subgoals → discharge → consolidate) is what
   carries every working system — Numina's ablation is **4/12 → 11/12 → 12/12** as
   it's added. That is our parked `[needs general reasoner]` item F. For a frontier
   domain, a leaf-prover with no planner above it is the ceiling.

None of this makes Track A (retrieval) or the autop scout *bad* — both are
independently validated by SOTA systems. The problem is we invested in **breadth**
(more retrieval sources, more fallback tactics, two model stages, cron
orchestration) while under-investing the **depth** every working system treats as
table stakes.

**Zero PRs is weak evidence of a broken pipeline.** Field base rates on hard/novel
targets: Aletheia 6.5% *meaningfully* correct, DeepMind 2.5% solved, StepFun-32B
~40% BEq *with a reference statement*, Leanstral-1.5 pass@1 28.9% on real FLT PRs.
One hard, novel, graduate-finance target per 3 days at a sub-floor budget has a
genuinely low expected hit rate. The cheapest way to tell "machinery broken" from
"task too hard + throughput too thin" is one experiment (bottom of this doc).

---

## The field's consensus shape (six load-bearing findings)

### 1. Model + Scaffold, and the scaffold barely matters vs the model + loop
Jason Rute (Mistral, Magistral author): the paradigm is **Model + Scaffold**;
"Leanstral is Leanstral model with **Vibe scaffold**, Claude Code is Opus/Sonnet
with Claude Code + Lean MCP + Skills." G-Research's controlled ablation ("Three
Horsemen", Nehal Patel) is blunter: AGENTS.md, MCP access, Claude skills, and NLP
proof hints "all make very little difference"; Claude used the skill on ~10% of
problems. The win is **a strong base model + a plain compile-and-check loop +
enough parallel tries** (213/220 PutnamBench off-the-shelf, ~3rd on the
leaderboard). **[new]** — direct evidence that our two bespoke tracks (Track A
retrieval injection, Track B1 tactic menu) are the category of scaffolding this
group found doesn't move the needle. We never ran the control of a raw strong agent
on our stuck targets.

### 2. Deep per-theorem feedback loops are non-negotiable table stakes
The single most consistent finding across all 13 systems. DeepSeek abandoned
whole-proof-one-shot going V1→V1.5 (RMaxTS tree search) in **Aug 2024**.
ax-prover-base's headline "pass@1" is a **50-round** propose→compile→read-goal→
repair loop. Delta Prover's scaling-law ablation (fixed `m×n` budget): accuracy
rises with **repairs-per-round**, not rounds — sequential depth beats parallel
breadth, advantage growing with budget. Numina's B4 case: closed in **5 iterative
rounds**, failed in **10 independent samples** at equal call budget. **[known,
under-tuned]** — we have the loop (`run_target` repair), it is just shallow, and
our `fanout=4 × 120k` budget shape favors breadth over the depth the data prefers.

### 3. Every working system has a decomposition middle we lack
Two-stage informal→formal is validated, but each working system inserts an explicit
**sketch/blueprint/subgoal** layer between "spec" and "prove". Numina-Lean-Agent
ablation: **4/12** without the informal-reasoning stage, **11/12** with it,
**12/12** once subgoal/subagent decomposition was added for the hardest problem.
DeepSeek-Prover-V2 (V3 decomposes, 7B discharges), Delta Prover (reflective
decomposition into a DSL), and Aristotle (informal proof → lemma sequence →
formalize each) all share `sketch → subgoals → discharge → consolidate`.
Magistral-specifies → Leanstral-proves has stage 1 and 3, **missing the middle**.
**[parked]** — this is backlog item F (DSP decomposition), deferred under "no
general reasoner". The harvest is strong evidence F is the biggest single lever.

### 4. Generalist frontier model in a loop now matches/beats bespoke provers
ax-prover-base (Claude Opus 4.5, **zero training**) beats Goedel-V2 (fine-tuned)
**54.7% vs 13.0%** on PutnamBench, at pass@1 (50-round loop) vs pass@184. Delta
Prover (Gemini 2.5 Pro, training-free) **95.9% MiniF2F**, beating
DeepSeek-Prover-V2-671B and nearly matching Kimina-72B. Numina-Lean-Agent (Claude
Code + MCP) **12/12 Putnam 2025**. On Mistral's own FLTEval, **Claude Opus pass@1
(39.6) beats Leanstral v1 even at pass@16 (31.9)**; Leanstral 1.5 pass@8 (43.2)
reverses it. **[new / strategic]** — the "no general reasoner" decision is the
strategic crux. The current winning shape is a frontier general model in a deep
agentic Lean-tool loop, not a leaf-prover called shallowly.

### 5. Statement faithfulness is the field's genuinely unsolved half
For our exact setting (open-domain NL→Lean, **no reference statement**):
- Back-translation + LLM-as-NLI-judge (Lean Workbook): ~5% of raw attempts survive
  fidelity filtering; ~50% failure on hard interpretation cases even after 6 rounds.
- Human 0–4 "correction effort" grading: GPT-4-class averages **2.238/4**.
- **BEq** (StepFun-Formalizer) — kernel-checked bidirectional `exact?` equivalence —
  is the field's most-trusted automated fidelity tool, **but it structurally
  requires a gold Lean statement to check against**, which is the whole thing we're
  generating. Best dedicated formalizers hit ~40% BEq@1 (6.9% on CombiBench).
- **Prove-or-disprove** (Aristotle/VERINA 96.8%; ~12% of "solved" were disproofs)
  and **hypothesis-rejection** need *no* reference — attempt `¬Concl` / `hyps ⊢
  False`. Faithful-to-a-flawed-source (Bolton Bailey's missing `k>0`; ProofNet's
  Sylow bug; Rute: **~25% of existing ITP benchmark problems are misformalized**)
  is caught *only* by this active probing, never by a translation-fidelity check.
**[parked]** — this is exactly backlog item C (disprove + hypothesis-rejection),
already designed. The "targets are R-curated so faithfulness is human-at-merge"
justification for parking it holds — but see recommendation R4.

### 6. Kernel-level verification integrity — we are ahead here
DeepSeek-Prover-V2's PutnamBench numbers were **inflated by a REPL warning-message
bug** (`apply?` + `Cardinal.toNat` suppressing `declaration uses 'sorry'`), caught
weeks later only via independent `SafeVerify`/`lean4checker` **kernel replay**.
Numina's public demo will **fabricate a bespoke `axiom`** when stuck (Eric Wieser:
`hausdorffMeasure_triangle_eq_heron_axiom`). Our `leanchecker` CI job + AxiomAudit
whitelist (`propext, Classical.choice, Quot.sound` only) + forbidden-tactic ban
(`native_decide`, `exact?`, `apply?`) operate at exactly the bar these failures
argue for. **[known — keep exactly as strict]**, and re-confirm the gate covers
autop/Leanstral-authored files, not just human-authored ones (it does, via
`test_values.py`).

---

## Where we stand, grounded in our code

| Piece | Field verdict | Our status |
|---|---|---|
| Two-stage specify→prove | validated shape (StepFun's two-capability split) | **[known]** Magistral → Leanstral |
| Decomposition middle (F) | **the biggest lever** (Numina 4→12) | **[parked]** — deferred (needs reasoner) |
| Per-theorem repair loop | table stakes, 20–50+ rounds | **[known, too shallow]** `fanout=4 × repair_rounds=2` |
| Depth > breadth at fixed budget | measured (Delta, Numina) | budget leans breadth (`fanout=4 × 120k`) |
| Trained-for agentic tool mode | how these models perform | **[new gap]** cron runs text-loop, not `vibe⇄lean-lsp-mcp` |
| Retrieval on unknown-identifier | standard (Delta Prover) | **[known]** Track A reactive, fails open to loogle |
| Proactive top-k injection | "very little difference" (G-Research) | **[known, questionable]** house_context injects closure |
| Autop static tactic menu | weaker than `lean_multi_attempt` | **[known]** last-resort scout, no feedback |
| Prove-or-disprove fidelity | needs no reference; catches ~25% misformalizations | **[parked]** backlog C, designed |
| Kernel replay + axiom whitelist | correct; SOTA labs shipped worse | **[known — ahead]** leanchecker + AxiomAudit |
| Failure-as-data (obstruction families) | widely recommended | **[known, partial]** logged to `runs/…-attempts.jsonl`, not triaged |
| Compute floor | $50–1000 / up to 34h per hard target | **[new]** we run ~500k tokens/tick — likely 1–2 orders low |
| Statement-integrity check tool | `comparator` (FRO), SafeVerify, LeanParanoia | **[new]** we hand-roll; could adopt `comparator` |

Correction to a prior belief: **AxProverBase's GitHub issue→PR automation is a
hosted product** (prover.axiomatic-ai.com beta), **not in the open-source repo**
(verified against the live repo tree — it's a plain `ax-prover prove Module:thm`
CLI with a `-o result.json` flag). So there is **no OSS prior art** for our
issue→PR glue; that remains bespoke by necessity, same as everyone.

---

## Reframing "zero PRs"

Do not read zero PRs as proof the machinery is bad. The dominant failure mode in
the field is **statement/intent mismatch on hard/novel targets**, and the base
rates are brutal even at the frontier:
- Aletheia (Erdős attempts): 68.5% fundamentally flawed, 31.5% technically correct,
  **6.5% meaningfully** correct.
- DeepMind's full agent: **9/353 (~2.5%)** hard open problems.
- StepFun-32B (purpose-trained, RL'd): **~40% BEq@1** *with* a reference statement.
- Numina's one real-paper formalization (Brascamp–Lieb): **two human experts, two
  weeks, still needed a cleanup pass** — "hands-off, one CI tick" is not what got
  anyone to a real result.

A cron proving **one** hard, novel, graduate-finance target per **3 days**, with a
leaf-prover, no decomposition reasoner, a 2-round repair loop, and a sub-floor
budget, producing zero PRs, is **consistent with a working pipeline on a task at
the frontier with tiny throughput** — not only with a broken one.

---

## Recommendations, ranked (cross-referenced to the backlog)

**R1 — [cheap, config] Deepen the loop; shift budget from breadth to depth.**
Delta/Numina both show sequential repair beats parallel sampling at fixed budget.
Try `fanout=2 × repair_rounds≥6–10` at the same total token spend before anything
else. One-line changes in `pipeline.toml`. (Backlog B is the budget lever; this
retunes its *shape*, not just its size.)

**R2 — [medium] Run the cron in the trained-for `vibe ⇄ lean-lsp-mcp` harness.**
The automation currently runs the calibration text-loop; the model was trained for
the agentic tool mode (live `lean_goal`, `lean_multi_attempt`, loogle/leansearch).
`leanstral-vibe.sh` already exists — the work is wiring it into `pipeline-tick.sh`
and resolving the one-Lean-process doctrine (daemon XOR lean-lsp), most naturally on
the 16 GB CI runner. Subsumes backlog A (`lean_multi_attempt` replaces the static
autop menu for free) and D (Kimina-style parallel REPL on CI).

**R3 — [strategic fork] Revisit "no general reasoner" for the decomposition
middle (F).** This is the biggest lever in the harvest and the one thing every
working system has that we don't. The evidence (Numina 4→12, DeepSeek-V2, Delta,
Aristotle) is now strong enough to reopen the 2026-07-11 decision. A general
reasoner drafts an informal proof → lemma DAG; Leanstral discharges the leaves. It
also directly automates R's hand-decomposition role. This is a genuine
architecture decision with cost/dependency/traffic implications — worth a dedicated
brainstorm, not a silent build.

**R4 — [cheap] Adopt the two zero-reference fidelity probes (C) as a safety net
even on R-curated targets, and add `comparator` to the PR gate.** Parking C on
"targets are curated" is defensible, but disprove/hypothesis-rejection is cheap
insurance against an accidentally vacuous stub, and the FRO's `comparator`
(declaration comparison + kernel replay) is a deterministic statement-integrity
check that costs nothing to run at PR-assembly time and catches redefinition
attacks an LLM judge can miss.

**R5 — [cheap] Triage the failure log into obstruction families.** We already write
`runs/…-attempts.jsonl` with per-attempt errors + `best_failure`; nobody reads it.
A small aggregator (unknown-identifier-despite-retrieval vs prover-gives-up-mid-
proof vs doesn't-typecheck vs times-out) would show *directly* which of the three
distinct fixes the zero-PR problem needs. Cheapest high-information thing to add.

**R6 — [cheap, adopt-not-build] Audit whether Track A duplicates lean-lsp-mcp's
bundled search.** lean-lsp-mcp v0.28.0 (2026-07-06) ships Loogle + LeanSearch +
Lean Finder + Hammer + Lean State Search as callable tools. If R2 puts the prover
in the vibe/MCP harness, confirm Track A's separate mistral-embed path isn't
duplicating retrieval sitting one config line away — and that the model is actually
*pointed at* those tools. (Note: no community tool does retrieval over a *small
in-house* corpus like MathFin, so Track A's core job has no off-the-shelf
replacement; the question is only about the Mathlib-facing overlap.)

**R7 — [practice] Ship a human-readable "statement-fidelity notes" artifact with
every autoformalized PR** (per the 5-color strong-majority project): what the issue
claims, what the Lean says, where they might diverge. Cheap, makes the judgment
auditable by someone who isn't the pipeline.

---

## The one experiment that settles "machinery vs task" (do this first)

Run two controls on the **same, deliberately easy** target — a near-restatement of
an existing MathFin/BrownianMotion lemma (guaranteed provable, guaranteed
faithful):

1. **Our pipeline** on the easy target. If it *still* fails → real pipeline bug,
   chase it with R5's triage. If it *passes* → the machinery works; the issue queue
   is too hard / throughput too thin, and R1–R3 (depth, trained-for harness,
   decomposition) are the levers, not "the pipeline is broken."
2. **A strong-general-agent control** (Claude Code or Codex + lean-lsp-mcp, no
   retrieval injection, no autop menu — just prove-and-iterate) on the same target,
   and on one currently-stuck real target. If the raw agent clears the stuck target
   our pipeline can't, that isolates the bottleneck to the model/loop layer (R2/R3),
   not the scaffolding.

Total cost is a few daemon sessions. It converts the zero-PR mystery into a
located fault before any redesign.

---

## Harvested systems — reference catalogue

**Provers / agents.** ax-prover-base (Opus 4.5, no training, 50-round loop, 54.7%
PutnamBench, AGPL, arXiv:2602.24273) · Numina-Lean-Agent (Claude Code + custom MCP,
12/12 Putnam, MIT, most structurally similar to us) · Delta Prover (Gemini 2.5 Pro,
training-free, 95.9% MiniF2F, arXiv:2507.15225) · Aristotle (Harmonic, >200B, MCGS
graph search, IMO gold, closed, arXiv:2510.01346) · Seed-Prover 1.5 (ByteDance,
closed, 11/12 Putnam) · DeepSeek-Prover-V2 (671B+7B open, subgoal decomposition,
arXiv:2504.21801) · DeepSeek-Prover-V1.5 (RMaxTS, arXiv:2408.08152) · Goedel-Prover
/ V2 (SFT, open weights) · Kimina-Prover (72B, Moonshot/Numina) · BFS-Prover-V2
(32B step-level, open) · C2C "Compile-to-Compress" (compiler-output self-correction
distillation) · Aleph Prover (closed, EBM claims, treat with skepticism).

**Fidelity / judging.** BEq (StepFun-Formalizer-32B, arXiv:2508.04440) · Lean
Workbook back-translation+NLI (arXiv:2406.03847) · RLMEval (Auguste Poiroux) ·
Evaluation Benchmark for Autoformalization in Lean4 (0–4 human grade,
arXiv:2406.06555) · prove-or-disprove (VERINA/Aristotle) · SafeVerify
(GasStationManager) · **comparator** (Lean FRO, Henrik Böving) · LeanParanoia
(Oliver Dressler).

**Tooling / infra.** lean-lsp-mcp (oOo0oOo, de-facto standard, bundles the search
tools + `lean_multi_attempt`) · cameronfreer/lean4-skills (standard Claude Code
skill) · Kimina Lean Server (parallel REPL pool + memcap + recycling — productizes
our daemon's just-patched failure mode) · Lean State Search (proof-state retrieval,
merged into official LeanSearchClient) · LeanDojo/ReProver (dead on Lean ≥4.12) ·
Pantograph (tactic-level + pickled state) · LeanTool + "Sorry Hammer"
(GasStationManager — near-sibling of our autop scout, has a negation-check we lack)
· SorryDB (real-world sorry-filling benchmark, co-built by LeanInteract's
maintainer — a possible external eval target) · OpenATP (henryrobbins — benchmarks
generalist agents against bespoke provers as peers).

**Practitioner blogs / talks.** G-Research "Three Horsemen"
(g-research-innovation.github.io/lean4-llm/blog3_three_horsemen) · Leanstral 1.5
(mistral.ai/news/leanstral-1-5) · Jason Rute, "Preparing for the next stage in
autoformalization" (ICERM).

**Domain note.** Quant finance has **zero presence** in this channel (one survey-
abstract hit). Research-level *probability* is wanted by several users, but Frauke
Harms: even o3/Claude 4/Gemini 2.5 Pro + MCP "does not help much if you already
know your way around mathlib." Our domain is genuinely novel — and genuinely at the
edge of what current systems do, which is context for the zero-PR count, not an
excuse for it.
