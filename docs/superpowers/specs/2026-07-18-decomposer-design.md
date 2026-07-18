# Magistral decomposition loop — mechanics design (Task 2.0)

> **Status: SIGNED OFF — R delegated the design decisions (2026-07-18); 2.1+ in progress.** This is a
> *mechanics* doc — the strategy is fixed by
> `docs/superpowers/plans/2026-07-18-autoformalization-improvements.md` (centaur
> architecture; general-reasoner role filled by Magistral for now; decision gate
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

Interface is **model-agnostic** (`chat_fn` injection). Magistral is the production
reasoner — the automated loop is **Mistral-only**. A manual/Claude centaur session
(the human-oversight layer at the top of the centaur, e.g. Phase 0 Control B) can log
its outcome as a REFERENCE row: opportunistic, never a scheduled competitor.

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

The PRIMARY measurement is **Magistral's absolute performance** on the real queue —
that is what says whether the decomposer works and whether a change helped (its own
numbers improve). Summary rows gain `arm ∈ {cron, decompose-magistral, centaur}`,
`leaves_total`/`leaves_closed`, and `refinery_minutes` (filled by hand at merge).
`docs/research/ab-decomposer.md` is the running scoreboard: one row per
target-attempt. `centaur` rows are **opportunistic** — logged when a manual
hard-target session (the human-oversight layer, e.g. Phase 0 Control B) naturally
happens. They are a free frontier reference for the 2026-09-30 gate, NOT a scheduled
Claude arm and NOT the per-tick signal: production stays Magistral-only.

## 2.7 — First-pass refinery punch list

A Magistral review call over the final candidate producing the *mechanical* half of
the 8-lens checklist (unused/gratuitous constructs, wrapper smell, docstring/
register, obvious golf) — a starting point for the human refinery. **Soft; never
gates.** The taste half (inspired math, architecture) stays human/Claude.

## The 2026-09-30 decision-gate inputs

Keep-Magistral (now paid) / frontier-decomposer / hybrid, decided on: Magistral's
accumulated absolute track record (leaves-closed, merge-rate, refinery-minutes), the
opportunistic centaur reference rows, and the actual Labs price sheet. If the call is
close, run a ONE-TIME focused frontier bake-off at the gate — we do NOT run a
continuous Claude arm before then (that would mix vendors for evidence we can
assemble more cheaply at decision time).

## Decisions (R delegated, 2026-07-18)

1. **MAX_LEAVES = 3** — tight splits first; raise on evidence if real targets need
   deeper DAGs. A split that wants >3 leaves is usually mis-shaped.
2. **Decompose trigger = tag-only** (`decompose` label). Conservative: no decomposer
   tokens on targets a patterns.md tweak or a plain retry would fix. Auto-after-N is a
   later tuning, gated on the obstruction report showing a decompose-shaped family.
3. **Centaur = opportunistic, not an arm.** No scheduled cadence; log a centaur row
   when a manual hard-target session happens. Production is Magistral-only.
