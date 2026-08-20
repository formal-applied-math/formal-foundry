# Autoformalization queue

The scheduled pipeline works this queue one target per tick, oldest-unattempted
first, and opens a PR on `formal-mathfin` that closes the target's source issue.
The queue is seeded from the **`status:ready` + `type:proof`** issue backlog
(`gh issue list --repo formal-applied-math/formal-mathfin`) — each issue's *Task* is
the statement and its *Pointers* are the context-pack modules.

## The issue is the state machine; this queue is a cache of it

The pipeline used to only READ issues. Five stores then answered "what work exists
and what is done" — the issues, these stubs, `manifest.json`, `pipeline_state.json`,
and open PRs — and a disagreement between any two of them was silent. Three outages
in one night came from that, the worst being six stubs staged here while every source
issue still said `status:ready`, so the refill kept re-drafting work already queued.

The pipeline now writes the state back, so the source carries it:

| transition | written by | meaning |
|---|---|---|
| `status:ready` → `status:in-progress` | `autoformalize.py` on a passing seed | a gated stub is staged here |
| `status:in-progress` → `status:review` | `open-pr.sh` after the PR is created | a PR is open against it |
| → closed | GitHub, on merge | terminal |

`select_issues` already filters `status:ready`, so this de-duplicates at the source
instead of in a local file that can drift. Every write is **fail-open** — a label is
bookkeeping and the proof is the work — so a missed transition costs one redundant
draft, which the local caches still catch. They stop being load-bearing; they do not
stop existing.

`probe/issue_state.py --repo <slug> --reconcile --queue targets/queue` rebuilds the
labels from the two witnesses (open PRs, stubs on disk) and is what makes "cache"
true rather than aspirational; the tick runs it with `--apply` before planning. It
never touches an issue holding a human status (`blocked-design`, `needs-triage`, …)
and never promotes an unlabelled issue into the backlog: it repairs the pipeline's
own writes, it does not create work.

## A target = a stub + a re-export sidecar

