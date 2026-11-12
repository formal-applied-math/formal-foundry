# Prover-agent setup

How the formalization (prover) agents are equipped to match a MathFin author's
standards: the context they see, the values their output is held to, the house
idioms they're told to use, the exact pins they target, and the loop/harness
around them. Implemented in `probe/house_context.py` + `probe/prove_session.py`, launched
by `scripts/claude-prove.sh`.

## What every agent gets (the system message)

`house_context.build_system_prompt(main_repo)` is the session's appended system prompt
(passed as a file: built from the v2 library it is larger than one command-line
argument may be). It is assembled live (never stale) from:

- **Context** — the project is a Mathlib + BrownianMotion quant-finance library;
  the agent replaces a single `sorry` and returns the complete file.
- **Values gate** — forbidden tactics (`sorry`, `admit`, `native_decide`,
  `polyrith`, `exact?`, `apply?`, `hint`), axioms restricted to the standard 3
  `[propext, Classical.choice, Quot.sound]`. Enforced downstream by
  `slop_report` + the `axiom_guard_block` subset check, so the doctrine and the
  gate agree.
- **Coherence-first / anti-wrapper** — consume Mathlib/Degenne lemmas rather than
  reprove them; never wrap a single library lemma in a finance-named restatement;
  `loogle`/`leansearch%` are available (LeanSearchClient is a pinned dep).
