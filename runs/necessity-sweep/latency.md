# Task 5 — what the arm costs, measured

Daemon: `mathfin-verify` lean-repl, container **freshly recreated** 2026-09-01 22:2x UTC
(a 4-day-old container took >4 min on a single trivial check before being replaced —
see `daemon-stability.md` for why container age is a variable here). Ceiling unchanged:
`.wslconfig memory=10GB`, `mem_limit: 6g`.

## The first entry priced the design, and found waste

| | power control | one binder probe | entry total |
|---|---|---|---|
| as the plan wired it | 347.5 s | 438.3 s | **13.1 min** |

Both on a theorem the sweep cannot prove at all. At ~43 s per daemon call that is 8 and
10 calls — the full eight-tactic sweep, every tactic failing, twice. Extrapolated to the
draw: **37.4 h** against a 12 h kill threshold.

The binder probe was pure waste: `sweep_report.rates` discards every record whose entry
failed the power control, so the run was paying 7.3 minutes per binder for rows the
analysis throws away by construction. Blind entries now short-circuit — their binders get
a `power_control_failed` record at zero cost, keeping the population countable. Same
entry, re-run: **306.4 s instead of 785.8 s.**

## Batching the sweep: A/B against the per-tactic prover

A daemon call is 35.7 s of which 33.8 s is `import MathFin`, so eight tactics in eight
calls re-elaborate the same imports eight times. `first | (t1; done) | (t2; done) | ...`
tries the same alternatives in the same order inside one elaboration.

Same three entries, both provers:

| entry | per-tactic | batched |
|---|---|---|
| `cm-thm-4.3.10` | 306 s | 134 s |
| `cm-prop-4.3.6` | 300 s | 201 s |
| `dist-exp-min` | 334 s | 193 s |
| **median** | **306.4 s** | **192.8 s** |

**Verdicts moved: 0 of 7 records.** Batching is verdict-preserving on this sample, which
is the only thing that licenses using it for the paper's instrument.

Speedup is **1.6×**, not the 5.8× the import arithmetic predicted — and that is the more
interesting number. If the import were the whole cost, batching would have collapsed it;
instead the eight tactics are doing ~12 s of real work each on these theorems. `grind`
and `field_simp; ring` genuinely grind before they fail. The import is a large constant,
not the whole story.

## Projection

At the batched rate, 135 power controls = **7.2 h**, inside the 12 h threshold for the
first time. Binder probes add to that only on *reachable* entries, since blind ones cost
nothing now — so the arm's cost is set almost entirely by the blind fraction, which is
also what decides whether the study has a denominator at all.

## The Mathlib arm

Task 5's rule: take the largest of {1000, 500, 250, 100} whose projection is ≤ 10 h, else
drop the arm. At 192.8 s per entry even n=100 is 5.4 h *on top of* the MathFin arm's 7.2 h,
and Mathlib declarations extracted without their `namespace`/`open` context are likely to
elaborate worse, not better. **The Mathlib arm is dropped on measured cost**, and spec §2.2
records that it was dropped rather than quietly omitted.

## Open: the blind fraction

Three of three pilot entries were blind — but those three were `continuous_martingales`
and `distributions`, because `--limit` slices a draw in corpus order rather than sampling
it (see the plan's Task 5 Step 2 amendment). A stratified 15-binder pilot is running to
get the real number. It decides more than cost: at near-total blindness there is no
denominator and the paper's headline claim has nothing to stand on.
