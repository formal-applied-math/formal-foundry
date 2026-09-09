# Why the foundry produced nothing for five weeks

**Status 2026-09-01.** The loop runs on schedule and every CI run is green. It has
produced **4 candidates ever**, all between 2026-07-17 and 2026-07-26, and **nothing
since**.

| tick | target | outcome |
|---|---|---|
| 2026-07-17 | cal-bk-66 | pass |
| 2026-07-18 | cal-bk-85 | pass |
| 2026-07-25 | cal-bk-161 | pass |
| 2026-07-26 | cal-bk-162 | pass |
| 2026-07-28 | cal-bk-144 | max_rounds |
| 2026-08-19 | cal-bk-129 | max_rounds |
| 2026-08-23 | cal-bk-56 | max_rounds |
| 2026-08-27 | cal-bk-57 | max_rounds |
| 2026-08-31 | cal-bk-69 | max_rounds |

## The chain

1. The easy targets were exhausted in July. The four passes are the four autoformalized
   theorems merged into `formal-mathfin`; the queue that remains is harder.
2. The direct prover hits `max_rounds` on all of it.
3. **The decomposer — the one mechanism built for exactly that case — has never produced
   a leaf.** `leaves_total=0` on all seven invocations, 2026-07-27 to 2026-08-31. No
   `*.dag.json` and no leaf manifest has ever been written to `runs/`.

> **Superseded 2026-09-09.** True when written; false since 2026-09-03. Three CI ticks
> this checkout had not fetched show the decomposer routing splits: `cal-bk-71`
> (2 leaves, 09-03) and `cal-bk-82` (3 leaves, 09-07), both with a persisted
> `*.dag.json` and a leaf manifest on disk. `cal-bk-80` (09-05) still failed its
> skeleton gate, for the missing-opens reason `c7e9294` has since fixed.
>
> **Their `leaves_closed=0` is not evidence about the prover.** Those leaf stubs predate
> `f8ff98e`, so they carried none of the target's own definitions — the
> `lifeAnnuityDue_nonneg` stub references a `lifeAnnuityDue` that is defined in the
> target and exists in no importable module. The stub does not elaborate, so no prover
> could have closed it. Five leaf attempts across two targets, none of them a fair test.
> Read as a capability measurement, that zero would have driven an architecture decision
> off an instrument defect — the fifth time in two days that this failure shape nearly
> landed as a finding.

## Where the decomposer fails

Isolated 2026-09-01 by running the stages separately against `cal-bk-69`, the target that
failed most recently.

**The draft stage is healthy.** One `claude -p` call, 9,464 tokens, returned a valid
3-leaf DAG first try, no re-ask:

```
leaves = hasDerivAt_P_singleTenor, hasDerivAt_P_parallelShift, krd_sum_eq_ed
main   = hasDerivAt_P_singleTenor_and_parallelShift_and_krd_sum_eq_ed
```

So `fail_draft` is not the failure mode, and neither `max_leaves=3` nor the model is at
fault.

**It dies at the skeleton gate.** `skeleton_gate` passes only when the assembled skeleton
elaborates cleanly AND `sorry_count == n_leaves`. It correctly returns `indeterminate` on
a daemon infra error, which the shell records as `error` — and 2 of the 7 failures are
`error`. The other **5 are `max_rounds`, which means a real verdict**: the skeleton did
not elaborate, or the sorry count did not match the leaf count.

## Why five weeks passed without a diagnosis

Three compounding blind spots, all now closed:

1. **The reason was discarded.** `do_draft` computes a `reason` on every failure path and
   `record()` in `scripts/decompose-tick.sh` wrote outcome and counts only — the
   scoreboard's own `note` field sat empty. Seven failures, no attribution. *Fixed:
   commit `1154dcd`.*
2. **CI reported success throughout.** The tick exits 0 when the prover cannot close a
   target, which is correct — that is not an infrastructure error — so a green check has
   always meant "the machinery ran", never "the machinery worked". *Fixed: `probe/health.py`
   alarms on a barren streak of 3 and fails the job.*
3. **`private` declarations were unparseable.** `af_parse._DECL_RE` and
   `autoformalize._locate_named` did not accept `private`/`protected`/`nonrec`, so the
   locator raised "not found" on well-formed declarations and callers read that as
   unprobeable. Not the decomposer's failure, but the same class of fault: **an
   instrument defect that presents as a result.** *Fixed at source.*

## The verdict, and the fix

Elaborated against real Lean, the skeleton production has been building for five weeks:

```
passed: false   indeterminate: false   sorry_count: 3
VERDICT: skeleton does not elaborate: Unknown identifier `P` … `KRD` … `ED`
```

Two things settle it. `sorry_count: 3` equals the leaf count, so **the split was
structurally correct all along** — the decomposition was never the problem. And
`indeterminate: false` means this was a real verdict rather than a wedged daemon, which
is why the shell recorded `max_rounds` and the whole thing read as *these targets are too
hard*.

`P`, `KRD` and `ED` are defined **in the target stub itself**, in no importable module.
`assemble_skeleton` built the skeleton from the DAG's statements alone, so every leaf
statement referring to them was an unknown identifier. Imports failed the same way: taken
from the pointers the splitter declared, one of that target's three.

Fixed by `target_preamble` + `assemble_skeleton(..., target_text=...)`. That fix then
exposed a second layer — a `/--` doc comment starts with `/-`, so the line filter dropped
each comment's opening line and left its prose as bare text, producing `unexpected
identifier; expected command`. Comment state is tracked now rather than pattern-matched.

Same target, same DAG, after both fixes:

```
passed: true    indeterminate: false    sorry_count: 3
VERDICT: PASSED — the split is provable and the leaves can be routed
```

The skeleton goes from 1,322 B with one import and no definitions to 3,589 B with all
three of each. **This is the first time since the decompose path shipped that a real
target has produced a routable split.**

## The pattern worth keeping

Four defects today wore the same disguise — a killed batch reading as "no tactic closes
it", a cold-start loop as an unprovable theorem, an unlocatable `private` declaration as
blindness, and a mangled preamble as a broken definition. **An instrument defect presents
as a result.** The foundry was never short of failures, only of failures it could
describe, which is why the reason-plumbing and `probe/health.py` matter more than any
single fix here.

## Still open

The loop being able to route leaves is not the same as the loop closing them. The next
scheduled tick is the real test, and it can now say what happened either way.