- **House patterns & idioms** — the **live `formal-mathfin/docs/patterns.md` is
  injected IN FULL** by `house_context.read_patterns`, a first-class requirement:
  always its *current* form (read fresh per prompt), never a snapshot, so the prover
  proves to the library's latest patterns. A quick summary of what it carries: the
  `grind → nlinarith [certificates] → positivity/gcongr/bound` tactic order;
  `field_simp` before `ring`, `push_cast` before `field_simp`; `abbrev` for
  decidable predicates; annotate ambiguous coerced lambdas; the
  `HasDerivAt.congr_of_eventuallyEq` derivative-identification idiom;
  `convexOn_of_deriv2_nonneg'` on open sets; canonical `Real.exp (-(r * τ))`.
  Plus the Mathlib house-style golf a maintainer holds proofs to (PR #484 review):
  bare proof term over `by exact_mod_cast` when defeq, let Lean insert coercions,
  bind ∀-vars in the `have` signature, `simpa … using`, no gratuitous `classical`,
  `set` without `with`, the minimal typeclass (`SigmaFiniteFiltration` not
  `IsFiniteMeasure` when it suffices), and — the headline — lift the bespoke core
  into a general reusable lemma instead of tailoring it to one call site.
- **Structural strategy** — "this IS already that under renaming" (reduce a new
  closed form to an instance of an existing one), the pointwise-certificate for
  variational minima, one-period-inequality + induction.
- **Pins** — the exact target API surface: Lean toolchain, Mathlib rev,
  BrownianMotion rev, read from the main repo's `lean-toolchain` +
  `lake-manifest.json` so a pin bump propagates automatically.

## Per-target context pack (consume-don't-reprove)

`house_context.extract_signatures(main_repo, modules)` builds a block listing the
declaration names in the MathFin modules a target builds on — taken from the
**Pointers** section of the target's GitHub issue. Feeding the agent the existing
`vasicekBondPrice_affine`, `bsV`, `itoIntegralCLM_T`, … names is what turns
"reprove from scratch" into "apply the existing result", which is both the house
value and the higher-yield path.

## The loop

The prover is one headless agentic session per target (`claude -p`, model
`[prover].claude_model`), with the lean-lsp MCP server as its only Lean access. The
session reads the goal at the `sorry`, tries tactics against the live state
(`lean_goal`, `lean_multi_attempt`, `lean_diagnostic_messages`), searches
(`lean_loogle`, `lean_leansearch`), and edits the stub in place until the file checks or
`max_turns` ends. It never sees the issue: its task is the stub, the context pack and,
on a retry, the notebook of what earlier attempts tried.

`prove_session.py` then takes the Lean slot back for the daemon and gates what the
session left: no errors, no `sorry`, the axiom guard, the slop screen, and the
statement-integrity pin (the proved file must still assert the drafted statement). The
session's transcript is parsed for its token use and for whether the Lean tools were
actually reached; a session that never started is a red tick, never a failed proof.

- **Depth, not breadth** — `max_turns` (60) is the lever. More turns is more repair
  depth in one session, under the per-issue token cap.
- **The canary** — before a paid session, a trivial theorem goes through the same
  launcher (`[prover].canary`); if it does not come back proved the tick stops.
- **The completion-style loop** (`probe.run_target`: prompt → reply → daemon check →
  feed the errors back) remains for the refill's two optional kernel probes only.

## Targets come from issues, not a parallel list

The foundry's tackable queue is the `status:ready` issues in `formal-mathfin`
(the roadmap lives there — no shadow list). Each issue's **Pointers** section
drives the context pack; its **Task** is the statement to formalize.

## The `lean-lsp-mcp` harness (built + verified, Docker-plugged)

**`lean-lsp-mcp`** is the MCP server exposing the Lean language server's live goal
states, type info, hovers, search and `run` as tools. A completion loop pastes compiler
*error strings*; this gives the agent the *goal state* directly. It is stood up and
verified against this box's constraints:

- **`docker/docker-compose.lean-lsp.yml`** (main repo) adds a `lean-lsp` service:
  the pinned Lean image + the `lake_build_cache` olean volume (reuses the built
  oleans — no host build, no root-owned host `.lake`), `mem_limit 6g`, `cpuset`.
  It `pip install`s `lean-lsp-mcp` at start and idles; `docker exec` spawns the
  stdio MCP per session.
- **Memory doctrine** — the Lean LSP loads only during a session, so it is *the*
  one Lean process: the lean-repl daemon must be DOWN. `scripts/claude-prove.sh`
  enforces it (validates its arguments first, then takes the daemon down and brings
  `lean-lsp` up).
- **The MCP wiring** is a config the launcher writes per session and passes with
  `--strict-mcp-config`: `docker exec -i <lean-lsp container> lean-lsp-mcp
  --lean-project-path /app`, with `--allowedTools` limited to the Lean tools and file
  edits. The agentic drafter uses the same server (`autoformalize._lean_lsp_mcp_config`).

Runbook:

```bash
# Claude auth: CLAUDE_CODE_OAUTH_TOKEN (or ANTHROPIC_API_KEY) in the env, or a local login.

# per session (the launcher takes the daemon down + brings lean-lsp up):
cd ~/code/formal-foundry
scripts/claude-prove.sh --agent lean --max-turns 60 \
  -p "prove the sorry in <MathFin/…>; use lean_goal to read the state"

# tear the LSP down when done (frees the Lean slot for the daemon):
docker compose -f "$MAIN_REPO/docker/docker-compose.yml" \
  -f "$MAIN_REPO/docker/docker-compose.lean-lsp.yml" down lean-lsp
```

The house doctrine + values gate + issue-driven context pack (above) go in front
of whichever session produces the candidate; the slop screen and axiom guard do not
depend on it.

Note: the first `up` pip-installs `lean-lsp-mcp` in the container (~30 s); to skip
that, bake it into the image via CI (`publish-image.yml`), never a local
`docker compose build` (memory doctrine).

## Harness tuning — no new model (see `docs/upgrade-backlog.md` §A/§B)

Two lean-lsp-mcp levers speed the prove session with zero new dependencies:

- **`lean_multi_attempt`** — lean-lsp-mcp exposes a REPL-backed multi-candidate
  tool (~5× faster than paste-and-recheck, maintainer estimate). Tell the
  prover to use it for cheap candidate fan-out on hard targets (add it to the task
  prompt or the agent's standing instructions).
- **Self-hosted search endpoints** — the bundled LeanSearch/Loogle/State-Search
  hit hosted services rate-limited to ~3 req/30s and send queries off-box. Point
  `LOOGLE_URL`, `LEAN_STATE_SEARCH_URL`, `LEAN_HAMMER_URL` at local instances (in
  the `lean-lsp` service env) to remove the limit and keep queries private. A local
  Loogle build is ~2 GB; it is host-side (not a Lean env), but verify the footprint
  against the one-Lean-process memory doctrine before standing it up.

The other no-model lever is the **budget shape**: `max_turns` under
`tokens_per_issue_cap` in `pipeline.toml`. The reasoning is in
`docs/upgrade-backlog.md` §B.

## References

- [oOo0oOo/lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp)
- house idioms: `formal-mathfin/docs/patterns.md`; values: `docs/values-review.md`.

## Hands-off PR activation (the one credential)

The scheduled pipeline (`.github/workflows/pipeline.yml`) proves a queued issue
and, on a pass, opens a ready-for-review PR on `formal-mathfin` that closes the
source issue. That step needs one credential — the only thing granting the
foundry write access to main:

1. On `formal-applied-math/formal-mathfin`, create a **fine-grained PAT** scoped to
   that repo with **Contents**, **Pull requests** and **Issues**, each *Read and
   write*. All three are load-bearing: Contents pushes the branch, Pull requests opens
   the PR, Issues writes the `status:` labels and the PR's `autoform` label. A token
   with Contents alone fails in the worst way — the branch is pushed and the PR is
   refused (`Resource not accessible by personal access token (createPullRequest)`).
   The token set on 2026-09-28 was that token: thirteen ticks proved or re-gated a
   target, assembled it green and died at the last command.
2. Store it as the foundry secret (never in argv/logs):

   ```bash
   printf '%s' "<PAT>" | gh secret set MAIN_PR_TOKEN --repo formal-applied-math/formal-foundry
   ```

Without `MAIN_PR_TOKEN` the tick falls back to candidate-notify and opens no PR,
so the pipeline is safe before the grant. Revoking the PAT fully disables auto-PR.

### The PAT's repository list, once there is a second library

**A PAT scoped to the flagship cannot see a private sibling, and GitHub says so with
a 404, not a 403.** `formal-econometrics` and `formal-macroeconomics` are private;
`actions/checkout` against either with a flagship-only PAT fails
`Not Found — /rest/repos/repos#get-a-repository`, which reads like the repository has
been deleted rather than like a token is missing a grant. Measured, not guessed: run
[31980239248](https://github.com/formal-applied-math/formal-foundry/actions/runs/31980239248).

So any workflow targeting a second domain needs that repository **added to the PAT's
repository access** (Settings → Developer settings → Fine-grained tokens → the token →
Repository access). Note the consequence before doing it: a fine-grained PAT applies
ONE permission set across every repo it selects, so adding a library for read also
grants it Contents: write and Pull requests: write. For this program that is the
direction of travel — the pipeline's job on those repos is to open PRs — but it is a
real widening and worth doing deliberately.

`publish-verify-image.yml` preflights this with a plain API call before the build, so
the failure costs ~30 seconds instead of dying after a Mathlib prebuild. Any new
workflow that checks out a sibling should do the same.

Flow: `pipeline-tick.sh` (prove) → on pass `open-pr.sh` (assemble the proof into
its `MathFin/` module + a re-export benchmark entry + umbrella import; regen
`formalization.yaml` and, in the verify image, `lake build` + `AxiomAuditGen`;
green-or-abort) → PR labeled `autoform`. A candidate that will not assemble green
files an `autoform-blocked` issue on the foundry instead of opening a red PR.
R reviews (8-lens) and merges — scout authors the draft, human authors the merge.

FIRST-RUN NOTE: the in-image build/regen path in `open-pr.sh` (docker run against
a mounted main checkout) is validated on the first live PR; until then, expect to
finalize the AxiomAuditGen/ledger regen environment.
