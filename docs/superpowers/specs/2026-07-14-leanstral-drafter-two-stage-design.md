# Two-stage drafting: Leanstral formalizes, Magistral specifies

Status: IMPLEMENTED (2026-07-14). Supersedes the single-stage magistral drafter in
`2026-07-12-issue-to-stub-autoformalizer-design.md` (§ draft_with_repair) — which is kept
only as the eval baseline. Landed: `draft_intent` + `formalize_with_repair` (leanstral,
loogle-augmented repair) + `intent_fidelity_check` (folded roundtrip) in
`probe/autoformalize.py`; two-stage rewire of `refill`; config (`intent_model`,
`formalize_model`, `formalize_rounds`, `retrieval`); `probe/eval_draft.py`. 165 tests green.
Next: run `eval_draft.py` for the baseline vs two-stage numbers.

## Problem

The Jul-14 forced tick (`#29347394393`) drafted the next three ready issues and
**all three failed at the first gate**:

```
[refill] #53: no elaborating draft after 3 rounds
[refill] #67: no elaborating draft after 3 rounds
[refill] #61: no elaborating draft after 3 rounds
[tick] refill seeded=0
```

`#53` exhausted its rounds with no API timeout, so this is a genuine
draft-ELABORATION failure, not infra. The failure modes are all *Lean* — hallucinated
identifiers, `let`-scoping (`Unknown identifier V/F/δ` on `#67`), coercions — i.e. a
knowing-the-Lean/Mathlib-surface problem, which is Leanstral's home turf and magistral's
weak spot. The draft-elaboration rate is the current binding constraint on yield (it sits
*upstream* of the depth gate we just shipped, so nothing even reaches the gate).

## Decision

Give each model its strength. Split the draft into **intent** (reasoning, magistral) and
**formalization** (Lean, Leanstral). Land three composing levers on top: grounding /
retrieval, Leanstral-targeted repair, and reliability.

- **Two-stage draft** (chosen over Leanstral-only): keep magistral on math intent — the
  reason the split ever existed — and move only the *Lean* writing to Leanstral. Rejected
  Leanstral-drafting-straight-from-the-issue: it asks Leanstral to also parse finance prose
  (unmeasured risk) and buys little over a cheap magistral intent step.
- **Roundtrip folds into an intent-fidelity check (option b)**: since magistral now emits an
  explicit intent, check *"does Leanstral's Lean faithfully render magistral's intent?"*
  instead of a re-formalize round-trip. Cheaper (one fewer model call) and more apt.
- **Retrieval is local** (fuller `extract_signatures` + `scripts/loogle.sh`), never
  leansearch/cloud (privacy; the public index tracks a newer Mathlib than our pin → false
  hits).

## New cascade

| # | step | model | notes |
|---|------|-------|-------|
| 1 | **intent** | magistral | issue → precise prose statement + objects-to-use + naming meta. No Lean. |
| 2 | **formalize + repair** | **leanstral** | intent + grounding → `theorem sig := by sorry`; repair Lean errors (loogle-augmented) with Leanstral. |
| 3 | depth gate | (elaborator) | unchanged; structural, pointers-scoped. |
| 4 | hypothesis-rejection + disproof | leanstral | unchanged. |
| 5 | judge | magistral | statement vs issue; unchanged. |
| 6 | **intent-fidelity** (was roundtrip) | magistral | informalize leanstral's Lean → compare to the step-1 intent. |
| 7 | prove | leanstral | unchanged. |

## How the three levers compose (the point)

They are not independent knobs under this architecture; they share machinery:

1. **Retrieval is ONE shared service, called twice.** A single
   `retrieve_signatures(concepts | idents) -> str` helper (local: fuller `extract_signatures`
   of pointer modules + `scripts/loogle.sh`). It grounds the **formalize** prompt up front
   (lever 1) *and* answers `unknown identifier X` in the **repair** prompt reactively
   (lever 3). Build once, wire two call sites.
2. **Repair targets the Lean model** (lever 3 × the leanstral swap). Compiler errors are Lean
   artifacts; Leanstral consumes them natively, so lever 3 is strictly more effective now
   than when feedback went to a general reasoner. The draft-repair loop's `chat_fn` moves
   from `reason_fn` (magistral) to `prove_fn` (leanstral).
3. **Grounding and the depth gate reinforce** (lever 1 × the shipped gate). Magistral's intent
   names the pointer-module defs; grounding makes Leanstral consume them; the depth gate
   enforces it. Better grounding raises BOTH the elaboration rate and the depth-gate pass
   rate — same direction.
