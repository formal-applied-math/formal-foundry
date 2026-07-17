# Semantic repair cascade for the refill drafter

date: 2026-07-17
status: approved (direction ratified by R: "the whole point of the pipeline is to be
autonomous" — no human mid-pipeline step; all improvements must serve the autonomous
issue→PR flow)
supersedes-part-of: 2026-07-12-issue-to-stub-autoformalizer-design.md (the refill
gate cascade section; the two-stage draft + gates themselves are unchanged)

## Problem

The funnel is inverted at the drafter. Production evidence (CI runs 2–3, 2026-07-17):

- the vibe prover went 2/2 on its target; the gates + honesty guards work
  (rfl-trivial blocked at open-pr, shallow rejected at refill);
- the drafter went 0/4 faithful+substantive: #67 gate-passing but rfl-trivial,
  #53/#66 depth-rejected as shallow, #61 API timeout;
- 3 autoform PRs ever opened, 0 merged. 49 ready+proof issues wait.

Root cause (autoformalize.py `refill`): the ONLY repaired failure class is
compilation — `formalize_with_repair` loops on elaborator errors. Every SEMANTIC
gate is a terminal `continue`: depth (shallow), hypothesis-rejection (vacuous),
disproof (false), judge (unfaithful), intent-fidelity (drift). A statement can
elaborate cleanly yet be shallow or trivial, and nothing repairs that: the drafter
is never told what was wrong. This contradicts the ML4TP harvest headline
(sequential repair beats terminal filtering) on the exact stage where we need it
most. Separately, NO gate catches the #67 class at draft time (a statement whose
proof is `rfl`) — that guard lives only in open-pr, after all prove-compute is
spent.

## Design

Three additions, all autonomous (no human step):

### 1. Triviality gate (new, structural, zero tokens)

`triviality_goal(lean_text)`: splice the stub's single `sorry` proof into
`:= by first | rfl | simp`. `triviality_rejection(lean_text, *, check_fn)`:
elaborate via the daemon; **clean close ⇒ the statement is definitionally/
simp-trivial** — a definition unfolded into itself, no mathematical content
(the #67 class). Boundary is deliberate: bare `rfl` + goal-only `simp` (no
`simp_all`, no `grind`) so easy-but-real content is NOT over-filtered. Fail-open
like the depth gate: a daemon error is not a verdict; no recognizable `sorry`
to splice ⇒ skip. Runs right after the depth gate (both structural, both free),
before any prover-token gate.

### 2. Feedback re-draft loop (the repair cascade)

Wrap the per-issue draft+gate pipeline in a bounded loop (`semantic_rounds`,
default 2 total attempts). On any gate failure, build gate-specific feedback —
`render_gate_feedback(gate, detail, stub)`: the rejected stub + the gate verdict
+ a revision instruction — and re-enter the loop:

- feedback goes to **both stages**: `draft_intent(…, feedback=…)` (magistral may
  need to re-frame the statement) and `formalize_with_repair(…, revision_note=…)`
  (the observed #67 failure was leanstral inlining `let`s over raw reals even when
  the intent named `MathFin.zcb` — the formalizer must see the depth verdict too);
- per-gate instructions encode the repair direction:
  - `depth` → express the theorem's TYPE through the pointer-module declarations,
    fully applied; never inline their formulas;
  - `trivial` → state the substantive fact (identity/inequality between
    independently defined quantities), not a definition restated;
  - `vacuous` → the hypothesis set is contradictory; check inequality directions
    and degenerate parameter values;
  - `false` → the negation was PROVED: the issue's math is presumed right, the
    RENDERING flipped a sign/inequality or dropped a hypothesis. Do NOT weaken
    the conclusion (the judge + fidelity gates re-run on every repaired draft,
    so a weakened restatement is still caught);
  - `unfaithful` / `drift` → the judge's issues list rides along verbatim.
- every repaired draft re-runs the FULL battery (cheapest-first:
  depth → trivial → vacuous → false → judge → drift), extracted into
  `semantic_verdict(…)` so the loop body stays readable and the battery is
  unit-testable;
- the refill `budget` is checked per attempt, not only per issue, so the loop
  cannot overspend the tick.

Goodhart note: iterating against ONE syntactic gate would invite gate-satisfying
junk. The defense is battery DIVERSITY — two structural gates (depth, triviality),
two kernel gates (vacuity, disproof), two model judges (issue-faithfulness,
intent-fidelity) — plus the unchanged downstream honesty stack (open-pr rfl
guard, axiom audit, human merge review at the very end, where it already lives).

### 3. Obstruction telemetry

Refill returns `attempted`: one record per issue —
`{issue, attempts, outcome: "seeded"|<last gate>|"error", history: [{attempt,
gate, detail}…], tokens}` — and the CLI appends each to
`runs/refill-history.jsonl`. A tick that seeds nothing now says exactly which
gate ate each issue and whether feedback moved the draft between rounds. This is
the drafter's analogue of `triage.py` for the prover.

## Config

`AutoformalizeConfig` (+ `pipeline.toml [autoformalize]`, + CLI flags):
- `semantic_rounds: int = 2` — total semantic attempts per issue (1 = the old
  single-shot behavior);
- `triviality_gate: bool = true`.

Cost envelope: worst case ~2× the current per-issue draw (one extra
intent+formalize+gates cycle), bounded by the existing `budget` per attempt.
Tokens are labs-tier; the binding constraint is CI wall-clock, which stays
bounded by `max_attempt_issues` × `semantic_rounds` × the existing per-stage
budgets.

## Out of scope (deliberate)

- No human statement-approval step (rejected: pipeline must be autonomous).
- No hand-seeded targets.
- No decomposition reasoner (harvest R3 — a separate, larger fork).
- open-pr/tick unchanged: refill's CLI contract (`{"seeded": […]}`) is additive.

## Validation

1. Unit: full foundry pytest (fake chat_fn/check_fn, per the existing pattern).
2. Live (fast local feedback cycle, R-endorsed): daemon up, then
   `autoformalize.py refill --only 53 --queue-dir <scratch>` against real
   Mistral — issue #53 is one of the two observed depth-rejections. Success =
   a deep, faithful stub seeded in the scratch queue; acceptable = bounded
   rounds exhausted with per-round telemetry showing the feedback was applied.

## Addendum (2026-07-17, post PR #123): draft-time lint gate

The first production PR (formal-mathfin #123) opened red on the main repo's
`lake lint`: `defsWithUnderscore` on two snake_case def names + `docBlame` on
three docstring-less defs. Both classes are textual, so they are now caught
where repair is cheapest instead of at human review:

- `probe_lib.lint_violations(code)` — the textual mirror of the two linter
  classes (def/abbrev/structure names must be lowerCamelCase; each needs a
  `/-- … -/` immediately above; theorem names exempt; structure FIELDS not
  checked — main-repo CI remains the backstop there). `DEF_RE` moved to
  probe_lib as the shared def-parser (routing measurement + lint).
- `formalize_with_repair` treats an elaborating-but-lint-dirty round as a
  repair round: the violation list rides the same feedback channel as
  elaboration errors, bounded by the existing `rounds`/`token_budget`.
- `gate.gate()` adds a `lint:<list>` textual screen beside the slop screen —
  the backstop for statement text mutated after drafting (e.g. by the vibe
  prover). A lint-dirty candidate records as `fail_gate`, never opens a PR.
- The formalize contract states the bar up front (docstring + lowerCamelCase),
  so round 1 is usually already clean.
