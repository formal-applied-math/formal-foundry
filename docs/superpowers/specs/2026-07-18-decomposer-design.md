# Magistral decomposition loop — mechanics design (Task 2.0)

> **Status: BUILT — tasks 2.1–2.8 landed on `phase2/decomposition` (2026-07-18), off by
> default (`[decompose] enabled=false`, tag-only).** R delegated the design decisions. This is a
> *mechanics* doc — the strategy is fixed by
> `docs/superpowers/plans/2026-07-18-autoformalization-improvements.md` (an autonomous
> Mistral pipeline on CI; general-reasoner role filled by Magistral; decision gate
> 2026-09-30). It specifies the schema, prompts, gates, budgets, recomposition,
> keep-and-revise, and A/B telemetry the Phase-2 tasks build. Phase 0 confirmed the
> bottleneck is the reasoning/drafting layer, so this loop is the structural fix.

## The shape

A hard target that the plain draft→prove path fails (or is tagged `decompose`) is
handed to a **general reasoner (Magistral) that splits it into a lemma-DAG**: named
leaf lemmas plus a main theorem proved by applying them. Every reasoner output
passes a **Lean-side gate before any prover budget is spent** — a mid-tier reasoner
inside a verified protocol ≈ a frontier one free-styling. Leaves are ordinary
single-`sorry` targets that reuse the existing prove+gate path; a recomposition
gate assembles the proved leaves into the final module.

```
target ──▶ draft_decomposition (Magistral, chat_fn)         [2.2]
             │  lemma-DAG JSON
             ▼
        parse_dag + topo_order  (schema validate, cycle/size) [2.1]
             │
             ▼
     assemble_skeleton ─▶ skeleton_gate  (elaborates? sorries==n_leaves?) [2.3]
             │  pass                     │ fail → one bounded re-decompose, else stop
             ▼
     leaf targets (parent + dag_order) ─▶ vibe_prove run/gate (each) [2.4]
             │  all pass                 │ partial
             ▼                           ▼
     recompose ─▶ gate.gate (full)   bank proved leaves + declared remainder [2.5]
             │
             ▼
        open-pr (DAG-shaped module; PR body lists the DAG)
```

Interface is **model-agnostic** (`chat_fn` injection), but the automated loop is
**Mistral-only**: Magistral is the production reasoner. The interface stays swappable
so a frontier eval at the 2026-09-30 gate is a config change, not a rewrite — it is
NOT a standing second arm.

## 2.1 — Lemma-DAG schema + validation (`probe/decompose.py`)

