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

## The canonical production harness (P1): `lean-lsp-mcp`

Leanstral was specifically trained for maximal performance with **`lean-lsp-mcp`**
— an MCP server exposing the Lean language server (live goal states, type info,
hovers, search, run) as tools, which is what enables its long-horizon (millions
of tokens, multi-compaction) proof sessions. The text-loop here pastes compiler
*error strings*; the MCP harness gives the agent the *goal state* directly. For
P1, stand this up rather than extend the text-loop:

```toml
# ~/.vibe/config.toml
[[mcp_servers]]
name = "lean-lsp"
transport = "stdio"
command = "uvx"
args = ["lean-lsp-mcp"]
tool_timeout_sec = 600
```

Run with `vibe --agent lean` (Leanstral) pointed at a clone pinned to the
toolchain/Mathlib/BM revs above; keep the same system doctrine + values gate +
issue-driven context pack in front of it. The house doctrine, slop screen, and
axiom guard are harness-independent — they wrap whichever loop produces the
candidate.

## References

- [Leanstral 1.5 — Mistral AI](https://mistral.ai/news/leanstral/)
- [mistralai/Leanstral-1.5-119B-A6B — Hugging Face](https://huggingface.co/mistralai/Leanstral-1.5-119B-A6B)
- [oOo0oOo/lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp)
- house idioms: `formal-mathfin/docs/patterns.md`; values: `docs/values-review.md`.
