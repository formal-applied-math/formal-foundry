# mathfin-foundry

Private operational repo for the **MathFin autoformalization operation**: it
points an LLM theorem-prover (Mistral's **Leanstral**) at open proof tasks, checks
the candidates against Lean's kernel, and surfaces the good ones for a human to
refine and merge into [`formal-mathfin`](https://github.com/raphaelrrcoelho/formal-mathfin).
If you've built agentic coding loops, this is one — an agent proposing code (a
Lean proof) into a compiler-feedback repair loop, where "compile passes" means a
proof kernel certified a gap-free proof and the prover is a **scout, not an
author**.

> **New here?** Read [`docs/overview.md`](docs/overview.md) first — the full map of
> both repos, the pipeline, and the outside reading to get fluent. Design of
> record: `formal-mathfin docs/superpowers/specs/2026-07-08-leanstral-foundry-design.md`.

## The pipeline at a glance

```
 TARGET  →  HOUSE DOCTRINE  →  LEANSTRAL  ⇄  LEAN ENV  →  ACCEPTANCE GATE  →  REFINERY  →  PR
 (issue)    (system prompt)    (the prover)  (checks it)   (kernel-clean)      (human)     (human)
```

A queued target is a `theorem … := by sorry` stub plus pointers to the modules it
should reuse. The house doctrine (values gate · house idioms · exact pins ·
"consume Mathlib/Degenne, don't reprove" · a per-target context pack) is injected
as the system prompt. Leanstral emits a candidate; on failure it is re-fed the
compiler errors and resends the whole file. A candidate passes the gate only with
no errors, no `sorry`, axioms ⊆ `{propext, Classical.choice, Quot.sound}`, and no
forbidden tactics. It is then a *candidate*, not a contribution — the refinery
rewrites it to the conceptually-right proof under the 8-lens bar before a human
merges it.

Full diagram: [`docs/leanstral-architecture.md`](docs/leanstral-architecture.md).
How the prover agents are equipped (context pack, loop, lean-lsp-mcp harness, PR
activation): [`docs/PROVER_SETUP.md`](docs/PROVER_SETUP.md). Why this shape is the
validated one: [`docs/research/2026-07-11-world-class-autoformalization-survey.md`](docs/research/2026-07-11-world-class-autoformalization-survey.md).

## Hard rules

- This repo **reads** `formal-mathfin`; it **never writes** to it. PRs into main are
  authored by R from the local clone after refinery, never by automation.
- API traffic (Mistral or any prover) carries only public-corpus statements and
  fresh textbook statements. Never held-out eval content (`formal-mathfin-evals`),
  never DMW/Dalang-named material.
- `MISTRAL_API_KEY` lives in `.env` (gitignored) or the shell env. Never committed.
- Machine proofs are scouts, not authors: nothing merges to main without the
  refinery (conceptually-right refactor + house idiom + 8-lens bar).
- **One Lean-loaded process at a time** (~10 GB box, ~4–5 GB per Mathlib env). REPL
  daemon XOR lean-lsp server; never both. See `formal-mathfin/CLAUDE.md`.

## Repo layout

| Path | What |
|---|---|
| `probe/` | the pipeline: `probe.py` (metered prover loop) · `pipeline.py` + `pipeline_lib.py` (cadence + token budgeting) · `house_context.py` (system-prompt assembly) · `build_manifest.py` (elaborate-with-sorry target validation) · `assemble.py` (corpus entry assembly) · `scout_index.py` (main-repo declaration index) · `issues.py` (issue sync) + tests |
| `scripts/` | shell entrypoints: `pipeline-tick.sh` (the cron prove step) · `open-pr.sh` (assemble + open the PR) · `contribute.sh` (manual contribution packet) · `leanstral-vibe.sh` (hands-on vibe + lean-lsp path) · `build-index.sh` (build the scout index) |
| `targets/` | `queue/` — validated targets (stub `.lean` + `.entry.json` sidecar + `manifest.json`, seeded from `status:ready`+`type:proof` issues) · `informal/` — informal statements |
| `index/` | the scout index of the main repo: `const_dep.jsonl`, `types.jsonl`, `PIN` |
| `scout-lake/` | a minimal Lake project used to build the index against the pins |
| `runs/` | per-tick telemetry JSONL + candidate `.lean` files |
| `reports/` | calibration reports |
| `docs/` | `overview.md` · `leanstral-architecture.md` · `PROVER_SETUP.md` · `research/` · `superpowers/` (specs + plans) |
| `pipeline.toml` · `pipeline_state.json` | cadence + token-budget config; spent-budget/attempt state |
| `.github/workflows/pipeline.yml` | the scheduled cron (one tick every `interval_days`) |

## Runbook

`MAIN_REPO` defaults to `/home/rapha/code/automated_proofs_quantfin`; the daemon and
Docker image live in the main repo. `MISTRAL_API_KEY` is sourced from the main
repo's `.env` locally (from the CI secret in Actions).

```bash
# All commands run from the foundry root.

# 1. Validate + activate the queue (needs the daemon; spends NO Leanstral tokens):
docker compose -f $MAIN_REPO/docker/docker-compose.yml up -d lean-repl   # in the main repo
( cd probe && python3 build_manifest.py --main-repo "$MAIN_REPO" )        # writes targets/queue/manifest.json

# 2. Run one tick now (proves the next target; needs the daemon up + the API key):
FORCE=1 scripts/pipeline-tick.sh

# 3. Hands-on path for a hard target (vibe + lean-lsp; takes the daemon down first):
scripts/leanstral-vibe.sh --agent lean -p "prove the sorry in MathFin/…; use lean_goal"
```

On a pass, the scheduled workflow assembles the proof and — **only with the
`MAIN_PR_TOKEN` foundry secret set** — opens a ready-for-review PR on
`formal-mathfin` that closes the source issue (first fully-autonomous green PR:
[#120](https://github.com/raphaelrrcoelho/formal-mathfin/pull/120), contango,
2026-07-11). Without the token it stops at candidate-notify and opens no PR.
Either way a human runs the refinery and owns the merge. Activation + the PAT
scoping: [`docs/PROVER_SETUP.md`](docs/PROVER_SETUP.md).
