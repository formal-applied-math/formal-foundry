# Experiment Zero — locating the bottleneck (Phase 0)

Phase 0 of `docs/superpowers/plans/2026-07-18-autoformalization-improvements.md`.
The plan's own gate: run the discriminating experiment *before* building anything,
so the later phases are sequenced on evidence rather than on the shape of the
question. This report is that evidence, with an explicit verdict line.

## Method

Two controls, from the plan:

- **Control A** — an easy, guaranteed-provable, non-trivial target through the
  *full* pipeline (refill → draft + gates → vibe prove → gate → open-pr). If it
  produces a ready-for-review PR, the machinery is sound; if it dies, the failure
  is a located machinery bug.
- **Control B** — a strong general agent (a Claude Code session, one Lean process,
  in the main repo on a branch) on a target the pipeline has
  *repeatedly failed*. The reading: **B succeeds ⇒ bottleneck is the
  model/loop (drafting/reasoning) layer ⇒ Phase 2 (decomposition) is
  the priority; B also fails ⇒ bottleneck is target feasibility ⇒ Phase 1's
  feasibility census + issue curation is the priority.**

## Evidence base — the failure record (`runs/refill-history.jsonl`)

13 rows, all under `arch: routing-v1-2026-07-17`. Every recorded death:

| issue | outcome | family | where it died |
|-------|---------|--------|---------------|
| 53 | depth → formalize | needs_primitives / undraftable | depth-gate, then "no elaborating Lean"; hallucinated `MathFin.zcb` |
| 61 | formalize | undraftable | no elaborating Lean after 3 rounds |
| 66 | intent | undraftable | depth-gate, then no parseable intent reply |
| 73 | unfaithful → formalize | needs_primitives / undraftable | depth-gate, then "misrepresents Omega as Sharpe"; hallucinated `MathFin.omegaRatio` |
| 88 | formalize | undraftable | no parseable intent, then no elaborating Lean |
| 108 | depth → intent | needs_primitives / undraftable | depth-gate, then defs-route dead-end |

