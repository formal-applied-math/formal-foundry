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

## Addendum 2 (2026-07-17, post PR #124): gate-time strengthen — drop unused hypotheses

2/2 production PRs shipped a hypothesis the finished proof never used (#123
`hTn`, #124 `hσ_eq`). Unused-ness is a property of the PROOF (Lean suppresses
the `unusedVariables` linter under `sorry`), so no draft-time gate can catch
it; instead the vibe gate now runs a strengthen pass on a kernel-passing
candidate (R chose auto-strip over gate-fail and human-review):

- `gate.gate()` surfaces the candidate check's elaborator `warnings`.
- `autoformalize.strengthen_candidate` reads `unused variable` warnings,
  intersects with the theorem's EXPLICIT binders (`_`-prefixed and
  implicit/instance binders exempt), drops them from the signature, and
  re-runs the FULL gate on the stripped statement; warnings from the re-gate
  drive a bounded cascade (a drop can orphan another binder). Dropping an
  unused hypothesis can only strengthen the theorem — the fidelity direction
  is safe by construction.
- Fail-open everywhere: re-gate red, unlocatable decl, or unrebuildable
  snippet reverts to the proved original. Module and re-export snippet move
  together or not at all (a signature mismatch would block open-pr regen).
- The stripped re-export entry is written as a RUN artifact
  (`runs/$TAG-$ID.entry.json`, provenance notes `stripped_hypotheses`);
  `open-pr.sh` prefers it over the seed-manifest entry. The queue stays
  immutable (zombie doctrine).
- Telemetry: the run summary row carries `stripped_hypotheses` when the pass
  fired.

## Addendum 3 (2026-07-18): full CI parity + failure classes + import trim

R's directive: the priority is a pipeline ROBUST to the review-found classes,
not hand-shepherding PRs. Landed:

- **Full main-CI parity pre-PR.** open-pr's in-image block now runs `lake lint`
  after `lake build` (all 16 linters — the textual gate covers only the 2
  observed classes), and after placement it runs the main repo's python gates
  (`pytest tests/`) exactly as build.yml does. A PR can no longer open red on
  anything main CI checks.
- **Repo-gates preflight.** The tick stands down (skip, loud reason) when the
  MAIN repo's python gates are already red BEFORE spending a prove — a tripped
  values-review cadence or stale ledger is a human's red light on the repo, not
  a per-target failure.
- **Failure classes.** open-pr exit 3 = content-deterministic block (lint/
  regen/python gates rejected the candidate) → the tick RECORDS
  `fail_assembly` and moves on (kills the #53 infinite-retry class); exit 4 =
  transient (gh/network) → target stays retryable. `gh pr create` failures are
  transient, `blocked()` is content.
- **Unused-import trim** (2/2 production PRs): `trim_unused_imports` drops
  `public import MathFin.X` lines the proved candidate elaborates without
  (subtractive, per-removal elab check, `Mathlib` never touched), then one full
  re-gate guards against instance-resolution drift — revert wholesale if it
  fails. Telemetry: `trimmed_imports` on the summary row.
- **Contract: natural generality.** The formalize contract now demands the
  natural level of generality (no hard-wired curve the claim does not need,
  `s.Nonempty` over member-witnesses, `A ≠ 0` over derivable positivity) and
  signature-bound definition arguments with `↦`.

Designed, deferred (next observed instance builds it): a draft-time
DERIVABLE-HYPOTHESIS probe — for each explicit hypothesis `(h : P)`, elaborate
`example <earlier binders> : P := by first | positivity | norm_num | simp`
in one daemon call (marker/line mapping as in `defs_probe`); closures mean the
hypothesis is provable and should be dropped from the statement (the #123 `hP`
class — strengthen cannot see it because the proof USES the hypothesis). The
contract + fidelity judge cover this class at instruction level today.

## Addendum 4 (2026-07-18): the four refinery-approved upgrades

R approved all four wiring candidates from the #123/#124 refinery. Landed:

- **Derivable-hypothesis probe** (draft-time, the #123 `hP` class): after a draft
  passes elab+lint, `derivable_hypotheses` builds ONE probe file — the stub's
  prefix (imports + drafted defs) plus one single-line `example` per single-name
  explicit binder, proving its type from the EARLIER binders only via
  `by intros; first | positivity | norm_num | simp | exact?` under a 50k
  heartbeat cap, `(… : Prop)`-ascribed so data binders are a type error, never a
  false hit. Error lines map to examples; error-free examples = derivable →
  fed back HARD (like lint), bounded by the same rounds. Fail-open on foreign/
  unlocatable errors. Wired via injectable `derivable_fn` (production `main()`
  passes the daemon-backed one; fakes in tests can't leak into the probe).
- **∧-bundle advisory** (draft-time, soft): a top-level `∧` conclusion triggers
  exactly ONE nudge round (core+corollary, or bundle-as-core with projection
  corollaries); whatever comes back is accepted — never a hard gate.
- **Core + corollary stub shape**: the formalize contract now allows extra
  SORRY-FREE theorems after the single-`sorry` core (issue-shaped instantiation
  or per-fact projections, proved by terms applying the core); the intent
  defs-addendum may specify `"corollary": {name, statement}`. `sorry_count == 1`
  and all first-decl-targeting gates hold unchanged; `_rebuild_snippet` now
  refuses a snippet that applies a different theorem than the stripped core
  (the corollary shape) rather than corrupting it.
- **Post-gate proof golf** (experiment, `GOLF=0` disables): after strengthen +
  trim, the prover golfs its own accepted proof to the house register
  (certificate over search, no dead `set … with`, `simpa`-folds). Accepted only
  if every decl signature is byte-equivalent (proof-only edits, no `sorry`) AND
  the full gate passes again; otherwise the proved original stands.
