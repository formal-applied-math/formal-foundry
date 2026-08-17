# MathCode harvest — the persistent process, the theorem bank, and where our bleeding actually is (2026-08-17)

Prompted by [math-ai-org.github.io/mathcode](https://math-ai-org.github.io/mathcode/).
The site itself was **unreachable from this session** (egress policy blocked
`math-ai-org.github.io`), so the primary source here is the project's published
README on GitHub — [`math-ai-org/mathcode`](https://github.com/math-ai-org/mathcode) —
which the landing page summarises. Claims marked **[vendor]** are their own README
numbers: self-reported, no third-party replication, and — the part that matters
most for us — **no benchmark table at all**. There is nothing here to compare
against a pass rate. Read it as an *engineering* signal, not a results signal.

MathCode is a terminal coding agent (Codex CLI by default, Anthropic-compatible
routes optional) with a Lean 4 formalization engine bolted in: plain-language
problem → Lean statement → agentic proof attempt. Its formalization/proving
pipeline is descended from [AUTOLEAN](https://github.com/T3S1AMAX/autolean.git).
It is a *personal-workstation* product; we are an unattended CI foundry that
opens PRs a human merges. That difference explains most of what we can and
cannot take.

## The architecture, mapped to ours

| Their piece | Ours |
|---|---|
| formalization engine (NL → Lean 4) | the refill phase: Claude specifies intent, then formalizes agentically (`probe/autoformalize.py`) |
| agent-mode proving (search Mathlib, write, compile, repair; ≤10 iterations) | the vibe ⇄ lean-lsp session, `max_turns = 60` (`probe/vibe_prove.py`) |
| Lean LSP integration (`leansearch`, Loogle, line/col diagnostics) | **the same lean-lsp-mcp tool surface** — as with Axiomatic, everyone converges here |
| tree-of-subgoals (`have … := by sorry` skeleton, subgoals proved independently) | the lemma-DAG path + skeleton gate (`probe/decompose.py`, `[decompose]`) |
| **persistent Lean REPL** (~30s → ~0.4s per check) **[vendor]** | the lean-repl daemon — but see the flip cost below |
| **TheoremLib/Stored.lean** — every proved theorem named, appended, re-injected | **nothing between kernel-green and human-merged** |
| multi-planner (`MATHCODE_NUM_PLANNERS=3`), all discovered lemmas kept | deliberately fanout=1 (item B′, depth over breadth) |
| axiom library (`/axiomatize`, `check` for consistency) | the opposite by doctrine: axioms ⊆ `{propext, Classical.choice, Quot.sound}` |
| Obsidian theorem graph | `scout_index.dependency_closure` + the embedding corpus (item W) |
| skills / tools / plugins | domain packs (`domains/<name>/`) |

Nothing in the list is a shape we lack. Two rows are gaps: the theorem bank, and
what "persistent process" actually costs us.

## The reframe: their surface is aimed where we do not bleed

Almost everything MathCode ships is *proving*-side. Our live obstruction report
(`runs/obstructions-report.md`, 22 obstructions) says:

| family | count | side |
|---|---|---|
| depth-gate | 9 | drafting |
| no-elaborating-draft | 7 | drafting |
| unknown-id-despite-retrieval | 3 | drafting |
| prover-max-rounds | 3 | proving (all `cal-bk-144`) |
| gate-fail / infra-indeterminate | 0 | — |

**19 of 22 obstructions are on the drafting half.** A tree-of-subgoals prover, a
third planner, and a lemma vault all make a prover stronger; our prover is not
the thing failing. Any item below that only helps proving is, on this queue,
optimising the 14% — which is the same discipline that withdrew the frontier
refinement (commit 296db40) and the item-W over-breadth complaint.

The one MathCode idea that reaches the drafting half is the persistent process,
and only via wall-clock — which happens to be our binding constraint.

## Take 1 — the flip cost is the real content of "persistent REPL" (item X)

Their headline engineering number is **~30s → ~0.4s per compile check [vendor]**,
achieved by keeping *one* stateful Lean process warm for the whole session and
serving every role off it — formalization checks, subgoal proving, verification.

We already beat the naive baseline *inside* a phase: the daemon answers on 7878,
and the agentic drafter's `lean_diagnostics` runs against a live LSP. What we do
not have is one process serving *both* roles, and the seam is expensive:

- **One Lean slot, two containers.** `scripts/slot-switch.sh` stops one and
  starts the other. The daemon side restarts and cold-loads Mathlib behind
  `wait_daemon.py` (a 5-minute readiness budget); the lean-lsp side is
  `--force-recreate`d, and each `docker exec`'d MCP session then pays its own
  Mathlib import (their README puts the same first-LSP-operation cost at ~60s).
- **The flip is inside the attempt loop, not around it.** `probe/autoformalize.py`
  flips to `lean-lsp` for the agentic formalize and back to `daemon` for the gate
  battery **per attempt** — so `semantic_rounds = 2` × `max_attempt_issues = 3`
  is up to six flip pairs in one refill phase, plus the prove phase's pair.
- **Wall-clock is the binding constraint**, not tokens: 2000 Actions-min/month, a
  tick costing 46–85 min (README, `pipeline.toml`).

The obvious fix — run both slots at once — is **refuted by our own infra**: the
memory doctrine forbids two Mathlib processes on the 10 GB dev box, and CI is
worse, not better (`.github/workflows/batch-verify.yml` documents ubuntu-latest
as a 2-core/7 GB runner; `verify_pool` needs ≥16 GB for `workers > 1`). So
co-residency is not the lever. The lever is **fewer transitions**: either
phase-major ordering (draft-side work under one lean-lsp uptime, kernel-grade
gates under one daemon uptime), or giving the lean-lsp container a programmatic
check path so the elaboration-level gates (depth, triviality, elaboration) never
need the daemon at all — the two containers run the *same* image; the daemon
exists because it offers a clean socket API, not because it can do something
lean-lsp cannot.

**Measure before building either.** Nothing in the run record times a phase; the
first commit here is a stopwatch on `slot-switch.sh`, not a refactor. If flips
turn out to be 3 min of an 85 min tick, this item dies and that is a good outcome.

## Take 2 — a bank for kernel-green, not-yet-merged lemmas (item Y)

"Every successfully proved theorem is automatically named, appended to
`TheoremLib/Stored.lean`, and made importable for future proofs." Our equivalent
horizon is the human merge: `house_context` reads the **live** target repo, so a
lemma becomes reusable only once R merges the PR. Everything proved in between is
discarded — including the explicit case where we already *compute* the list:
`decompose.recompose`'s partial path returns `banked` leaves (kernel-green,
gate-passed), `decompose_tick.do_recompose` writes them into the summary row, and
**no code ever reads them back**.

The scout-not-author line still binds: an unmerged lemma is not house-reviewed,
so it must never be *imported*. It can be *offered* — a "this statement has been
proved kernel-green but is not merged; prove it yourself or restate it" hint in
the context pack, exactly the shape the state cache already uses.

Honest caveat, and the reason this is measurement-first rather than a plan: on
today's telemetry the bank would be nearly empty. The A/B rows for `cal-bk-144`
show `leaves_total: 0, leaves_closed: 0` — we have not yet produced the partial
decompositions this feeds on. Build the store only after the decompose path
actually banks something.

## Take 3 — a failed attempt should leave Lean behind, not only prose (item Z)

The genuinely interesting half of multi-planner is not the parallelism (which our
item-B′ depth-over-breadth finding and the single Lean slot both rule out) — it is
that **"all discovered lemmas are saved to the vault" regardless of which planner
won**. A losing attempt still deposits verified artifacts.

Ours deposits a *prose* summary: `probe/experience.py` folds a failed attempt into
a rolling notebook. That is the right memory shape (it is what we took from the
Axiomatic harvest) but the wrong medium — the next attempt is told "`ring_nf`
did not close it", not handed the two `have` blocks that *did* elaborate. And we
already own the machinery to extract them: `state_cache` replays an accepted
proof prefix-by-prefix on the daemon and reads the goal at a spliced `sorry`.
Pointing that same replay at the *failed* candidate yields the longest
elaborating prefix, which is Lean text, not a paraphrase.

**Where it would pay:** the `prover-max-rounds` family is `cal-bk-144` ×3 — one
target retried across three ticks, re-walking the same ground each time. That is
precisely the case the notebook exists for, and precisely where prose is thinnest.

## What we do NOT take

- **The axiom library.** `/axiomatize` persists conversational assumptions as
  compile-checked declarations injected into later proofs. For a workstation
  assistant that is a feature; for us it is the failure mode the whole gate
  battery exists to prevent — a stored assumption is an unproved hypothesis
  granted library status. Our legitimate sibling already exists and is honest
  about itself: the decompose path's *declared remainder* (`refs`, never
  `closes`).
- **Cooperative cancellation across subgoals.** Theirs kills sibling subgoals when
  one fails; ours banks the proved leaves and declares a remainder. Under a
  wall-clock-bound quota there is a case for doing *both* — stop spending on the
  remaining leaves once the DAG cannot close, but keep what is proved. Cancel-only
  would throw away the input to item Y.
- **Multi-planner breadth.** Refuted by our own B′ measurement and by the single
  Lean slot: parallel planners need parallel Lean, which neither the dev box nor
  ubuntu-latest has.
- **The Obsidian graph.** We have the dependency closure and a 68k-premise
  embedding corpus; a vault visualisation is a UI over data we already query.
- **Skills / tools / plugins.** Domain packs cover this, with a stronger property:
  `probe/test_no_domain_leakage.py` enforces that the engine stays domain-free.

## The meta-lesson, which is not in their README

Two independent harvests (Axiomatic 08-06, MathCode today) found the same
architecture: frontier model + lean-lsp-mcp tool surface + compile-repair loop +
some memory across attempts. The shape is now commodity. What is not commodity is
what we published this repo to defend — an adversarial *statement*-side gate
battery (vacuity, disproof, depth, triviality) before any proving budget is spent.
MathCode formalizes a problem and proves it; nothing in its README asks whether
the formalization means what the problem said. That gap is our thesis, and it is
the reason their proving-side polish is not where our next commit goes.

## Sources

- [`math-ai-org/mathcode`](https://github.com/math-ai-org/mathcode) — README (primary; the landing page was egress-blocked)
- [AUTOLEAN](https://github.com/T3S1AMAX/autolean.git) — the pipeline MathCode builds on
- our own: `runs/obstructions-report.md`, `runs/ab-decomposer.jsonl`, `scripts/slot-switch.sh`, `probe/autoformalize.py`, `probe/decompose.py`, `probe/experience.py`, `.github/workflows/batch-verify.yml`
