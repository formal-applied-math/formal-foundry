# Hands-off autoform → PR pipeline (design spec)

Date: 2026-07-11 · Repo: `formal-foundry` (main repo `formal-mathfin` stays independent)

## Goal

A functional, hands-off pipeline where the Leanstral autoformalization setup
**opens PRs on `formal-applied-math/formal-mathfin`, ready for review**, that expand
the formalization program. R evaluates and merges; the machine does everything
up to the merge button.

## Non-goals (this session)

- MathFin-Bench / pass@k measurement (item 5) — explicitly deferred by R.
- Changing the main repo's own build/CI/values gates — main stays independent;
  the foundry contributes only through PRs those gates judge.

## Decided forks

- **Decomposition driver**: Leanstral-only (no paid general-LLM dependency; $0
  until 2026-09-30).
- **PR autonomy**: fully hands-off — the foundry CI opens PRs on `formal-mathfin`
  using a granted PR-scoped token. Human is the merge gate.

## The blocker this fixes

Today the cron no-ops (`skip: no_unattempted_targets`) because `targets/queue/`
is empty, and even a passing candidate stops at a notification — nothing reaches
a PR. "Main gets PRs" = **seed real targets + wire candidate→PR + grant the
credential**.

## Architecture (data flow)

```
seed (authored stubs, faithful)         targets/queue/*.lean + manifest.json
   │  each stub carries: pointers, placement metadata, source-issue
   ▼
prove (probe.py, pass@k + ≤2 repair)     candidate .lean (elaborates, axiom-clean, no slop)
   │  daemon in foundry CI; Leanstral API
   ▼
assemble (assemble.py)                   edits on a main-repo checkout:
   │  place proof in MathFin/<...>.lean; wire benchmark JSON
   ▼
validate + regen (in foundry CI, mathfin-verify image)
   │  lake build MathFin; axiom_audit_gen --write; formalization_yaml --write;
   │  ledger verify (changed entry). GREEN-OR-ABORT.
   ▼
open PR (open-pr.sh)                      branch on formal-mathfin, commit (no
        gh pr create --label autoform     Claude attribution), push, PR w/ provenance
   ▼
R reviews (8-lens + CI gates) → merges
```

Never opens a red PR: if `lake build` or a gate fails during assembly, the run
files a "candidate needs manual placement" issue and stops (fallback to the
existing scout-not-author packet). Only green, ready-for-review PRs are opened.

## Components

### New

- **`targets/queue/*.lean` + `targets/queue/manifest.json`** — the seed. Each
  stub is a faithful statement ending in `:= by sorry`, carrying comment
  metadata:
  - `-- pointers: <repo-relative .lean paths>` (context pack; existing)
  - `-- main-module: MathFin/<Section>/<Module>.lean` (where the proof lands)
  - `-- benchmark: benchmarks/<file>.json` + `-- benchmark-id: <id>` (which
    entry flips to `full`)
  - `-- source-issue: <#n>` (provenance)
- **`probe/assemble.py`** — pure(ish): given `(candidate_code, target_meta,
  main_repo_path)`, compute and apply the file edits — replace the reduced proof
  in `main-module` with the candidate's proof body, and update the benchmark
  entry (`formalization_status → full`, `code.lean` refreshed). Returns a
  manifest of edits for logging. No git/network here (testable on a temp tree).