JSON contract (the decomposer's output):

```json
{
  "main": {"name": "<snake_case>", "statement": "<Lean conclusion + binders>",
           "proof_sketch": "<how the leaves combine — applied by name>"},
  "leaves": [
    {"name": "<snake_case>", "statement": "<Lean theorem sig, no proof>",
     "pointers": ["MathFin/…/X.lean"], "depends_on": ["<leaf name>", ...]}
  ]
}
```

- `parse_dag(json) -> Dag` — validate: unique names, `depends_on` references exist,
  no cycles, `len(leaves) <= MAX_LEAVES`. Raises `DagError` otherwise.
- `topo_order(dag) -> [Node]` — leaves before the `main` node, dependencies first.
- `MAX_LEAVES` from `pipeline.toml [decompose] max_leaves` (default **3** — tight
  splits first; a DAG that wants more is usually mis-shaped. Raise on evidence).

## 2.2 — Decomposer call (`draft_decomposition(target, context_pack, *, chat_fn)`)

`DECOMPOSE_SYSTEM` embeds the harvest's **B-class playbook** + the Task-1.2
statement-design authority (pins + faithful-statement rules). The playbook, as the
decomposer's operating instructions:

- **B1 spike the riskiest kernel first** — the one leaf most likely to be
  impossible (a missing primitive, a hard analytic step) is leaf #1; if it can't be
  stated cleanly, the whole split is wrong.
- **B2 recon by conclusion-head** — name each leaf after the Mathlib/MathFin result
  family its conclusion belongs to, so the prover consumes the right lemma.
- **B4 definition-shaping** — shape a leaf so its hard side-conditions are
  *inherited* from a closed structure, not asserted (the Task-1.2 rule).
- **B5 skeleton-with-sorries** — the main theorem's proof is written as leaf
  applications with the leaves `:= by sorry`; it must ELABORATE (that is 2.3).
- **B6 scope-fork with declared deferral** — a leaf that is out of current reach is
  split off and declared `deferred` (→ `refs`, not `closes`), never silently
  dropped.
- **B7 bank green rungs** — a leaf that proves is kept even if a sibling fails.

Output contract: JSON only; each leaf a Lean theorem signature with pointers; the
main node's proof sketched as leaf applications. Malformed reply ⇒ one re-ask
round, then a structured failure (no infinite loop). Engine = injected `chat_fn`.

## 2.3 — Skeleton-elaboration gate (the load-bearing check)

`assemble_skeleton(dag, meta) -> lean_text` writes a module: every leaf lemma
`:= by sorry`, the main theorem proved by a term/tactic **applying the leaves**
(NOT `sorry`). `skeleton_gate(lean_text, *, check_fn)`:

- **passes iff** elaboration is clean AND `sorry_count == n_leaves` (the main node
  carries no sorry — its proof genuinely reduces to the leaves).
- daemon infra-error ⇒ **indeterminate** (reuse Task 1.4's `error` sentinel), never
  a false pass.
- on gate failure: **one** bounded re-decomposition round (feedback = the
  elaboration errors), then structured failure.

This is where a bad decomposition dies for one elaboration's cost, before any leaf
gets proving budget — the mid-tier-reasoner compensation.

## 2.4 — Leaf routing through the existing prove+gate path

- `build_manifest.py` learns lemma-DAG leaves: targets carrying `parent` +
  `dag_order`; per-leaf single-`sorry` stub contract unchanged.
- Leaves reuse `vibe_prove.py run/gate` VERBATIM — they are ordinary single-`sorry`
  targets. The parallel Lean pool (Phase 3.1) runs independent leaves at once.
- `pipeline-tick.sh` takes the decompose path for a target tagged `decompose` OR
  after N failed plain attempts (`[decompose] enabled/max_leaves/leaf_max_turns`).
- All leaves `pass` ⇒ 2.5; partial ⇒ record per-leaf outcomes.

## 2.5 — Recompose + keep-and-revise

`recompose(dag, proved_leaves, *, check_fn)`:

- **all leaves proved** ⇒ assemble the module (leaves with real proofs + main) and
  run the FULL gate (`gate.gate`); open-pr places leaves + main in one module and
  lists the DAG in the PR body.
- **partial** ⇒ proved leaves are BANKED as run artifacts + flagged as standalone-PR
  candidates if independently valuable; unproved leaves recorded as a declared
  remainder (`deferred`, `refs` not `closes`) — never a silent gap.
- **keep-and-revise**: a proved leaf's statement+proof is appended to the target's
  context pack on the next decomposition attempt (the loop learns within a target).

## 2.6 — Performance scoreboard (decision-gate evidence)

The measurement is **Magistral's absolute performance** on the real queue — what says
whether the decomposer works and whether a change helped (its own numbers improve).
Summary rows gain `arm ∈ {cron, decompose}` (both Mistral), `leaves_total`/
`leaves_closed`, and `refinery_minutes` (filled by hand at merge).
`docs/research/ab-decomposer.md` is the running scoreboard: one row per target-attempt.
The single question it answers: **does the decomposition loop close hard targets the
plain `cron` path cannot, for the tokens it costs?** No Claude/centaur arm — production
is Mistral-only, and R is the PR reviewer plus an independent author, not a tracked
pipeline component.

## 2.7 — First-pass refinery punch list

A Magistral review call over the final candidate producing the *mechanical* half of
the 8-lens checklist (unused/gratuitous constructs, wrapper smell, docstring/
register, obvious golf) — a starting point for the human refinery. **Soft; never
gates.** The taste half (inspired math, architecture) stays human/Claude.

## The 2026-09-30 decision-gate inputs

Keep-Magistral (now paid) / frontier-decomposer / hybrid, decided on: Magistral's
accumulated absolute track record (leaves-closed, merge-rate, refinery-minutes) and the
actual Labs price sheet. Good enough → keep it. If it is not and the call is close, run
a ONE-TIME focused frontier eval at the gate (the `chat_fn` interface makes that a config
swap). No continuous second arm before then.

## As built (2.1–2.8)

- `probe/decompose.py` — schema/validation (`parse_dag`/`topo_order`/`MAX_LEAVES=3`),
  the decomposer call (`draft_decomposition` + `DECOMPOSE_SYSTEM`, brace-aware JSON
  extractor, a `feedback` seed for the skeleton re-decompose), `assemble_skeleton` +
  `skeleton_gate`, `build_leaf_manifest` (per-leaf single-sorry stubs), `recompose` +
  `extract_leaf_decl`, `dag_to_dict`.
- `probe/decompose_tick.py` — `do_draft`/`do_recompose` (injected chat/check fns,
  unit-tested with no API/daemon), plus the CLI wiring real Magistral + the daemon.
- `scripts/decompose-tick.sh` — the leaf-prove orchestration: one daemon↔lsp flip pair
  for all leaves, records a vibe-shaped summary row + the A/B scoreboard row.
- `scripts/pipeline-tick.sh` — takes the path ONLY when `[decompose].enabled` AND the
  target is tagged `decompose`; otherwise the plain path is byte-identical.
- `scripts/open-pr.sh` — lists the DAG (sidecar-guarded) + embeds the first-pass refinery
  punch list; `probe/scoreboard.py` + `docs/research/ab-decomposer.md` are the A/B evidence.
- **Deferred refinement:** deep `depends_on` chains prove independently in v1 (the flat
  split is the MAX_LEAVES=3 common case); keep-and-revise inlining of a proved dependency
  into a dependent leaf's stub is supported by `build_leaf_manifest(proved=…)` but not yet
  driven by the loop. **Live end-to-end run is an operator action** (paid Magistral + the
  single local Lean slot), like Phase 0's Control A.

## Decisions (R delegated, 2026-07-18)

1. **MAX_LEAVES = 3** — tight splits first; raise on evidence if real targets need
   deeper DAGs. A split that wants >3 leaves is usually mis-shaped.
2. **Decompose trigger = tag-only** (`decompose` label). Conservative: no decomposer
   tokens on targets a patterns.md tweak or a plain retry would fix. Auto-after-N is a
   later tuning, gated on the obstruction report showing a decompose-shaped family.
3. **No centaur/Claude arm.** Production is Mistral-only; the scoreboard is `cron` vs
   `decompose`. R's own manual proofs are independent author work, not a tracked arm.

## Update 2026-07-29 — go-live reconciliation + structural-split playbook

Two corrections to the sections above, which predate the same-day go-live commit
(`0a2e277`, written ~90 min after this doc):

- **Decompose is LIVE and `enabled = true`** (since 2026-07-18). The "tag-only /
  byte-identical" framing in *As built* / Decision 2 is superseded: the path now takes
  **three triggers** — a `-- decompose` tag, a `decompose=true` workflow_dispatch one-shot,
  and **autonomous failure-escalation** (a plain prove that hits `max_rounds`/`fail_gate`
  re-routes that same target through the lemma-DAG path, `pipeline-tick.sh`). CI decides to
  decompose exactly what plain proving fails on.
- **Drafter is Claude** (`[drafter].claude_model = claude-sonnet-5`), not Magistral — the
  CLI wires `claude_draft_fn`. (Leanstral still PROVES the leaves.)

**Structural-split playbook (B8) — no schema change.** A hard target whose difficulty lives
*inside one proof* (a piecewise payoff → case split; an n-period/CRR recursion → induction;
a one-step reduction → suffices) needs no new node kind: `main.proof` is arbitrary Lean, so
the main DISPATCHES to sorried leaves via a tactic, and the existing skeleton gate validates
it. `DECOMPOSE_SYSTEM` now teaches the three moves:

- **case-split** — one leaf per branch carrying its branch hypothesis; main dispatches
  `by rcases <disc> with h | h` / `by_cases`. A non-exhaustive split fails the gate for free
  (a missing branch leaves an extra goal).
- **induction** — a BASE leaf (goal at 0) + a STEP leaf taking the IH as an explicit premise
  (`(k : ℕ) (ih : P k) : P (k+1)`); main dispatches `by induction n with | zero => … | succ k ih => …`.
- **goal-reduction** — isolate the core fact `Q` as its own leaf + a reduction leaf; main
  `by suffices h : Q by …` or `by exact <reduce> <core>`.

Evidence (2026-07-29, real elaborator via the lean-repl daemon): hand-written skeletons for
all three shapes elaborate clean — `errors: []`, `sorry_count == n_leaves` (a `skeleton_gate`
PASS). So this is a decomposer *guidance* upgrade validated by the gate we already run, not a
schema change. Tests: `test_decompose.py::{test_decompose_system_teaches_structural_split_patterns,
test_cases_dag_assembles_and_gates, test_induction_dag_assembles_and_gates}`.

**Hardening (built 2026-07-29/30):**

- **Orphan-leaf check** — `parse_dag` now rejects a leaf the main proof never dispatches to
  (directly, or transitively via a sibling's `depends_on`): dead weight that would burn prover
  budget. Reachability is word-bounded substring matching over `main.proof`, closed under
  `depends_on`; over-counting a reference can only under-reject, never falsely reject a good
  DAG. Gated on a REAL main proof — the schema-validation shape (empty/sketch proof) skips it.
  `DECOMPOSE_SYSTEM` states the rule so the decomposer avoids emitting orphans.
- **`applied_to`** — an optional leaf field (`list[str]`, the Mathlib/MathFin lemmas the leaf's
  proof will consume, inspired by LeanAide's `deduced-from`). It round-trips through the
  persisted DAG and is surfaced to the vibe prover as an `-- apply: …` hint comment in the leaf
  stub (a Lean line comment — no `sorry`, no elaboration effect; sliced off by
  `extract_leaf_decl` at recompose). A CONSUMED field, not a dead one.

Both are pure/host-side, unit-tested (`test_decompose.py::{test_dag_rejects_orphan_leaf,
test_dag_orphan_check_follows_depends_on_and_skips_sketch, test_applied_to_*}`), and preserve
every prior fixture.
