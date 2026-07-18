# A/B scoreboard — does decomposition earn its tokens?

The running evidence for the **2026-09-30 decision gate** (keep-Magistral /
frontier-decomposer / hybrid). One row per real target-attempt per arm:

- **`cron`** — the plain draft→vibe-prove path (Task-1 hardened).
- **`decompose`** — the lemma-DAG loop (Phase 2): Magistral splits the target into a
  few leaf lemmas + a main theorem, a skeleton gate rejects a bad split for one
  elaboration's cost, the leaves are proved by the same vibe prover, and a
  recomposition gate assembles the whole.

**Both arms are Mistral.** There is no centaur/Claude arm — production is Mistral-only
(R decision 2026-07-18). R is the PR reviewer and an independent author of hard proofs,
not a tracked pipeline arm.

## The single question

> Does the decomposition loop close hard targets the plain `cron` path cannot, for the
> tokens it costs — and at what human-refinery cost per merged PR?

`leaves_closed/leaves_total` shows how much of a split actually proved (an honest partial
banks the proved leaves + declares the remainder). `refinery_minutes` is the unpriced
bottleneck: it is **filled by hand at merge**, not by the machine.

## Update protocol

- The decompose driver appends a row per attempt to `runs/ab-decomposer.jsonl` and
  refreshes the table below (`scoreboard.update_scoreboard_md`); the persist step commits
  the doc with the tick's other telemetry.
- At merge, edit the row's `refinery_minutes` (and `outcome` if the human review changed
  the verdict) by hand — the one field the pipeline cannot measure.
- By 2026-09-30 this table + the actual Labs price sheet decide the engine. If Magistral's
  absolute numbers are good enough, keep it; only if the call is close, run a one-time
  focused frontier eval (the `chat_fn` interface makes that a config swap, not a rewrite).

## Scoreboard

<!-- SCOREBOARD:START -->
| (no rows yet — first decompose attempt pending) |
<!-- SCOREBOARD:END -->
