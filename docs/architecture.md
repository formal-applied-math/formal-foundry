# MathFin autoformalization pipeline — architecture

An autonomous **issue → PR** pipeline: a GitHub Actions cron (every 2 days) picks a
`status:ready`+`type:proof` issue on **formal-mathfin**, drafts a faithful Lean stub,
proves it, gates it kernel-clean, runs the library's own CI on the result, and opens a
ready-for-review PR. One model vendor, two separate sessions: a **drafting** Claude
session owns everything up to the statement (intent · agentic formalize · faithfulness
judge · decomposition), and a **proving** Claude session, which never sees the issue,
owns the proof. Lean's kernel checks both. The only human touchpoint is reviewing and
merging the PR.

Pins: read from the library's checkout at tick time (`lean-toolchain`,
`lake-manifest.json`); the verify image is built from the same commit.
Models: `[autoformalize].claude_model` drafts and judges, `[prover].claude_model` proves
(both `claude-sonnet-5-5` in `pipeline.toml` today).

The July 2026 paper and its figure, which show the pipeline with its first prover, are
in [`research/2026-07-pipeline-paper/`](research/2026-07-pipeline-paper/).

## The tick, end to end

1. **SOURCE** — GitHub issues on formal-mathfin (`status:ready` + `type:proof`). The
   queue self-feeds: with no unattempted target, the tick drafts one from the next
   ready issue. A cross-tick lesson carries the compressed post-mortem of an earlier
   failed attempt on the same issue into the new draft, so a retry is not blind.
2. **DRAFTER** — Claude, two stages, into an elaborating Lean stub: *intent*
   (precise prose + the objects it must consume + naming meta), then *agentic
   formalize* — one `claude -p` session wired to the same lean-lsp MCP the prover
   uses, writing the module against live diagnostics and self-validating to
   elaboration; *emit* assembles the module mechanically with a deterministic
   pre-lint (strip model imports · `Type u`→`Type*` · `open MeasureTheory` ·
   `autoImplicit false` · placement that cannot collide with an imported module) and
   a prose screen on drafted docstrings. The session grounds itself in the library
   checkout, the pointer modules' signatures and live `lean_loogle`; a feasibility
   census at intent time drops an intent that names MathFin primitives which do not
   exist.
   Route is `theorem` or `defs` (introduce missing primitives); a target may
   honestly formalize a declared subset.
3. **PRE-PROVE GATES** — the semantic battery on an elaborating draft,
   cheapest-first: depth (theorem route) or defs-consumption + instance probe (defs
   route) → the map gate (the statement must rest on the library's blueprint) →
   triviality (structural, zero tokens) → issue-faithfulness (Claude judge). Two
   kernel probes, hypothesis-rejection (⊢ False) and disproof (⊢ ¬Concl), are
   available and off (`[autoformalize].kernel_probes`); when on they are
   content-addressed, so a repeated goal costs no tokens and no Lean call across
   attempts and ticks. A rejection feeds its verdict back
   and **re-drafts** (semantic-repair cascade, ×2). The battery's diversity is the
   anti-Goodhart defense: a re-draft that games one gate still faces the others.
4. **PROVE** — the prover session drives the live goal state. Two paths: **plain**
   (`claude -p ⇄ lean-lsp-mcp`, one deep agentic session, turns 60) and **decompose**
   (Claude splits the target into ≤3 leaf lemmas + a main theorem → a
   skeleton-elaboration gate → prove each leaf as an ordinary single-sorry target,
   turns 40 → recompose + full-gate). Decompose is routed by a force flag, a
   `decompose` tag, or **autonomous failure-escalation** (plain can't close it ⇒
   escalate the same target to decompose).
5. **LEAN ENVIRONMENT** — Docker, memory-capped 6 GB, one Lean process at a time
   (`lean-repl daemon` XOR `lean-lsp server`), reusing the prebuilt oleans. The tick
   flips that single slot to lean-lsp around the agentic drafter and the prover, and
   back to the daemon for the gates. The models run on their providers' servers, so
   only the ~5 GB Lean env is local.
6. **ACCEPTANCE GATE** — the honesty floor: no errors, no `sorry`, axioms ⊆
   `{propext, Classical.choice, Quot.sound}`, no forbidden tactics, the
   statement-integrity pin (the accepted proof must still assert the *drafted*
   statement — no weakened conclusion, no dropped binder), verification ledger
   (input-hash freshness), AxiomAudit (build-enforced). Post-proof polish — golf,
   unused-hypothesis strengthening, import trim — is re-gated before it counts.
7. **ASSEMBLY + AUTO-PR** — on a pass, place the module and its corpus entry where
   the library's taxonomy says, run the library's CI from the pack
   (`domains/<name>/regen.sh`, `host-gates.sh`), and open a ready-for-review PR on
   formal-mathfin. It closes the source issue, or `refs` it when the issue names a
   frontier node. The body carries the refinery notes (and the DAG, for a decompose
   proof); the entry's `provenance.model` names the model that proved it.
8. **REVIEW & MERGE** — the only human touchpoint: an 8-lens values review (the
   machine proof is a scout, not the author) refines it to the conceptually-right
   proof, then merges into the library.

## Same thing as Mermaid (renders on GitHub)

```mermaid
flowchart TD
  I["① SOURCE — GitHub issues (formal-mathfin)<br/>status:ready + type:proof · self-feeds the queue · cross-tick lessons"]
  D["② DRAFTER — Claude, two stages<br/>intent · prose + objects + naming meta<br/>agentic formalize · claude -p ⇄ lean-lsp MCP, self-validating to elaboration<br/>emit · assemble + pre-lint (strip imports · Type u→Type* · autoImplicit false)<br/>grounded in the checkout, pointer signatures and lean_loogle"]
  G["③ PRE-PROVE GATES — cheapest-first<br/>depth (theorem) | defs + instance probe (defs) → map gate → triviality<br/>→ issue-faithfulness (Claude judge)<br/>optional kernel probes (⊢ False, ⊢ ¬Concl), off by default"]
  P["④ PROVE — a separate Claude session drives the goal state<br/>plain · claude -p ⇄ lean-lsp-mcp (turns 60)<br/>decompose · Claude splits ≤3 leaves + main → skeleton gate → prove leaves (turns 40) → recompose<br/>routed by: force flag · decompose tag · autonomous failure-escalation"]
  E["⑤ LEAN ENVIRONMENT — Docker, mem-capped 6 GB<br/>one Lean process: lean-repl daemon XOR lean-lsp server · reuses oleans"]
  A["⑥ ACCEPTANCE GATE — honesty floor<br/>no errors · no sorry · axioms ⊆ standard 3 · no forbidden tactics<br/>statement-integrity pin · ledger · AxiomAudit · polish re-gated"]
  PR["⑦ ASSEMBLY + AUTO-PR → formal-mathfin<br/>the library's CI from the pack · ready-for-review PR · refinery notes + DAG · provenance names the prover"]
  H["⑧ REVIEW & MERGE — the only human touchpoint<br/>8-lens values review → conceptually-right proof → merge"]

  I --> D --> G --> P
  G -. "reject → re-draft (semantic cascade ×2)" .-> D
  P -->|candidate .lean| E
  E -->|goal states + errors| P
  E --> A --> PR --> H
```
