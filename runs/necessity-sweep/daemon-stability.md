# Task 0 — does the REPL survive consecutive checks?

## Attempt 1, 2026-08-27 23:41–23:45 UTC — CONTAMINATED, no verdict

Run at the **unchanged** ceiling (`.wslconfig memory=10GB`, `lean-repl mem_limit: 6g`,
`LEAN_NUM_THREADS=1`), because the plan's "before" table was ten days old and R's decision
2026-08-27 was to re-measure before spending a WSL restart on it.

| call | wall-clock | errors | infra |
|---|---|---|---|
| `example : 2+2 = 4 := by rfl` (no import) | 5.9 s | 0 | — |
| `import MathFin` #1 | 133.1 s | 0 | — |
| `import MathFin` #2 | 56.5 s | 1 | — |
| `import MathFin` #3 | 0.0 s | — | connection refused |
| `import MathFin` #4 | 0.0 s | — | connection refused |
| `import MathFin` #5 | 0.0 s | — | connection refused |

Respawns during the run: **1**.

**Why this is not a verdict.** Calls 3–5 did not time out and were not OOM-killed — they
were refused instantly, because `docker-lean-repl-1` **no longer existed**:

```
$ docker ps -a --format '{{.Names}}' | grep lean-repl     # nothing
$ docker logs docker-lean-repl-1
Error response from daemon: No such container: docker-lean-repl-1
```

A sibling session working in `formal-mathfin` took the Lean slot: it removed the daemon and
at 23:46 started `docker compose run --rm --entrypoint bash verify -c "lake build MathFin
&& lake lint"`. The container went away roughly ninety seconds *before* that command
launched, which is the shape of a deliberate slot handoff rather than a crash. Call 2's
single error and calls 3–5 are that teardown, not the memory ceiling. Only the first two
rows are evidence of anything, and two calls decide nothing.

This is the plan's own global constraint biting — *one Lean-loaded process at a time* —
from the other direction: `scripts/necessity-sweep.sh` refuses to start while a build holds
the slot, but nothing stops a build from taking the slot out from under a measurement
already in flight. Worth knowing before a multi-hour arm runs: **the sweep will lose the
daemon mid-run at least once.** It survives that by construction (append-only JSONL,
fsync per entry, `done_keys` resume, and `daemon_error` in no denominator), which is
exactly why those were built — but the run must be re-launched by hand afterwards.

**What the two clean rows do say.** The no-import baseline is 5.9 s against the docstring's
5–30 s promise, so the daemon itself is healthy. The first `import MathFin` cost 133.1 s
and the second 56.5 s — the second being much cheaper is the first sign that the Mathlib
environment *can* persist across calls, which the plan's ten-day-old table (110 s / 70 s /
258 s) never showed. Suggestive, not decisive: two calls, one of them during a teardown.

## Attempt 2, 2026-08-28 00:20–00:35 UTC — CLEAN, and it decides the population

Container recreated (`docker compose up -d lean-repl`) after the build vacated the slot,
then `wait_daemon.py` to READY, then the same six calls with nothing else on the box.
Ceiling still unchanged: `memory=10GB`, `mem_limit: 6g`.

| call | wall-clock | errors |
|---|---|---|
| `example : 2+2 = 4 := by rfl` (no import) | 1.9 s | 0 |
| `import MathFin` #1 | 10.8 s | 0 |
| `import MathFin` #2 | 55.2 s | 2 |
| `import MathFin` #3 | 35.7 s | 0 |
| `import MathFin` #4 | 81.5 s | 0 |
| `import MathFin` #5 | 32.7 s | 0 |

Median 35.7 s, mean 43.2 s. Respawns during the run: **2**.

**Step 2's bar, and the verdict against it.** The bar was *calls 2–5 in the 5–30 s band and
zero respawns*. Calls 2–5 are 55.2 / 35.7 / 81.5 / 32.7 — none of them in the band, median
45.5 s — and there were two respawns. **Fails both halves.**

