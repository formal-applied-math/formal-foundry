# Prover-agent setup

How the formalization (prover) agents are equipped to match a MathFin author's
standards: the context they see, the values their output is held to, the house
idioms they're told to use, the exact pins they target, and the loop/harness
around them. Implemented in `probe/house_context.py` + `probe/probe.py`.

## What every agent gets (the system message)

`house_context.build_system_prompt(main_repo)` is injected as the `system`
message on every attempt. It is assembled live (never stale) from:

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
- **House idioms** (distilled from `formal-mathfin/docs/patterns.md`) — the
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

`run_target` runs the multiturn compiler-feedback loop Leanstral is trained for:
build prompt → chat → extract the ```lean block → slop-screen → daemon-check →
on failure, feed the compiler errors back and resend the complete file; on
success, verify axiom-cleanliness. Supporting pieces:

- **`reasoning_effort`** — `--reasoning-effort high` for hard targets, `none` for
  speed. In reasoning mode the API returns content as a list of blocks;
  `normalize_content` keeps the answer text and drops the internal thinking.
- **Test-time scaling** — the per-target token budget (`--budget`) is the
  first-class knob; the calibration sweeps it 50k → 500k → 2M.
- **Windowing** — `window_messages` preserves the leading system message + the
  first user turn (the file) across long repair loops, so the doctrine and the
  statement are never dropped.

## Targets come from issues, not a parallel list

The foundry's tackable queue is the `status:ready` issues in `formal-mathfin`
(the roadmap lives there — no shadow list). Each issue's **Pointers** section
drives the context pack; its **Task** is the statement to formalize.

## The `lean-lsp-mcp` harness (built + verified, Docker-plugged)

Leanstral was specifically trained for **`lean-lsp-mcp`** — the MCP server
exposing the Lean language server's live goal states, type info, hovers, search,
and `run` as tools, which is what enables its long-horizon proof sessions. The
text-loop pastes compiler *error strings*; this gives the agent the *goal state*
directly. It is stood up and verified against this box's constraints:

- **`docker/docker-compose.lean-lsp.yml`** (main repo) adds a `lean-lsp` service:
  the pinned Lean image + the `lake_build_cache` olean volume (reuses the built
  oleans — no host build, no root-owned host `.lake`), `mem_limit 6g`, `cpuset`.
  It `pip install`s `lean-lsp-mcp` at start and idles; `docker exec` spawns the
  stdio MCP per session.
- **Memory doctrine** — the Lean LSP loads only during a session, so it is *the*
  one Lean process: the lean-repl daemon must be DOWN. `scripts/leanstral-vibe.sh`
  enforces it (takes the daemon down, brings `lean-lsp` up, sources the key).
- **`~/.vibe/config.toml`** wires the MCP as
  `docker exec -i mathfin-lean-lsp lean-lsp-mcp --lean-project-path /app`.
  Verified end-to-end: the handshake exposes 23 tools including `lean_goal`,
  `lean_term_goal`, `lean_hover_info`, `lean_diagnostic_messages`,
  `lean_completions`, and `lean_loogle`/`lean_leansearch`/`lean_state_search`.

Runbook:

```bash
# one-time: Leanstral API key + agent
vibe --setup                 # stores the Mistral API key
# then, inside vibe, run /leanstall to install the Leanstral agent + model (labs-leanstral-1-5)

# per session (the launcher takes the daemon down + brings lean-lsp up):
cd ~/code/mathfin-foundry
scripts/leanstral-vibe.sh --agent lean -p "prove the sorry in <MathFin/…>; use lean_goal to read the state"

# tear the LSP down when done (frees the Lean slot for the daemon):
docker compose -f ~/code/automated_proofs_quantfin/docker/docker-compose.yml \
  -f ~/code/automated_proofs_quantfin/docker/docker-compose.lean-lsp.yml down lean-lsp
```

The house doctrine + values gate + issue-driven context pack (above) go in front
of whichever loop produces the candidate — the text-loop *or* this MCP harness;
the slop screen and axiom guard are harness-independent.

Note: the first `up` pip-installs `lean-lsp-mcp` in the container (~30 s); to skip
that, bake it into the image via CI (`publish-image.yml`), never a local
`docker compose build` (memory doctrine).

## References

- [Leanstral 1.5 — Mistral AI](https://mistral.ai/news/leanstral/)
- [mistralai/Leanstral-1.5-119B-A6B — Hugging Face](https://huggingface.co/mistralai/Leanstral-1.5-119B-A6B)
- [oOo0oOo/lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp)
- house idioms: `formal-mathfin/docs/patterns.md`; values: `docs/values-review.md`.
