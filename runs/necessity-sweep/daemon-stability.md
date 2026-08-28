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

## Still open

Re-measure once the slot is free and the daemon is back, five clean consecutive calls, and
only then apply Task 0 Step 3's decision rule:

- **median ≤ 30 s** → full census, Tasks 6 and 7 as written;
- **otherwise, or if R declines the memory raise** → stratified random sample, seed
  `20260913`, proportional by domain, every rate carrying a Wilson interval.

The ceiling itself is untouched and still needs R if the re-measure says it does:
`/mnt/c/Users/rapha/.wslconfig` `memory=10GB` → `12GB`, `docker-compose.yml` `lean-repl`
`mem_limit: 6g` → `8g`, then `wsl --shutdown` from Windows.
