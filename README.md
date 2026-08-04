# mathfin-foundry

Private operational repo for the **MathFin autoformalization operation**. It runs
a two-engine, self-feeding loop that turns an open proof *issue* on
[`formal-mathfin`](https://github.com/raphaelrrcoelho/formal-mathfin) into a
ready-for-review PR: a frontier reasoner (Anthropic's **Claude**) drafts and
faithfulness-gates a Lean *statement* from the issue — specifying the intent, then
writing the Lean **agentically** against the lean-lsp — and a leaf-prover (Mistral's
**Leanstral**) proves it — both checked against Lean's kernel, both **scouts, not
authors**. If you've built agentic coding loops, this is two of them chained: a
model proposing code (a Lean statement, then its proof) into compiler-feedback
repair loops, where "compile passes" means a proof kernel certified a gap-free
proof and a human still owns the merge.

> **New here?** Read [`docs/overview.md`](docs/overview.md) first — the full map of
> both repos, the pipeline, and the outside reading to get fluent. Design of
> record: `formal-mathfin docs/superpowers/specs/2026-07-08-leanstral-foundry-design.md`.

## The pipeline at a glance

```
 SELF-FEED  (when the queue has no unattempted target)
   ISSUE → CLAUDE specifies intent → CLAUDE formalizes agentically ⇄ lean-lsp → depth gate → triviality → kernel gates (vacuity·disproof, Leanstral) → judge (Claude) → STUB
 PROVE + SHIP
   STUB → HOUSE DOCTRINE (+ LIVE docs/patterns.md) → LEANSTRAL ⇄ LEAN ENV → GATE → REFINERY → PR
          (system prompt)                             (the prover)  (checks it) (kernel)  (human)  (human-merged)
```

**Self-feed (the refill phase, `probe/autoformalize.py`).** When the queue is empty,
the tick pulls the next `status:ready`+`type:proof` issue and drafts it with **Claude**
in **two stages**: **Claude specifies** the intended statement in precise prose (the
objects it must consume + naming meta), then **Claude formalizes** it **agentically** —
one `claude -p` session wired to the same lean-lsp MCP the prover uses, writing a
`theorem … := by sorry` against live diagnostics (`lean_diagnostics`/`lean_loogle`/
`lean_leansearch`) and self-validating to elaboration. A cascade of gates then guards
faithfulness before any proving budget is spent: elaboration, a **structural depth gate**
(a `run_cmd` meta check requiring the statement's TYPE to consume a def from its
`-- pointers:` MathFin modules — else it is a Mathlib identity in domain clothing, like
cal-bk-67 inlining the forward rate over raw reals instead of consuming `MathFin.zcb`;
pointers-scoped, so it skips when the issue cites none), a **triviality** check, two
kernel-grade **Leanstral** probes (**hypothesis-rejection** ⊢ False and **disproof**
⊢ ¬Concl → retire if provable), and a **Claude faithfulness judge**. A rejection feeds
its verdict back and re-drafts (semantic-repair cascade, ×2). A passing draft is staged
as a validated target; a rejected issue stays `status:ready`, never auto-closed. Honest
caveat: elaboration, the depth gate, and the Leanstral probes are *independent,
elaborator/kernel-grade* checks; the judge is a *soft Claude self-check* (statement vs
issue), not a faithfulness guarantee — the real authority is the human review at merge.

**Prove + ship.** A queued target is a stub plus pointers to the modules it should
reuse. The house doctrine — values gate · **the live `docs/patterns.md` injected in
full (a first-class requirement)** · exact pins · "consume Mathlib/Degenne, don't
reprove" · a per-target context pack — is the system prompt. Leanstral emits a
candidate; on failure it is re-fed the compiler errors and resends the whole file.
A candidate passes the gate only with no errors, no `sorry`, axioms ⊆
`{propext, Classical.choice, Quot.sound}`, and no forbidden tactics. It is then a
*candidate*, not a contribution — the refinery rewrites it to the
conceptually-right proof under the 8-lens bar before a human merges it.

**Two proving paths (Phase 2).** The scheduled cron's stated job is **calibration +
easy-harvest** — it proves targets a single vibe session can close and keeps the pipeline
honest against the live queue. A **hard** target instead takes the **lemma-DAG path**:
**Claude splits** it into a few leaf lemmas + a main theorem, a skeleton gate rejects a
bad split for one elaboration's cost, the same vibe prover (Leanstral) proves the leaves,
and a recomposition gate assembles the whole. The path is **on**
(`pipeline.toml [decompose] enabled`), routed by the `decompose` tag, a
`workflow_dispatch` flag, or **autonomous escalation** — a plain attempt that can't close
its target escalates that same target to the split. Whether decomposition earns its
tokens is tracked on the live queue in
[`docs/research/ab-decomposer.md`](docs/research/ab-decomposer.md).

**Cadence, and what actually constrains it.** One tick every `interval_days` (currently
**2**, cron `17 6 */2 * *`). The binding constraint is not tokens but the **2000-min/month
Actions quota on a private repo**: a productive tick costs 46–85 min, so ~15 ticks/month
leaves about half the quota for hands-on `workflow_dispatch` runs, which measured **85% of
the spend** over the last 30 days. Note the due check carries
`DUE_GRACE_SECONDS` slack — `last_tick_epoch` is stamped when a run *records*, i.e. the
cron minute plus the run's duration, so a whole-day threshold would put the next firing
just short of the interval and silently halve the cadence.

**What the loop remembers between ticks.** Two content-addressed stores under `runs/`,
both committed by the persist step and both dropped wholesale on a Mathlib/Lean pin bump:

- `gate-cache.json` (`[autoformalize].gate_cache`) — addresses whole adversarial gate
  goals (vacuity ⊢ False, disproof ⊢ ¬C) and substitutes the cached verdict on a hit, so
  a stuck issue that keeps redrafting the same statement stops re-paying for the probes.
- `state-cache.json` (`[autoformalize].state_cache`) — the same idea one level down:
  after a proof passes, the gate phase replays it prefix by prefix on the daemon it
  already owns, reads the goal at a spliced `sorry`, and records each
  `(state → the tactic that advanced it)` under a **normalized** key (metavariable
  numbering and inaccessible-name daggers collapsed; a raw hash of goal text almost never
  hits). This is currently a **measurement**: consumption is self-gating, since only
  states reached by two *different* targets are ever offered to the prover, so until
  cross-target recurrence is nonzero the prompt is unchanged. Read it with
  `python3 vibe_prove.py states`; if the number stays zero, the feature is ceremony and
  comes back out.

Full diagram: [`docs/leanstral-architecture.md`](docs/leanstral-architecture.md).
How the prover agents are equipped (context pack, loop, lean-lsp-mcp harness, PR
activation): [`docs/PROVER_SETUP.md`](docs/PROVER_SETUP.md). Why this shape is the
validated one: [`docs/research/2026-07-11-world-class-autoformalization-survey.md`](docs/research/2026-07-11-world-class-autoformalization-survey.md).
Decomposition mechanics: [`docs/superpowers/specs/2026-07-18-decomposer-design.md`](docs/superpowers/specs/2026-07-18-decomposer-design.md).

## Hard rules

- This repo **reads** `formal-mathfin` — its issues, and its live sources for the
  context pack, the pins, and the first-class `docs/patterns.md`. It may **open a
  ready-for-review PR** (a branch, with `MAIN_PR_TOKEN`) but **never merges**: every
  PR is human-reviewed (refinery + 8-lens) and R owns the merge. Nothing reaches
  `main` without a human.
- API traffic (Claude, Mistral, or any prover) carries only public-corpus statements
  and fresh textbook statements. Never held-out eval content (`formal-mathfin-evals`),
  never DMW/Dalang-named material.
- `MISTRAL_API_KEY` (Leanstral + `mistral-embed` retrieval) and
  `CLAUDE_CODE_OAUTH_TOKEN` (the Claude drafter) live in `.env` (gitignored) or the
  shell env. Never committed. With no Claude auth the drafts defer (no fallback drafter).
- Machine proofs are scouts, not authors: nothing merges to main without the
  refinery (conceptually-right refactor + house idiom + 8-lens bar).
- **One Lean-loaded process at a time** (~10 GB box, ~4–5 GB per Mathlib env). REPL
  daemon XOR lean-lsp server; never both. See `formal-mathfin/CLAUDE.md`.

## Repo layout

| Path | What |
|---|---|
| `probe/` | the pipeline (34 test modules, 511 tests, all daemon-free): `probe.py` (metered prover loop) · `vibe_prove.py` (the live prove path: headless vibe ⇄ lean-lsp, then the daemon-phase gate) · `autoformalize.py` + `af_parse`/`af_prompts`/`af_routing`/`af_drafting`/`af_gates` (the issue→stub refill, split into focused modules re-exported through `autoformalize`) · `decompose.py` + `decompose_tick.py` (the lemma-DAG path) · `gate.py` (the kernel-grade candidate gate) · `strengthen.py` (drop hypotheses the theorem does not need) · `gate_cache.py` · `state_cache.py` + `proof_states.py` (proof-state addressing + recurrence measurement) · `pipeline.py` + `pipeline_lib.py` (cadence + token budgeting) · `house_context.py` (system-prompt assembly, injects the live `docs/patterns.md`) · `build_manifest.py` (elaborate-with-sorry target validation) · `assemble.py` (corpus entry assembly) · `scout_index.py` + `embed.py` (declaration index + embedding retrieval) · `issues.py` (issue sync) |
| `scripts/` | shell entrypoints: `pipeline-tick.sh` (the cron prove step) · `decompose-tick.sh` (the lemma-DAG tick) · `open-pr.sh` (assemble + open the PR) · `contribute.sh` (manual contribution packet) · `leanstral-vibe.sh` (hands-on vibe + lean-lsp path) · `slot-switch.sh` (the daemon ⇄ lean-lsp flip) · `build-index.sh` / `build-embeddings.sh` (scout index + embedding cache) |
| `targets/` | `queue/` — validated targets (stub `.lean` + `.entry.json` sidecar + `manifest.json`, seeded from `status:ready`+`type:proof` issues). Its [`README`](targets/queue/README.md) carries the authoring bar, including the two ways a *faithful* stub is still empty: instantiating an already-∀-quantified corpus lemma, and restating a Mathlib lemma in finance names. · `informal/` — informal statements |
| `index/` | the scout index of the main repo: `const_dep.jsonl`, `types.jsonl`, `PIN` |
| `scout-lake/` | a minimal Lake project used to build the index against the pins |
| `runs/` | per-tick telemetry JSONL + candidate `.lean` files |
| `reports/` | calibration reports |
| `docs/` | `overview.md` · `leanstral-architecture.md` · `PROVER_SETUP.md` · `research/` · `superpowers/` (specs + plans) |
| `pipeline.toml` · `pipeline_state.json` | cadence + token-budget config; spent-budget/attempt state |
| `.github/workflows/pipeline.yml` | the scheduled cron (one tick every `interval_days`, currently 2 days) |

## Runbook

`MAIN_REPO` defaults to `/home/rapha/code/automated_proofs_quantfin`; the daemon and
Docker image live in the main repo. `MISTRAL_API_KEY` (Leanstral + embeddings) and
`CLAUDE_CODE_OAUTH_TOKEN` (the Claude drafter) are sourced from the main repo's `.env`
locally (from the CI secrets in Actions).

```bash
# All commands run from the foundry root.

# 1. Validate + activate the queue (needs the daemon; spends NO Leanstral tokens):
docker compose -f $MAIN_REPO/docker/docker-compose.yml up -d lean-repl   # in the main repo
( cd probe && python3 build_manifest.py --main-repo "$MAIN_REPO" )        # writes targets/queue/manifest.json

# 2. Run one tick now (needs the daemon up + the API key). If the queue has no
#    unattempted target, the tick first SELF-FEEDS — autoformalizes a stub from the
#    next ready issue (Claude draft + faithfulness gates → build_manifest) — then
#    proves it. Toggle with [autoformalize].enabled in pipeline.toml.
FORCE=1 scripts/pipeline-tick.sh

# 3. Hands-on path for a hard target (vibe + lean-lsp; takes the daemon down first):
scripts/leanstral-vibe.sh --agent lean -p "prove the sorry in MathFin/…; use lean_goal"
```

On a pass, the scheduled workflow assembles the proof and — **only with the
`MAIN_PR_TOKEN` foundry secret set** — opens a ready-for-review PR on
`formal-mathfin` that closes the source issue (the first was
[#120](https://github.com/raphaelrrcoelho/formal-mathfin/pull/120), contango,
opened 2026-07-11). Without the token it stops at candidate-notify and opens no
PR. **An opened PR is a *proposal*, not a finished contribution** — it passes CI
but is unmerged, and R reviews it under the 8-lens bar and revises before merge.
A green or opened PR is not proof of quality; the merge is.

**Where the loop actually stands.** Four autoformalized proofs have been merged into
the corpus (closing #66, #85, #161, #162) — counted mechanically from the `provenance`
markers in the corpus by `formal-mathfin`'s `formalization.yaml` generator, never
hand-asserted. The review round that landed them also **closed four autoform duplicates**
and surfaced the failure mode worth knowing about: 4 of 4 drafts asserted a division
hypothesis their theorem did not need, and every automated gate passed them, because the
prompt forbade *dropping* a hypothesis and never forbade *adding* one. A guard the proof
genuinely consumes can still be unnecessary, and neither an unused-variable warning nor a
deletion probe finds it — only dropping it and re-proving does. That check now runs in
`strengthen.py`. Activation + the PAT scoping:
[`docs/PROVER_SETUP.md`](docs/PROVER_SETUP.md).
