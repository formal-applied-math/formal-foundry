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

## Next

The skeleton gate's verdict on a freshly drafted DAG is the remaining unknown, and it is
one elaboration away. Two shapes it can take, with different fixes:

- **`sorry_count != n_leaves`** — the assembled skeleton is structurally wrong (the main
  proof is not reducing to leaf applications). A bug in `assemble_skeleton` or in the
  prompt's contract, and fixable here.
- **Elaboration errors** — the split is real but the leaf statements do not typecheck in
  the module context. That is a prompt/context problem, and the bounded re-decompose is
  supposed to absorb it with the errors as feedback.

Either way the loop stays barren until this is closed, because every remaining queue
target needs the decompose path.