4. **Reliability matters MORE** (lever 4). Two-stage = more calls/issue (intent + formalize +
   N repairs), so timeout exposure grows. Wrap every call (retry/backoff already in
   `mistral_chat`) + per-stage budgets, so one stage's timeout degrades that stage, not the
   issue. Leanstral may be on a non-free tier — its reliability is a separate knob from
   magistral's free-tier flakiness.
5. **Roundtrip independence is traded for intent-fidelity** (the knock-on). A Leanstral
   re-formalize would now be Leanstral-grading-Leanstral. Fold (b) changes the check's meaning
   from "two models independently agree on the statement" to "the formalization is faithful to
   the intent it was built from" — which is the more relevant risk once Leanstral is the
   formalizer (it catches Leanstral dropping a hypothesis / flipping a direction). The
   issue-level faithfulness check remains magistral's job at step 5 (judge).

## Component design

- **`draft_intent(issue, context) -> intent`** (magistral). Output JSON:
  `{statement, objects: ["MathFin.zcb", ...], module_name, benchmark_id, docstring,
  deferred: [...]}` where `statement` is precise math prose. `deferred` keeps the honest-subset
  contract. Naming meta stays here (it is classification, not Lean).
- **`formalize_with_repair(intent, grounding, *, chat_fn=leanstral, check_fn, emit_fn,
  retrieve_fn, rounds) -> {ok, stub, lean_text, entry, tokens}`**. Prompt: intent + grounded
  signatures → one `theorem NAME binders : concl := by sorry`. Repair loop: on `errors`,
  feed the compiler message + (for `unknown identifier X`) `retrieve_fn(X)` candidates back to
  **Leanstral**. Reuses `emit_target_files` + the elaboration gate (`errors==[] and
  sorry_count==1`).
- **`retrieve_signatures(...)`** — local only. Two tiers by trust: (a) **pin-exact**
  pointer-module signatures via a fuller `extract_signatures` — trustworthy grounding, used up
  front; (b) `scripts/loogle.sh` hits — the public index tracks a NEWER Mathlib than our pin,
  so its results are **unverified candidates** (may name lemmas absent in-pin). Use loogle only
  as *reactive repair* candidates, where the elaborator gates a bad suggestion for free; never
  as up-front ground truth. No cloud.
- **`intent_fidelity_check(intent, stub, *, reason_fn) -> {faithful, verdict, tokens}`** —
  magistral informalizes the stub and compares to `intent.statement`. Replaces
  `roundtrip_check`. Soft + lenient (reject only on explicit divergence), fail-open when
  inconclusive.
- **Reliability** — per-stage token budgets threaded through; `mistral_chat` retry/backoff
  retained; a stage exception degrades to a skip of that issue (existing refill try/except),
  never a tick crash.

## Eval harness (build FIRST — measure before optimizing)

`eval_draft.py`: draft-ONLY (no prove), against the warm local daemon, over a fixed set of
~10 ready issues. Modes to A/B: `baseline` (current magistral single-stage) ·
`leanstral-formalize` (two-stage) · `+grounding` · `+repair`. Per-issue metrics:
elaboration success (bool), depth-gate pass (bool), rounds used, tokens, wall_s. Output a
small table so each lever's delta is visible. This decides whether leanstral-formalize alone
suffices or grounding is doing the work — minutes, not 45-min CI rolls.

## Config

`[autoformalize]`: `intent_model = magistral-medium-latest`, `formalize_model =
labs-leanstral-1-5`, `retrieval = true`, `formalize_rounds` (was `draft_rounds`), per-stage
budgets. Back-compat: keep `draft_model`/`prover_model` as fallbacks.

## Testing (TDD, stdlib, injected chat_fn/check_fn)

New pure-logic tests: intent JSON parse; `formalize_with_repair` drives leanstral `chat_fn`
and repairs on an elaboration error; `retrieve_signatures` shape; `intent_fidelity_check`
verdict parsing + fail-open; eval-harness accounting. Full suite stays green.

## Phasing

1. Eval harness + **baseline number**.
2. Two-stage draft (`draft_intent` + `formalize_with_repair`, repair→leanstral); A/B vs baseline.
3. Retrieval (grounding + reactive repair); A/B delta.
4. Roundtrip → intent-fidelity fold.
5. Reliability polish (per-stage budgets); config + docs; enable.

Each phase A/B'd against the eval; ship only what moves the number.

## Risks / open

- Leanstral tier/cost + rate limits (more calls); measure token/wall in the eval.
- Magistral intent quality is now load-bearing for correctness (leanstral formalizes what it
  is told); the judge + intent-fidelity check are the guards.
- If the eval shows leanstral-formalize alone closes the gap, defer retrieval to keep it simple.
