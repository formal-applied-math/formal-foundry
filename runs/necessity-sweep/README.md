# necessity-sweep pilots — what each file is, and which are void

Pilot telemetry from 2026-09-01/02. **None of these is a result**; every one was cut
short or superseded, and they are kept because they are the evidence for decisions and
retractions made on the strength of them. Deleting them would leave those decisions
unsupported in the record.

| file | records | status |
|---|---|---|
| `pilot.jsonl` | 2 | **Superseded.** First catalogue pilot, killed after one entry. It priced the design and found real waste: a power control at 347.5 s plus a binder probe at 438.3 s on a *blind* entry, whose record `sweep_report.rates` discards on sight. Motivated the blind short-circuit. |
| `pilot2.jsonl` | 7 | **Superseded.** Same pilot re-run with the short-circuit: the same entry cost 306.4 s instead of 785.8 s. Stopped on discovering `--limit` slices a stratified draw by domain rather than sampling it. |
| `ab-batched.jsonl` | 7 | **VOID — do not cite.** The batched-prover A/B. It reported 0/7 verdicts moved and a 1.6× speedup, and both numbers are worthless: the daemon caps elaboration at 180 s and *kills the REPL* on overrun, so two of three batched power controls were truncated rather than completed. Every compared record was also a negative, where a timeout and a genuine failure are indistinguishable — the comparison structurally could not detect the one thing batching breaks. Retracted the same session. |
| `pilot-strat.jsonl` | 2 | **Superseded.** First stratified catalogue pilot, stopped once the population itself was found wrong: 99.4% of catalogued `full` entries are term-mode re-exports, so the sweep's blindness there is ~100% by construction. |
| `pilot-lib.jsonl` | 2 | **Superseded.** First library-arm pilot, stopped when the per-probe import cost was measured at ~200 s regardless of import shape — the ceiling, since diagnosed as an OOM hard kill (`daemon-stability.md`). |

Each `*.jsonl.meta.jsonl` sidecar records the corpus commit, arm, seed and tactic set the
run actually read, so a row can be traced to the population it came from.

**The measurement arm has never run.** An earlier version of this line blamed a daemon
memory ceiling; that diagnosis is retracted (see `daemon-stability.md`) — the deaths were
another session taking the Lean slot, and exit 137 is SIGKILL, which `docker stop`
produces on a healthy container. What the arm needs is an uninterrupted Lean slot, which
is a coordination problem rather than a hardware one.
