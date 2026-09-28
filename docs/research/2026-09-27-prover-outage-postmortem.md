# The prover did not run from 2026-07-28 to 2026-09-27

**Status 2026-09-27.** The last real prover session was 2026-07-28 04:57 UTC (cal-bk-144).
Every tick after it either skipped with nothing selectable or crashed before the prover
started, and every scheduled run was green. The telemetry recorded fifteen `max_rounds`;
fourteen of them were a crash. Everything computed from that telemetry since — the
2026-09-01 decomposer postmortem's "easy targets exhausted", the A/B scoreboard, the
experience notebooks, the obstruction census's "prover-max-rounds leads (43)", the
2026-09-30 decision gate — measured the outage, not the prover or the targets.

Found by reading the GitHub Actions job logs rather than `runs/`. The crash is printed on
the line directly above every "result" it produced:

```
13:16:09.47 [leanstral-vibe] waiting for lean-lsp-mcp…
13:16:09.53 TypeError: build_system_prompt() missing 1 required positional argument: 'pack'
13:16:09.54 [vibe-run] cal-bk-83: captured 3436 bytes, sorry_removed=False
13:17:03    [vibe-gate] cal-bk-83: max_rounds
```

## Timeline

| window | what happened (Actions logs) | what was recorded |
|---|---|---|
| 07-17 .. 07-26 | the prover ran; four easy targets proved | 4 × pass |
| 07-27 .. 07-28 | the prover ran on cal-bk-144 (stochastic Fubini); real partial proofs, one `sorry` left | `max_rounds` — the last real session |
| 07-29 .. 08-18 | each tick drafted a stub; one stub that did not elaborate failed the **batch** manifest; nothing selectable | green "skip: no_unattempted_targets" |
| 08-19 .. 09-23 | `scripts/leanstral-vibe.sh:59` raised a TypeError before `vibe` started (14 ticks, and every decompose leaf) | 14 × `max_rounds`, leaves 0/N, green |
| 09-25 .. 09-27 | cal-bk-116 (a missing import) failed the batch manifest again | green skip; health printed STALLED, job green |

## The chain

1. **The crash.** `071a849` (2026-08-16) gave `house_context.build_system_prompt` a `pack`
   parameter and updated every Python caller and test. Three callers lived in shell
   strings: both launchers' `python3 -c "…build_system_prompt('$MAIN')"` and open-pr.sh's
   heredoc (`apply_contribution`, `ensure_umbrella_import`). Nothing parsed or ran them.
2. **The swallow.** `vibe_prove.run_vibe_target` ran the launcher with `check=False`,
   discarded its exit code, and read back the file. A launcher that died in under a second
   returned the untouched stub; it still had its `sorry`; `_cmd_gate` scored `max_rounds`.
3. **The escalation.** `max_rounds` escalated to the decomposer, whose leaf runs hit the
   same crash. Splits that passed their skeleton gate were retired as a `max_rounds`
   "remainder" with no prover turn. Splits that did not pass were rejected on a separate,
   deterministic bug: Claude pointed a leaf at the target's own not-yet-existing main
   module, one unresolvable import empties Lean's environment, and the gate read
   `unknown namespace MeasureTheory` — four ticks, and the scoreboard attributed them to
   "the REPL losing Mathlib", which its own environment canary had already ruled out.
4. **The blocked queue.** `build_manifest` validated the queue as a batch, and the drafter
   staged text nothing had elaborated (`check_fn=None`; the depth gate counts only its own
   marker error, the triviality gate reads any error as "not trivial"). One bad stub held
   the queue shut for three weeks, twice.
5. **The alarm that could not ring.** `health.py --fail-on-alarm | tee` ran under the
   default Actions shell, without `pipefail`: `tee` exited 0, so STALLED was printed on
   every run from 09-01 and the job stayed green. `health.py` also read only recorded
   outcomes, so the skips were invisible to it anyway.