**But it is four times better than the plan's table**, which recorded 110 / 70 / 258 s at a
~150 s median ten days ago. Nothing about the memory ceiling changed between those two
measurements; what changed is that this container is *freshly created*, against a warm
shared olean volume. Container age is a variable the plan did not have, and it is worth
more than the diagnosis credited. It does not rescue the census, though: two respawns in
five calls is still the diagnosed failure — the REPL dying and the next call re-paying the
import — merely less often. The two slowest calls (55.2 s, 81.5 s) are almost certainly the
two respawns, and call #2's two errors are what a reply looks like when the server dies
underneath it.

**Cost of the census at this latency.** The MathFin arm is 1,030 records. At the measured
35.7 s *per daemon call*, and each record costing between one and eight calls (the sweep
stops at the first tactic that closes), the census runs **10 h at the impossible floor of
one call per record, and 30 h at three** — against the plan's own 12-hour kill threshold.
Task 5's pilot would pin the real multiplier, but no plausible value rescues it.

## Decision: stratified random sample

Task 0 Step 3's rule was fixed before the data existed, and it fires cleanly — *not stable
(median > 30 s, respawns > 0) → sample rather than census*:

- **MathFin arm**: proportional stratified draw of 200 binders, seed `20260913`, drawn by
  domain, the seed and the per-domain draw both reported.
- **Every rate carries a Wilson confidence interval**, and the paper says it sampled.

The plan already argued this is the better claim on its merits — a rate with an interval,
rather than a point estimate from one library's census — so the instrument's limits and the
methodology point the same way here.

**The ceiling remains untouched and the decision remains reversible.** If R raises
`/mnt/c/Users/rapha/.wslconfig` `memory=10GB` → `12GB` and `docker-compose.yml` `lean-repl`
`mem_limit: 6g` → `8g` and restarts WSL, a third measurement could clear the 30 s bar and
put the census back on the table. Nothing built for the sample is wasted if it does: a
census is the sample with the draw removed.

## The slot is the binding constraint, not the memory

Both attempts were shaped by a sibling session in `formal-mathfin` taking the Lean slot —
it removed the daemon at 23:44, built at 23:46, and took it again at 00:41 for
`lake build MathFin MathFin.Blueprint blueprint_export && lake lint`. Attempt 2 fitted
between two builds. A 200-binder sample at this latency is still hours, so it *will* be
interrupted; `scripts/necessity-sweep.sh` refuses to start into a held slot, and the
resume path (append-only JSONL, fsync per entry, `done_keys`) is what makes the
interruption cost one entry. Plan the arm around losing the daemon, not around keeping it.


## Attempt 3, 2026-09-01 23:45 UTC — the ceiling is now the binding constraint

The library arm would not run: one entry in 11 minutes, the container log filling with
`uncaught exception in the Lean REPL — respawning`. Two import shapes were tried and the
difference between them measured directly, on a trivial `example : 2+2 = 4 := by rfl`:

| call | wall-clock |
|---|---|
| `import MathFin` (root, re-exports the library) | 193.8 s |
| `import MathFin.BlackScholes.DividendsGreeks` (own module) | 206.8 s |
| `import MathFin` again — warm? | 237.0 s |
| own module again — warm? | 186.4 s |

Respawns across those four calls: **3**.

**Two conclusions, both negative.** The import shape does not matter — every shape lands
at 190–240 s. And *there is no second call*: a repeat of the identical header is no faster
than the first, because the REPL dies and respawns between them and re-pays the import
every time. A shared root header had been introduced on the theory that it would keep one
environment warm across 149 modules; that theory is refuted here and the change is
reverted, since it bought nothing and the per-module import is the faithful environment.

**What this costs.** Every daemon call is ~200 s, for a theorem-free trivial example. The
import is the entire bill and it is paid on every call:

| | at ~200 s/call |
|---|---|
| one power control (8 tactics) | 0.4 h |
| the library arm's 343 power controls | **152 h** |
| a hypothetical one-call-per-binder instrument, 610 binders | **34 h** |