- **`scripts/open-pr.sh`** — the shell side: checkout `formal-mathfin` with the
  token, branch `autoform/<id>-<date>`, apply assemble edits, run
  validate+regen, commit (specific adds only; no attribution), push,
  `gh pr create` (non-draft, label `autoform`, body = provenance + 8-lens
  checklist in R's outbound style: lowercase, "i", short, no em-dashes).

### Changed

- **`probe.py` / `probe_lib.py`** — pass@k fan-out: per round sample `k`
  candidates (concurrent API calls; daemon checks serialize), pick the best
  failure (fewest errors) for ≤2 repair rounds; keep the token ledger, slop
  gate, and axiom guard. `k`, repair-rounds, per-call `max_tokens` become knobs.
- **`house_context.py` / `scout_index.py`** — richer context packs: add
  `dependency_closure(names, depth)` to walk const_dep; `_index_pack` injects the
  closure's exact signatures + (bounded) preceding-file source of the pointer
  files (miniCTX file-context result).
- **`build_manifest.py`** — parse the new `-- main-module: / -- benchmark: /
  -- benchmark-id: / -- source-issue:` lines into target metadata alongside
  `-- pointers:`.
- **`pipeline-tick.sh` / `.github/workflows/pipeline.yml`** — on `pass`, invoke
  `open-pr.sh` (with the token) instead of only notifying; the assemble+build+PR
  runs in the foundry CI runner (16 GB, mathfin-verify image) — never on the
  local 10 GB box.
- **`pipeline.toml` / `pipeline_lib.py`** — add `fanout`, `repair_rounds`,
  `tokens_per_attempt` knobs (item 2 rebudget: spend the 500k cap as fewer,
  bigger attempts).

### Staged (built + unit-tested, not wired live this session)

- `probe/decompose.py` (item 3 subgoal decomposition + item 8 variant warm-up),
  `probe/faithfulness.py` (item 6 gates), self-host search env in
  `leanstral-vibe.sh` (item 7). Behind the working pipeline; live-wired later.

## Infra / credential (the one R action)

- R creates a **fine-grained PAT** on `formal-applied-math/formal-mathfin` with
  `contents: write` + `pull requests: write`. I store it as the foundry secret
  `MAIN_PR_TOKEN` (via stdin, never argv/logged). `pipeline.yml` checks out main
  with it for the assemble+PR step. This is the only thing that grants the
  foundry write access to main; revoking the PAT fully disables auto-PR.

## Seed strategy

Start from the **tractable `reduced_core → full` gaps** (already catalogued as
main-repo issues): the theorem is already stated in a `MathFin/` file and only
the proof needs strengthening — so the statement is faithful by construction and
placement is an edit-in-place, not greenfield. Author ~6–10 such stubs; skip the
gaps that are reduced_core because the full result is genuinely hard (Novikov,
progressive measurability). A couple of roadmap-breadth statements can follow
once the loop is proven.

## Safety envelope

- **Quality wall**: probe enforces axiom-clean + no forbidden tactics; the PR
  runs main's full CI (build + `test_values` rfl-tripwire + ledger +
  formalization.yaml + AxiomAudit); R runs the 8-lens review before merge.
- **Promotion honesty**: a `reduced_core → full` flip includes a `#print axioms`
  / derivation check so a definitional-`rfl` "full" (the rfl-tripwire case) can
  never slip through.
- **Hygiene**: specific `git add` only (never `-A`/`.`); no Claude/Co-Authored-By
  attribution anywhere; PR body in R's outbound style.
- **Memory doctrine**: all heavy build/regen runs in foundry CI, never a second
  Lean process on the local box.
- **Reversibility**: PRs are non-draft but labeled `autoform` and human-merged;
  nothing merges without R; revoking `MAIN_PR_TOKEN` stops all auto-PR.

## Phasing

0. **Endpoint** — confirm newest Leanstral endpoint (`labs-leanstral-1-5` vs
   `labs-leanstral-2603`); set `DEFAULT_MODEL`.
1. **Functional pipeline** — seed queue (1a) + assemble.py/open-pr.sh + workflow
   wiring + token (1b). *This is the deliverable.*
2. **Yield** — pass@k + repair + rebudget (items 1, 2).
3. **Yield** — richer context packs (item 4).
4. **Staged** — decompose/faithfulness/warm-up/self-host (items 3, 6, 7, 8).

## Testing

All pure logic is unit-tested with injected `chat_fn`/`check_fn` and temp trees
(no Lean, no API, no network): pass@k selection + repair (`test_probe.py`),
assemble edits on a temp main tree (`test_assemble.py`), context-pack closure
(`test_house_context.py`), manifest metadata parse (`test_build_manifest.py`),
decompose/faithfulness (`test_decompose.py`, `test_faithfulness.py`). The
git/build/PR shell path is validated by a CI dry-run and the first real PR.
