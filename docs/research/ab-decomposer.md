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

> **2026-09-27: every row below is VOID as a prover measurement.** No leaf was ever proved
> by a running prover — the leaf launcher crashed before starting (08-19 .. 09-23), and the
> rejected splits failed on assembly bugs. Mistral retires the Leanstral endpoint 09-30, so
> the prover is now `[prover] engine` (Claude by default) and each row carries `engine`.
> See [`2026-09-27-prover-outage-postmortem.md`](2026-09-27-prover-outage-postmortem.md).

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
| ts | target | arm | engine | outcome | leaves | tokens | refinery min | note |
|----|--------|-----|--------|---------|--------|--------|--------------|------|
| 2026-09-23T12:12:27 | cal-bk-91 | decompose | leanstral | max_rounds | 0/0 | 0 |  | VOID — the skeleton imported the target's own not-yet-existing main module (a leaf pointer); one unresolvable import em… |
| 2026-09-21T13:23:20 | cal-bk-83 | decompose | leanstral | max_rounds | 0/2 | 0 |  | VOID — the leaf prover never started (TypeError in leanstral-vibe.sh from 071a849); leaves_closed=0 measures the crash,… |
| 2026-09-19T12:16:10 | cal-bk-90 | decompose | leanstral | max_rounds | 0/0 | 0 |  | VOID — the skeleton imported the target's own not-yet-existing main module (a leaf pointer); one unresolvable import em… |
| 2026-09-17T12:36:10 | cal-bk-74 | decompose | leanstral | max_rounds | 0/0 | 0 |  | VOID — the skeleton imported the target's own not-yet-existing main module (a leaf pointer); one unresolvable import em… |
| 2026-09-15T12:53:57 | cal-bk-75 | decompose | leanstral | max_rounds | 0/0 | 0 |  | VOID — the skeleton imported the target's own not-yet-existing main module (a leaf pointer); one unresolvable import em… |
| 2026-09-11T12:19:58 | cal-bk-98 | decompose | leanstral | max_rounds | 0/2 | 0 |  | VOID — the leaf prover never started (TypeError in leanstral-vibe.sh from 071a849); leaves_closed=0 measures the crash,… |
| 2026-09-09T12:24:57 | cal-bk-93 | decompose | leanstral | max_rounds | 0/2 | 0 |  | VOID — the leaf prover never started (TypeError in leanstral-vibe.sh from 071a849); leaves_closed=0 measures the crash,… |
| 2026-09-08T22:33:05 | cal-bk-80 | decompose | leanstral | max_rounds | 0/0 | 0 |  | ENVIRONMENT ARTIFACT, not a split verdict — the REPL lost its Mathlib environment mid-run (container died 2 min later);… |
| 2026-09-07T12:47:22 | cal-bk-82 | decompose | leanstral | max_rounds | 0/3 | 0 |  | VOID — the leaf prover never started (TypeError in leanstral-vibe.sh from 071a849); leaves_closed=0 measures the crash,… |
| 2026-09-05T10:51:17 | cal-bk-80 | decompose | leanstral | max_rounds | 0/0 | 0 |  | VOID — skeleton assembly bug (the target's defs/opens were not carried; fixed f8ff98e/c7e9294); no leaf was attempted \… |
| 2026-09-03T11:36:58 | cal-bk-71 | decompose | leanstral | max_rounds | 0/2 | 0 |  | SUPERSEDED — not a prover measurement. These leaf stubs predate f8ff98e and carried none of the target's own definition… |
| 2026-08-31T14:03:08 | cal-bk-69 | decompose | leanstral | max_rounds | 0/0 | 0 |  | VOID — skeleton assembly bug (the target's defs/opens were not carried; fixed f8ff98e/c7e9294); no leaf was attempted |
| 2026-08-27T18:25:22 | cal-bk-57 | decompose | leanstral | max_rounds | 0/0 | 0 |  | VOID — skeleton assembly bug (the target's defs/opens were not carried; fixed f8ff98e/c7e9294); no leaf was attempted |
| 2026-08-25T07:52:28 | cal-bk-57 | decompose | leanstral | error | 0/0 | 0 |  | VOID — skeleton assembly bug (the target's defs/opens were not carried; fixed f8ff98e/c7e9294); no leaf was attempted |
| 2026-08-23T07:18:32 | cal-bk-56 | decompose | leanstral | max_rounds | 0/0 | 0 |  | VOID — skeleton assembly bug (the target's defs/opens were not carried; fixed f8ff98e/c7e9294); no leaf was attempted |
| 2026-08-19T12:59:08 | cal-bk-129 | decompose | leanstral | max_rounds | 0/0 | 0 |  | VOID — skeleton assembly bug (the target's defs/opens were not carried; fixed f8ff98e/c7e9294); no leaf was attempted |
| 2026-07-28T05:50:52 | cal-bk-144 | decompose | leanstral | max_rounds | 0/0 | 0 |  | VOID — skeleton assembly bug (the target's defs/opens were not carried; fixed f8ff98e/c7e9294); no leaf was attempted |
| 2026-07-27T01:50:16 | cal-bk-144 | decompose | leanstral | error | 0/0 | 0 |  | VOID — skeleton assembly bug (the target's defs/opens were not carried; fixed f8ff98e/c7e9294); no leaf was attempted |
<!-- SCOREBOARD:END -->