**The single structural fact:** every death sits at the **drafting/intent layer**
— `depth` / `intent` / `formalize` / `unfaithful`. **Not one** reached the prover
(`vibe`). The prover never received a valid draft to work on. This is the harvest's
central finding (`2026-07-18-mainrepo-grind-lessons-harvest.md`: "the funnel dies
at the drafter, not the prover") observed directly on the live queue.

Two of the deaths are **hallucinated constants**: #53 reached for `MathFin.zcb`
(a *fixed-income zero-coupon-bond* constant) on a **barrier-option** problem; #73
reached for `MathFin.omegaRatio`, which does not exist. The drafter is not merely
failing to prove — it is failing to *state*, and inventing vocabulary when it does.

## Control B — a strong agent on #53 (the decisive arm)

**Target:** issue #53, "Knock-in/knock-out parity: barrier-in + barrier-out =
vanilla." Labeled `good first issue` / `difficulty:good-first` / `status:ready`.
The pipeline failed it across four ticks (17:04, 20:13, 22:25 on 2026-07-17), each
time at the drafter, twice inventing `MathFin.zcb`, and recorded it as
`needs_primitives`.

**Feasibility, on inspection:** the issue body is explicit — this "needs **no**
barrier density — it is a pure linearity-of-expectation identity." There is **no
barrier module** in MathFin and none is required. The content is
`∫ 𝟙_hit·f + ∫ (1−𝟙_hit)·f = ∫ f`, lifted through linearity and discounted. The
pipeline's `needs_primitives` verdict was **false**: a feasible target mislabeled
infeasible because the drafter could not write the faithful statement.

**Outcome:** authored `MathFin/BlackScholes/BarrierParity.lean` — green, **0
sorries, 0 warnings, axiom-clean** (`[propext, Classical.choice, Quot.sound]`).
The proof is ~6 lines (`integral_indicator` + `integral_add_compl` +
`Set.indicator_self_add_compl`). Full delivery: benchmark entry
`mf-barrier-inout-parity` (`full`), coverage row, AxiomAuditGen pin, ledger row —
**closes #53.** Cost: one session; the mathematics was never the obstacle.

A secondary find that only surfaced by doing the work: **MathFin had no
present-value / discounted-expectation primitive at all** (grep-confirmed). The
parity development introduces `discountedValue D Q g = D·E_Q[g]`, the functional
the pricing files had only ever spelled inline — a reusable gap-fill, not a
one-off. (A drafter with statement-design authority would have wanted exactly
this; its absence is part of why the pipeline flailed.)

## Control A — machinery soundness

Not run as a fresh forced tick this session (it needs the paid Mistral pipeline;
that is an operator action, and it contends for the single local Lean slot with
Control B's daemon). It is, however, **already evidenced**:

- The refill-history shows every pipeline *stage* firing correctly — refill seeds,
  drafts, gates reject on the right criteria, outcomes are recorded per family.
  The gates are not silently passing garbage; they are killing bad drafts. The
  machinery *runs*.
- Two pipeline PRs **merged this month on suitable targets** — #66 (swap par
  identity) and #85 (loaded premium principles). The easy path demonstrably
  produces ready-for-review PRs.

A fresh Control A remains a cheap confirmation for the operator: force a tick on a
`status:ready` two-step corollary (e.g. #128 or #131) and confirm a PR, recording
tokens + wall-clock. It is not load-bearing for the verdict below.

## Verdict

**Control B succeeds where the pipeline repeatedly failed. The bottleneck is the
model/loop — specifically the drafter/intent layer — not target feasibility and
not the prover.** Phase 0's reading rule resolves to: **Phase 2 (decomposition) is
confirmed as the structural priority.**

The failure mode sharpens *which* Phase 1 item matters most. The pipeline is not
dying for want of proving power or of primitives; it is dying because it cannot
**state** a target faithfully in the repo's vocabulary. That is exactly the target
of **Task 1.2 — drafter authority (statement-design + pins)**, which becomes the
highest-leverage cheap win, ahead of the rest of Phase 1.

## Located bugs → tasks

1. **Drafter mislabels feasible targets as `needs_primitives`** because it cannot
   write a statement that consumes the right pointer-module defs. #53 is a false
   `needs_primitives`. → **Task 1.2** (statement-design authority; give the drafter
   the pointer-module surface + the house statement-design rules). This is the fix.
2. **Drafter hallucinates non-existent constants** (`MathFin.zcb`,
   `MathFin.omegaRatio`). → **Task 1.2** + **Task 1.8** (feasibility census: record
   `blocked_on_infra` with the *missing* list, no doomed draft) + **Task 1.3 (A2)**
   (unknown-identifier → grep the pinned source before re-drafting).
3. **The depth-gate cannot distinguish "genuinely needs primitives" from "drafter
   wrote the wrong statement."** It correctly rejects context-free statements, but
   both cases land as the same `depth` outcome. The feasibility census (Task 1.8)
   separates case (a) from case (b); the drafter authority (Task 1.2) removes case
   (b). No gate change needed — the gate is doing its job; the drafter is the
   defect. Confirms the depth-gate stays as a trust floor (do not loosen it).
4. **A repo gap the drafter cannot see:** no present-value primitive existed.
   Filled here (`discountedValue`). Suggests Phase 2's decomposer / Task 1.2's
   drafter prompt should surface "the def you want may not exist yet — take the
   defs route" as a first-class branch, not a hallucination trigger.

## Phase priorities — confirmed

- **Phase 1:** unchanged in scope, **re-ordered** so **Task 1.2 (drafter
  authority) leads** — it is the direct, cheap counter to the observed bottleneck.
  Task 1.8 (feasibility census) and Task 1.3-A2 (unknown-id repair) follow, as they
  convert the remaining drafter-failure families into honest recorded outcomes.
- **Phase 2:** confirmed as the structural priority (the bottleneck is the
  reasoning/drafting layer, which is precisely what the Magistral decomposer
  addresses). No reordering.
- **Phase 3/4:** unchanged.

**Acceptance (Phase 0): met.** The report exists with an explicit verdict; Phase 1
is re-ordered and Phase 2 confirmed on evidence; Control B produced a merged-quality
artifact (closes #53) as the bottleneck-locating control.