Per issue `#N` (worked example: `cal-bk-88.*` for #88):

- **`cal-<stream>-N.lean`** — the `MathFin` module stub: license header, `module`,
  `public import`s, a `## Result`-style docstring, `@[expose] public section`,
  `namespace MathFin`, exactly ONE `theorem … := by sorry`, `end MathFin`, plus
  the placement header comments read by `build_manifest.py`:
  ```
  -- pointers: <the issue's Pointers, repo-relative .lean paths>
  -- main-module: MathFin/<Section>/<Module>.lean   (where the proof lands)
  -- benchmark: benchmarks/<file>.json               (which corpus file gets the entry)
  -- benchmark-id: <new benchmark id>
  -- source-issue: N                                  (the PR closes it)
  ```
- **`cal-<stream>-N.entry.json`** — the re-export benchmark entry
  `assemble.py` appends to the corpus (the `mf-bs-put-formula` shape: `import
  MathFin.<Module>` + a thin `:= MathFin.<name> …`), carrying its **provenance**:
  ```json
  "metadata": { "provenance": { "source": "leanstral-autoform",
                                "model": "labs-leanstral-1-5", "issue": N } }
  ```
  `formal-mathfin`'s `formalization.yaml` generator counts these markers, so the
  automation disclosure stays mechanically honest (never a hand-set "0 merged").

## Faithfulness bar (the statement is R-curated, the proof is Leanstral's)

Author the stub statement to state EXACTLY the issue's formula — no vacuity, no
weaker restatement. Faithfulness is R's to confirm at merge; author conservatively.

Faithful is not sufficient: the statement must also be WORTH proving. Two ways a
faithful stub is still empty, both to reject at authoring time:

- **Instantiating a ∀-quantified corpus lemma.** If the target's proof is
  `MathFin.foo a b c` and `foo` binds those arguments with no hypothesis on them,
  the "theorem" is an application, not a result.
- **Restating a Mathlib lemma in finance names.** Search first (`scripts/loogle.sh`)
  and consume the library lemma instead; that is the coherence-first rule in
  `CLAUDE.md`, and it applies to autoformalized statements exactly as to hand-authored
  ones.

Retired on this bar:

- **#128** (Merton risk-neutral compensation, retired 2026-08-03). Its two theorems
  were one of each: `merton_lognormal_spot_recombination` instantiated
  `MathFin.integral_mertonSpot (S_0 k : ℝ)` at a concrete `k`, and the compensator
  identity `E[e^Y−1] = e^{m+δ²/2}−1` is Mathlib's
  `ProbabilityTheory.mgf_id_gaussianReal` (with `integrable_exp_mul_gaussianReal`
  for the side condition), both in `Mathlib.Probability.Distributions.Gaussian.Real`.
  The stub also carried two theorems where the shape above allows exactly one.
  The non-vacuous version of #128 needs jumps as actual random variables rather than
  `mertonSpot`'s abstract `k`, which is a corpus development, not a stub.

Removed as COMPLETED (a different thing from retired on the bar above):

- **#161 / #162** (`gain-to-pain`, `upside-capture`, removed 2026-08-18). Both were
  proved, merged as `formal-mathfin` PR #169, and their defs now live in
  `MathFin/Performance/RatiosExtended.lean`. A merged target's stub stops elaborating
  the moment the real declaration exists — `` `MathFin.gainToPain` has already been
  declared `` — and `build_manifest` validates the queue as a BATCH, so two finished
  targets were failing activation for every live one. That is how a freshly seeded
  `cal-bk-71` sat unactivatable behind work that had already shipped
  (run 32094892342).

  The queue holds work to do, not history: provenance for a merged target lives in
  the corpus, in `pipeline_state.json`'s history, and on the PR. Delete the pair
  (`.lean` + `.entry.json`) once its PR merges.

  Worth noting and NOT fixed here: batch-failing is brittle. One dead stub blocking
  every other target is a gate design question, not a stale-file question, and it
  should be decided on its own rather than in passing.

## Validate + activate (needs the daemon; does NOT spend Leanstral tokens)

```bash
docker compose -f docker/docker-compose.yml up -d lean-repl          # main repo
cd probe && python3 build_manifest.py --main-repo "$MAIN_REPO"        # elaborate-with-sorry check
```

`build_manifest.py` writes `targets/queue/manifest.json` (validated targets +
their sidecar `benchmark_entry`s). Until it runs, `manifest.json` is absent and
the pipeline safely no-ops. Then the cron (or `FORCE=1 scripts/pipeline-tick.sh`)
proves the next target and, with `MAIN_PR_TOKEN` set, opens the PR.

## Prioritized first batch (good-first / small, `status:ready`, `type:proof`)

| issue | area | statement |
|------:|------|-----------|
| **#88** | futures | contango/backwardation sign structure `F=S·e^{(r−δ)T}` (SEEDED here) |
| #108 | fx | covered/uncovered interest-rate parity `F = S·e^{(r_d−r_f)T}` |
| #109 | fx | Siegel's paradox `E[S_T]·E[1/S_T] = e^{σ²T}` |
| #53 | black-scholes | knock-in/knock-out parity: barrier-in + barrier-out = vanilla |
| #85 | actuarial | expected-value / variance / std-dev premium principles (loading ≥ 0) |
| #66 | fixed-income | vanilla IR-swap value + par swap rate |
| #67 | fixed-income | FRA value `δ·P(0,T₂)·(F−K)` + simple forward rate |
| #61 | fixed-income | Ho–Lee bond price `P(0,T)=A(T)·e^{−T·r₀}` |
| #27 | futures | Black-76 caplet / floorlet |
| #8  | black-scholes | third-order Greek "speed" `∂³V/∂S³` |

Author from the top down; skip `type:research` and `status:blocked-*` (harness
proves out on the easy tail first, per the Kimina/Goedel sample-efficiency read).
