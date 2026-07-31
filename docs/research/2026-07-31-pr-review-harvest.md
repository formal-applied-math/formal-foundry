# Open-PR review round → foundry hardening

Harvested 2026-07-31 from a review of the eight PRs standing open on
`formal-mathfin` — four of them autoform output (#163/#164/#165/#167 for issues
#161/#162). Unlike the grind-lessons harvest, this reads the pipeline's *shipped
artifacts* against the issues that seeded them, so every finding below is a
measured gap between what a target asked for and what the pipeline produced.

**Companion docs.** Research/evidence doc; actionable output folds into
`docs/upgrade-backlog.md`. The main repo's matching values-review entry is
`docs/values-review.md` → "2026-07-31 — corpus 344 — open-PR review round".

**Outcome in the main repo.** The four PRs were consolidated into one refined
change (formal-mathfin#169, merged) that closes both issues.

---

## 1. The spurious guard — the finding that matters

Every one of the four drafts asserted a hypothesis its own proof never used:

| target | drafted statement | what it needs |
|---|---|---|
| #161 gain-to-pain | `(h : 0 < ∑ s ∈ S, max (-(r s)) 0)` | nothing |
| #162 upside capture | `(h : ∑ s ∈ up, b s ≠ 0)` | nothing |

Both conclusions hold unconditionally. `gainToPain` is a quotient of two sums of
`max _ 0` terms, so `div_nonneg` closes it from two `Finset.sum_nonneg` calls;
`upCapture` homogeneity is `Finset.mul_sum` then `mul_div_assoc`, and
`mul_div_assoc` has no nonvanishing side condition. In Lean `x / 0 = 0`, so the
degenerate case is already inside the statement.

Three things make this the highest-value finding of the round:

1. **The issues did not ask for it.** Both #161 and #162 state the goal
   hypothesis-free, and #161 even names the proof (`div_nonneg` +
   `Finset.sum_nonneg`). The drafter *added* the guard. It is drafter-side
   invention, not target ambiguity.
2. **Every gate passed it.** Kernel-clean, axiom-clean, judge-approved, merged
   into a green CI. A weaker theorem type-checks exactly as happily as a strong
   one — no existing gate is even looking in this direction.
3. **It reproduced four times out of four,** across two targets and two
   independent ticks. This is a systematic reflex ("a division appeared, so
   guard the denominator" — true in ordinary mathematics, false in Lean), not a
   sampling accident.

### The fix: a redundant-hypothesis prober

Mechanical, model-free, and cheap. After a proof elaborates, for each binder in
the theorem's signature, re-elaborate the statement with that binder deleted and
the same proof term. Anything that still compiles was decorative — drop it and
re-run. On the daemon this is seconds per target, and it is a *strengthening*
pass: what it emits is a strictly more general theorem.

Worth noting against the retired intent-fidelity gate (284e41f, A/B verdict "no
marginal catch"): this is not that gate re-litigated. The fidelity judge compares
prose intent to Lean and asks "does this say the same thing?" — a semantic
question two models can agree on while both miss that a hypothesis is inert. The
prober asks a syntactic question the elaborator answers definitively. Different
instrument, different failure mode.

Second-order: the same prober catches over-strong typeclass assumptions, which is
the `SigmaFiniteFiltration`-not-`IsFiniteMeasure` rule in `patterns.md` made
enforceable.

## 2. The emitter ignores the issue's `location:` line

Both issues end with an explicit placement directive:

> `location: MathFin/Performance/RatiosExtended.lean` (beside sortinoRatio /
> informationRatio), re-export from the MathFin umbrella

All four drafts created a *new* module instead — `Performance/GainToPain.lean`,
`Performance/PerformanceRatios.lean`, `Performance/UpCapture.lean`,
`Performance/PerformanceRatiosExtended.lean` — each with its own umbrella import
line. Two targets, four modules, where the issues asked for zero new files.

The information was not lost in drafting: every emitted file carries
`-- pointers: MathFin/Performance/RatiosExtended.lean` in its header comment. So
the intent stage captured the location and the **emit** stage overrode it with a
module named after the PR subject. That is a one-line fix in emit: when the issue
declares a `location:` that names an existing file, append to it; only mint a new
module when the location is absent or names a file that does not exist.

Cost of not fixing it: module sprawl in the main repo, and it compounds — a
library of one-lemma modules is exactly the shape that makes later consolidation
expensive. It also loses the thing the issue was pointing at, which is that the
new ratio belongs *beside its siblings*, where the shared abstraction lives
(`RatiosExtended` already factors four ratios through one algebraic master).

## 3. Duplicate targets: the guard is a mutable file with no ground truth

Issues #161 and #162 each produced **two** PRs, five days apart:

| issue | first PR | second PR |
|---|---|---|
| #161 | #163 (2026-07-20) | #165 (2026-07-25) |
| #162 | #164 (2026-07-20) | #167 (2026-07-26) |

`pipeline_state.json` today records `cal-bk-161` and `cal-bk-162` once each, at
the 07-25 and 07-26 epochs. The 07-20 attempts are absent from `history`
entirely, so the state that `next_target` dedupes against had lost them by the
time the later ticks ran.

The structural problem, independent of which mechanism dropped those two rows:

- `probe/issues.py::select_issues` filters on labels and difficulty only. It has
  no notion of "this issue already has an open PR".
- `probe/pipeline_lib.py::next_target` dedupes against
  `state["attempted_issues"]` — a mutable file, written *after* the PR is opened
  (`record_attempt`), with no transactional link to the work it guards.
- So the only thing standing between a re-queued target and a duplicate PR is
  that one file surviving. It has already needed one repair for exactly this
  class of loss (`e1df178`, "recover run 29615562257's orphaned state").

Fix, cheapest first: before drafting a target, query ground truth —
`gh pr list --search "<issue-ref>" --state open` and the presence of
`targets/queue/cal-bk-N.entry.json` — and skip if either says the work exists.
State stays the fast path; the query is the backstop that does not depend on a
file surviving a race.

Note the interaction with the label convention: a passing tick opens a PR but the
issue stays `status:ready` until a human merges it, so the target remains
selectable for the entire review window. Any target awaiting review is exposed.

## 4. Provenance is stamped at enqueue, not by the stage that ran

`targets/queue/cal-bk-161.entry.json` and `cal-bk-162.entry.json` both carry:

```json
"statement_source": "magistral-autoform",
"statement_model": "magistral-medium",
```

baked into the queue entry. Magistral left the pipeline on 2026-07-29
(`docs/upgrade-backlog.md`; `pipeline.toml` now reads "No magistral, no
completion path" and `[drafter] claude_model` is the only drafter knob). Both
entries are still queued, and both issues are still open — so if either is
re-picked, the pipeline emits a `magistral-autoform` provenance claim for an
artifact Claude drafted. That is a falsified record, and it is one tick away.

A newer entry (`cal-bk-56`) already carries the generic
`"statement_source": "autoform"`, so the convention moved; the older entries were
never migrated.

Two fixes, both needed:

- **Migrate the queued entries.** Anything still in `targets/queue/` predating
  the cutover should drop the magistral strings.
- **Stop baking provenance at enqueue.** The field records *how the artifact was
  produced*, so the stage that produces it should stamp it, from the resolved
  model id at run time. Baking it into the target at enqueue records the
  pipeline as it was when the backlog was scanned.

**Main-repo counterpart, needs R's decision — not a foundry patch.**
`tools/formalization_yaml.py:164,238,240` hardcodes "statement specified by
Magistral" and `magistral-medium` into the generated `formalization.yaml`
automation disclosure, and `tests/test_formalization_yaml.py:113-115` *asserts*
those strings. That file is the public AI-disclosure artifact. It is accurate for
today's corpus — every autoform entry in it was drafted before the cutover — and
false for the next one. The reason this is not a mechanical fix: the drafter is
now Claude, and standing policy is that Claude is never attributed anywhere. So
what the disclosure should say is a decision, not a rename.

## 5. Smaller emit hygiene

Mechanical, all deterministic, all visible in the drafts:

- **Unused opens in every file.** `open MeasureTheory ProbabilityTheory` and
  `open scoped NNReal ENNReal` appear in all four drafts; neither target touches
  measure theory. Two of the four also carry `open scoped BigOperators`, which is
  a no-op on the current Mathlib. This looks like a fixed preamble template. It
  should be pruned to what the file's identifiers actually need — a one-pass
  check the emitter can do against the elaborated environment.
- **`@[simp]` on an `example`.** `PerformanceRatios.lean` attaches `@[simp]` to
  two `example` declarations. Examples are anonymous; the attribute is inert. A
  lint-level check for attributes on `example` costs nothing.
- **Naming against the target namespace.** `upCapture_scale_invariant` names a
  homogeneity claim `_scale_invariant`, in a namespace where
  `sortinoRatio_scale_invariant` / `treynorRatio_scale_invariant` /
  `informationRatio_scale_invariant` all mean the genuinely invariant
  `f (c • x) = f x`. The drafter had no view of the sibling names. Feeding the
  destination module's existing declaration names into the naming step is cheap
  and would have caught it — and `patterns.md` already warns about sign/shape
  confusion on exactly this pair of claims (2026-07-19).
- **Explicit type arguments.** `gainToPain (S : Type*) (finset_S : Finset S)`
  binds the type explicitly and names the finset `finset_S`, forcing
  `gainToPain S finset_S r` at every call site. Mathlib style is
  `{ι : Type*} (s : Finset ι)`.

## 6. Not a pipeline finding, but worth recording

The strongest statement design in the review round came from the **outside
contributor** (formal-mathfin#166, FRA): `P(0,T₂) ≠ 0` *derived* from `zcb_pos`
rather than assumed, leaving `δ ≠ 0` as the only hypothesis, with the generic
algebra stated once and instantiated on the existing curve. That is the
natural-generality discipline `patterns.md` asks for, arrived at without the
prompt. Worth holding as the bar for what the drafter's statement stage should
produce — and it is the exact inverse of finding 1.

---

## Backlog deltas

Ranked, for `docs/upgrade-backlog.md`:

1. **Redundant-hypothesis prober** in the gate chain (§1). Model-free, seconds
   per target, catches a failure mode with a 4/4 reproduction rate and no
   existing coverage. **[no reasoner]**
2. **Honour `location:` in emit** (§2). One-line fix; stops module sprawl.
   **[no reasoner]**
3. **Ground-truth duplicate check before drafting** (§3). Query open PRs + the
   queue rather than trusting state alone. **[no reasoner]**
4. **Provenance stamped at run time; migrate the queued magistral entries** (§4).
   Correctness-of-record, and one tick from firing. **[no reasoner]**
5. **Emit hygiene pass** (§5): prune unused opens, lint attributes on `example`,
   feed destination-module names into naming. **[no reasoner]**
