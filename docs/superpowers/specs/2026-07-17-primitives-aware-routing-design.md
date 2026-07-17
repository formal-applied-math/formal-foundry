# Primitives-aware routing (F3+F2+F1 composed)

date: 2026-07-17
status: approved by R ("do F3+F2+F1")
extends: 2026-07-17-semantic-repair-cascade-design.md

## Problem (from the #53 live validation)

The cascade repaired mechanics but exposed the deeper wall: some issues cannot be
seeded by a theorem-only stub no matter how good the feedback, because the library
lacks the primitives their domain-shaped statement needs (#53: no barrier payoff
defs; drafted `MathFin.vanillaPayoff` — a guess at a def that SHOULD exist). Also:
R's issue pointers are often STYLE references, while the depth gate assumes
consume-semantics. Three forks were proposed; R chose all three, composed.

## Design: one measured property routes every issue

**"Does the library already have the primitives?"** is measured, not assumed:

1. **Measure (F3)** — `count_pointer_defs(main_repo, pointers)`: consumable
   exports (`def`/`abbrev`/`structure`) in the issue's pointer modules. No issue
   rewriting; style-pointers remain prover context.
2. **Classify (F2)** — `classify_refill(record)` maps every attempted-record to
   an obstruction family (`seeded`/`needs_primitives`/`defs_rejected`/
   `trivial_restatement`/`fidelity`/`undraftable`/`statement_wrong`/`budget`/
   `infra`); the family rides the record in `runs/refill-history.jsonl`.
   Depth-exhaustion ⇒ `needs_primitives` (the runtime evidence that measurement
   missed — #53 measures consumable via `chooserPrice` but its faithful statement
   can't use it). `formalize_with_repair` now also RETURNS the unknown
   `MathFin.*` identifiers the model guessed — the defs it thinks should exist.
3. **Route (F3+F2)** — `route_for`: `defs` when the latest family is
   `needs_primitives` OR def_count == 0; else `theorem`. Theorem-route issues
   order first (cheap wins). Re-drawing a routed issue costs a defs attempt, not
   the same 42k-token depth-exhaustion every tick.
4. **Definitions path (F1)** — for `defs`-routed issues the drafter emits a
   SMALL MODULE: 1–3 sorry-free `def`s + ONE theorem `:= by sorry` stated
   through them. Intent JSON gains `definitions: [{name, signature, meaning,
   built_from}]`; prior guessed unknowns are fed into the intent as hints.
   Gate changes on this route ONLY:
   - pointer-scoped depth gate is REPLACED by `defs_rejection` (one daemon
     probe, two verdicts):
     * `newdef_depth` — the theorem's TYPE must use ≥1 drafted def
       (`getUsedConstants`, same machinery as the depth gate);
     * `ungrounded` — every drafted def's VALUE must use ≥1 IMPORTED constant
       (`getModuleIdxFor?.isSome`) — an identity/free-floating wrapper fails.
   - triviality/vacuity/disproof/judge/intent-fidelity run unchanged; both new
     verdicts are repairable via the cascade with their own instructions.
   Anti-Goodhart: wrapping real content in honest defs (e.g. `knockInPayoff`
   over indicator integrals) is exactly what we WANT — the gates only kill
   self-referential scaffolding; design quality stays with R's merge review.
   The stub module carries `-- new-defs: a, b`; open-pr labels such PRs
   `new-defs` (the architecture-heavy review class).

Self-healing: a merged new-defs PR makes blocked issues measure consumable at
the next tick — no tracking.

## Also fixed here

`_UNKNOWN_RE` never matched the live elaborator format (``Unknown identifier
`X` `` — capital U, backticks vs the expected lowercase + straight quotes), so
the retrieval hook silently never fired in production. Widened + pinned by a
test using the verbatim live error line.

## Out of scope

Prover ceiling (hard backlog stays out of cron reach); magistral judge quality;
the no-resets policy on green PRs (R's call).

## Validation

Full suite; live `refill --only 53` should now route `defs` (history says
needs_primitives) and either seed a defs+parity module into the scratch queue or
leave `defs_rejected` telemetry with per-round verdicts.