6. **The tests.** ~750 unit tests, none run in CI (the only pytest in any workflow was the
   daemon-backed instance probe), none executing a script. `test_vibe_prove.py` asserted
   that a run which does nothing returns the unchanged stub — the failure path, pinned.

The 2026-09-01 postmortem named the pattern — *an instrument defect presents as a
result* — and missed its largest instance. The lesson is procedural: no conclusion about
the prover or the targets from `runs/` without the raw job log beside it.

## What changed (Phase 0, branch `fix/phase0-make-it-run`)

| defect | fix |
|---|---|
| launchers + open-pr broken by `071a849` | `house_context.py doctrine` CLI; open-pr passes the pack; `test_script_call_sites.py` binds every Python call embedded in `scripts/*.sh` and **executes both launchers** with fake docker/claude/vibe |
| `claude-prove.sh` committed 100644, never called, no `mcp__lean-lsp` allowed | executable; `[prover] engine` selects it (default); tool allow-list, stream-json transcript, doctrine as a file |
| a crash scored as a failed proof | `run_prover_session` keeps exit code, duration, transcript, launcher log; `classify_session` → `error` unless the prover demonstrably ran |
| nothing proves the path works | a canary (a one-step theorem, stub-shaped) runs through the same launcher first; failure turns the tick red and records nothing |
| daemon failures as verdicts | `_parse_daemon_response` flags the daemon's own `daemon error:` replies; `gate` returns `indeterminate` (→ `error`) instead of `compile_or_sorry` / `axiom_dirty` |
| substring forbidden-tactic screen (`hintS2`, `have hint`) | the target library's own list, word-boundary, comment-stripped |
| batch manifest | per-stub quarantine; exit non-zero only when the daemon cannot answer; stale manifest rebuilt before any refill |
| drafter staged unelaborated text | gate 0 elaborates the staged text; placeholder name rejected; stale scratch removed; `claude -p` failures keep their reason; imports of existing library modules kept; an existing main module refused |
| Leanstral retires 2026-09-30 | prover → Claude; `kernel_probes = false` (0 firings ever; after 09-30 every call would raise and **no draft could be staged**); golf opt-in |
| health could not fail the job | `shell: bash`; `runs/ticks.jsonl` records every tick; idle alarm after two ticks with no prover verdict |
| unit tests never ran | `.github/workflows/tests.yml`; tool versions pinned |

## Voided on 2026-09-27

- `pipeline_state.json`: the fourteen phantom `max_rounds` moved to `voided` (with the
  reason); the targets are selectable again.
- `runs/pipeline-*-summary.jsonl` (2026-08-19 .. 09-23): forty `max_rounds` rows
  reclassified `error`, the original kept under `reclassified`. The census now reads
  `infra-indeterminate 42, prover-max-rounds 3` (the real cal-bk-144 attempts).
- `runs/ab-decomposer.jsonl`: every row marked `void` with its cause. No row measured a
  prover.
- `runs/experience.json`: the prove-side notebooks, which described attempts that never
  happened, removed.
- Queue: cal-bk-116 retired (never elaborated, two theorems, main module = an existing
  file, likely mis-stated); cal-bk-66 removed (merged as `a056ac5`); cal-bk-79's
  `_agentic_placeholder` renamed.

## Not yet validated

Everything above is unit- and smoke-tested with fakes; none of it has met the real
runner. The first dispatched run must show, in order: the canary proved through
`claude-prove.sh` (MCP `connected`, Lean tool calls in the transcript), a real target
session with a non-empty transcript and tokens, and the gate on the real daemon. The
pinned Claude Code (2.1.197) and whether the runner shadows it are printed by the
install step.

## Next

The measurement that has never been taken: every queued target and every saved leaf
through the Claude prover, with transcripts, turns, tokens, cost and wall time. That is
the data the 2026-09-30 gate was waiting for, and it replaces it.