Against a 12 h threshold, **no instrument runs at this ceiling** — not the sweep, not a
single-call design, not a smaller sample that still answers anything. This is no longer a
question of how the sweep is built.

**It is Task 0 Step 1, and it needs R.** `/mnt/c/Users/rapha/.wslconfig` `memory=10GB` →
`12GB`, `docker/docker-compose.yml` `lean-repl` `mem_limit: 6g` → `8g`, then
`wsl --shutdown` from Windows. The diagnosis has been the same since 2026-08-17 — Mathlib
plus MathFin resident at ~4–5 GB inside a 6 GiB cap leaves under 2 GB of elaboration
headroom, so the REPL is OOM-killed and respawns cold — but it was optional while the
catalogue arm looked affordable. It is not optional now.

For contrast, attempt 2 on a freshly created container measured a 35.7 s median for the
same call. The instrument does not need the box to be fast; it needs the REPL to survive
long enough to answer twice in a row.


## 2026-09-09 — RETRACTED: the deaths were slot contention, not the memory ceiling

An earlier version of this section asserted that the daemon was being OOM-killed at
`mem_limit: 6g`, cited `OOMKilled: true` / exit 137, and called for raising `.wslconfig`
to 13GB and `mem_limit` to 9g. **Retracted 2026-09-09 on R's correction: another session
had taken the Lean slot and stopped the container.**

I did not verify that diagnosis. It came from a peer session's `docker inspect` reading
and I wrote it up as established mechanism. Two things should have stopped me:

* **Exit 137 is SIGKILL, not proof of OOM.** `docker compose stop` sends SIGTERM and then
  SIGKILLs a container that does not exit in time — producing exit 137 on a perfectly
  healthy container. Every death this evening is consistent with a slot flip.
* **I had first-hand evidence of contention and did not connect it.** Earlier the same
  evening I stopped a concurrent session's daemon myself, by smoke-testing
  `scripts/claude-prove.sh` with incomplete arguments: it performed the slot flip before
  validating its args. Two sessions were sharing one Lean slot all evening.

**What was actually observed**, separated from what was inferred:

| observed | inferred (wrong) |
|---|---|
| `ConnectionResetError`, malformed/truncated replies | the container hit its memory cap |
| exit 137 | it was OOM-killed rather than SIGKILLed by a `stop` |
| `unknown namespace MeasureTheory` after a restart | the heap was lost to OOM |

A container that is stopped and restarted loses its Mathlib heap exactly as an OOM-killed
one does, so every downstream symptom fits contention at least as well.

**What survives the retraction.** The `probe.py` fix is correct on its own terms and
unaffected: a malformed reply is infrastructure whatever killed the process, and it must
never read as a verdict about the submitted Lean. The same holds for the `skeleton_gate`
canary, the annotated cal-bk-80 row, and the annotated cal-bk-71 row — all of them turn on
*the environment was not intact*, which is true under either cause.

**What does not survive**: the claim that this box needs more memory, and the two-line
`.wslconfig` / `mem_limit` change. No memory change is called for on this evidence. The
real constraint is the one the doctrine already names — **one Lean process at a time** —
and the enforcement gap is that nothing stops a second session from taking the slot from a
run already in flight. `scripts/necessity-sweep.sh` refuses to *start* into a held slot;
nothing defends a run already underway.

**The one measurement here that stands** is attempt 2 (2026-08-28), taken with nothing
else on the box: median 35.7 s per `import MathFin` call on a freshly created container,
with 2 respawns in 5 calls. That is the number to plan against, and whether those two
respawns were memory or contention is now also open.

**Where that leaves the sweep's measurement arm.** Still unrun, and now honestly
undiagnosed. It is NOT blocked on a diagnosed memory kill with a known remedy — that was
the retracted claim. What it needs is an uninterrupted Lean slot for long enough to
finish, which on a box where a second session can flip the slot mid-run is a coordination
problem, not a hardware one.
