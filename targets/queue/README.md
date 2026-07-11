# Autoformalization queue

The scheduled pipeline works this queue one target per tick, oldest-unattempted
first, and opens a PR on `formal-mathfin` that closes the target's source issue.
The queue is seeded from the **`status:ready` + `type:proof`** issue backlog
(`gh issue list --repo raphaelrrcoelho/formal-mathfin`) — each issue's *Task* is
the statement and its *Pointers* are the context-pack modules.

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
