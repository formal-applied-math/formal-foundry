# Grind-history lessons harvest → foundry hardening

Mined from the main repo's formalization session history — the thousands of
hours of hand-driven Lean 4 proving that built MathFin (Summit A/B/C Itô tower,
Feynman–Kac BS-PDE, FTAP rungs, SDE existence/uniqueness, Girsanov, the Itô–Lévy
tower, the v4.31 co-bump port). This is the *how* of that grind — the moves,
repairs, search recipes, and guards that made hard formalizations succeed or
wasted hours — distilled into what the autonomous pipeline should learn.

**Companion docs.** This is a research/evidence doc (like
`2026-07-17-ml4tp-zulip-harvest.md`), and its actionable output is folded into
`docs/upgrade-backlog.md`. It reads *our own* grind, where the
2026-07-11 survey read the external field.

**Authority note.** `docs/patterns.md` (in the main repo) is the shared authority
for **both** repos. The single cheapest way to propagate most of these lessons is
*through patterns.md* — the foundry prover already live-reads its full current form
(`house_context.build_system_prompt`), so a lesson added there reaches the prover
and the human loop at once. The gaps this harvest exposes are (a) the pipeline's
**drafter/intent** stages read none of it, and (b) the cheap deterministic
transforms deserve to be *code*, because prose in an LLM prompt still costs a
whole repair round.

---

## Method & provenance

Seven session-eras were mined in parallel, each by a dedicated reader over the
raw transcript (`~/.claude/projects/.../*.jsonl`), extracting the narration layer
(assistant reasoning, user redirections, shell commands, file edits, and
error-bearing tool results; secrets redacted) and grepping it for the
stuck→unstuck moments, error→fix repairs, search recipes, and time-sinks:

| Era | Dates | Content |
|---|---|---|
| E1 | Jun 1–7 | Summit A time-dependent Itô; whole-repo values audit; grind/loogle/hammer trials; Merton layer |
| E-jun6 | Jun 6–23 | Feynman–Kac → BS-PDE keystone (heat-kernel, *not* Itô); Summit B stochastic-integral CLM |
| E24 | Jun 24–27 | BM + Mathlib v4.31 port ("library-green ≠ corpus-green"); FTAP rungs 2/3/4 (Esscher EMM) |
| E-jun27 | Jun 27–29 | Itô-as-process; [0,∞) gluing; Summit C double-cutoff localization; Phase-1 convex duality |
| EM | Jun 27–Jul 5 | The mega-grind: SDE Picard existence; L²-Grönwall uniqueness (built, Mathlib lacks it); numéraire/Girsanov |
| EG | Jul 9–10 | Girsanov bounded-predictable θ; a.e.-subsequence engine; BM PR #484 golf review |
| EL | Jul 11–18 | Itô–Lévy CLM tower; the `extendOfNorm`-into-a-submodule instance diamond; HJM ratification |

**Ranking principle.** A lesson observed *independently in N eras* is load-bearing;
a one-off is noise. The stars below (★–★★★★★) are era-frequency. That convergence
is the whole point: the same dozen failure modes and the same dozen unlocking
moves recur across every hard proof, which is exactly what an autonomous loop can
be taught once.

---

## The strategic finding (read this first)

**The funnel dies at the drafter, not the prover.** Every prove attempt that
reached the vibe harness passed (cal-bk-53/66/67/85/88). The refill telemetry
shows the losses are all *upstream*: `depth-gate: statement consumes no def from
pointer modules`, `formalize: no elaborating Lean after 3 rounds`, hallucinated
constants (`MathFin.omegaRatio`, `MathFin.zcb`). Whatever gets a valid, in-depth
stub essentially always gets proved.

**And the drafter is the least-equipped stage.** The prover (Leanstral vibe) is
handed the full live `patterns.md` + the house doctrine + the pins. The
intent → formalize → judge → fidelity → golf stages get purpose-built prompt
contracts with *none* of that authority — no pins, and no statement-design
guidance at all. `patterns.md` today is ~all proof-mechanics (right for the
prover) and has almost nothing on *statement design* — which is precisely where
the machine keeps dying.

So the highest-leverage work is not a better prover. It is: **equip the
intent/formalize stages with the shared authority, add the statement-design
lessons to that authority, and turn the cheapest recurring repairs into code.**

---

## Convergence matrix

Notation: ★ per era it appeared in. "Slot" = where it belongs in the pipeline.

### A. Deterministic repair transforms (cheap, near-pure error→fix)

These are the recurring compiler-error→fix mappings. The drafter subset (errors
that block *statement* elaboration) belongs in code (`_repair_hint` +
emit-time pre-lint). The full table belongs in `patterns.md` as a "Repair table"
the prover consumes live.

| # | ★ | Error signature → fix | Slot |
|---|---|---|---|
| A1 | ★★★★★ | Un-beta-reduced lambda / Pi-combinator form ("did not find occurrence", "made no progress" on a function-shaped goal) → `show <beta-reduced>` / `simp only []`, or type-annotate the `have` with the single-lambda form; diagnose a stuck `convert` with `exact h`. **The #1 error family in every era.** | patterns.md repair table; prover |
| A2 | ★★★★★ | Unknown identifier → grep the **pinned** `.lake/packages/mathlib` source, never re-guess; loogle tracks a newer pin (upper bound only); consult a rename cache. `abs_add→abs_add_le` was re-discovered 4× in one session. | code (`_repair_hint` + rename cache); prover |
| A3 | ★★★★ | Stuck typeclass / metavariable `Class args ?m.N` → name the implicit (`(μ := μ)`) / `@`-apply / pre-typed `have`. Pure syntactic transform. | code (`_repair_hint`); prover |
| A4 | ★★★ | `zero_le`-style bare-vs-applied ("Function expected at X … type `0 ≤ ?m`") → drop the argument, use the bare term; toggle primed. 12 hits in EM alone. | patterns.md; prover |
| A5 | ★★★ | `omit`/`include`/`set_option … in` **before** the docstring; `Σ`/`Π`/`λ` are reserved in identifiers. Pure text/emit rule. | code (emit pre-lint) |
| A6 | ★★★★ | Stale-olean cascade (mass "unknown namespace MeasureTheory" in a file importing a just-edited sibling) → classify INFRA-STALE, rebuild the dep, do **not** touch the proof. | code (repair classifier); prover |
| A7 | ★★ | Wrong σ-algebra from an explicit annotation (two `MeasurableSpace` instances on one carrier) → drop the annotation, or `letI`-pin the sub-σ. | patterns.md; prover |
| A8 | ★★ | Dot-notation "Invalid field" on `And`/`Exists`/a `def` reducing to `And` → call by full name, or `obtain` first. | patterns.md; prover |
| A9 | ★★ | "No goals to be solved" → delete the trailing tactic (cascade after a root error; fix only the first root error). | patterns.md; prover |
| A10 | ★ | Ambiguous term (`intervalIntegral` vs `MeasureTheory`) → qualify by the goal's integral syntax. | patterns.md; prover |
| A11 | ★ | `ℝ≥0` misparse ("failed to synthesize `LE Type`/`OfNat Type`") → `open scoped NNReal`; `𝓝` → `open Topology`. Unique signature. | code (`_repair_hint`); prover |
| A12 | ★ | Cast-in-λ-over-ℕ mismatch → put the ℕ-atom leftmost / ascribe the binder; write `(2:ℝ) •`, never bare `2 •`. | patterns.md; prover |

### B. Strategy & decomposition (the high-leverage zone — the funnel dies here)

| # | ★ | Lesson | Slot |
|---|---|---|---|
| B1 | ★★★★★ | **Spike the make-or-break kernel** on the daemon *before* any infrastructure — a throwaway `_scout.lean` of `example : Inst := inferInstance` + `#check @name` batteries. Every scout changed the design; the recurring phrase is "zero rework." | prover phase; drafter name-check |
| B2 | ★★★★★ | **Template/reuse recon by conclusion-head** across repo + deps before authoring. "Consume, don't rebuild." A "missing infrastructure" verdict is legal only after a *shape-level* (not name-level) search. Collapsed feared 150-line builds into 3-line applications repeatedly. | context pack (add head-retrieval) |
| B3 | ★★★★ | **Feasibility census**: score candidate routes by number of primitives missing at the pin; pick the zero-missing route or emit "blocked-on-infra." This is the antidote to the depth-gate "needs_primitives" death family. | intent stage |
| B4 | ★★★★ | **Definition-shaping so hard side-conditions are inherited**, not proved: define the object as a closure/limit in a limit-closed class (`topologicalClosure`, `Lp`) so density/predictability is soft; diagonalize to reduce a matrix ODE to the scalar lemma; carve a `Submodule` instead of a bespoke `structure`. Antidote to the depth-gate "consumes no def" death. | intent/defs stage; patterns.md |
| B5 | ★★★★ | **Skeleton-with-sorries**: elaborate the *assembly* green before discharging any analytic fact; close sorries easiest-first, each its own repair loop. | prover strategy |
| B6 | ★★★★ | **Scope-fork on cost inflation**: ship the weaker-but-green result now with a *declared* deferral; never silently weaken and never grind. Estimate size from the dependency read. | descope exit; deferred metadata |
| B7 | ★★★ | **Bank each library-green rung** (commit-per-green-lemma); the benchmark entry rides the headline rung; enables restart-from-green under an unreliable checker. | PR/decomposition policy |
| B8 | ★★ | **Existence template** (Esscher/potential): `withDensity` reduction → convex potential whose FOC *is* the target identity → coercivity transverse to the degeneracy kernel → bounded strictly-positive density. | prover playbook (patterns.md) |
| B9 | ★★ | **Localization scaffold**: `ContDiffBump` cutoffs; exit times on *closed* sets over the raw filtration; define the unbounded object explicitly and prove agreement on the stopped event (no gluing infra). | prover playbook (patterns.md) |
| B10 | ★★ | **Wrapper→full = 4 named plan templates**: packaging/conjunction; identification-transport (find the bridge lemma); decomposition-transport (equality part + monotone part); pointwise certificate for variational statements. | prover playbook (patterns.md) |
| B11 | ★★ | On the **2nd instance of a shape**, extract the parameter-generic engine first, then instantiate (deleted ~450 lines once). | decomposition policy |
| B12 | ★★ | **Don't integrate over `↥Submodule`** (instance swamp) — stay ambient with subset/membership hypotheses; use `{m : MeasurableSpace}` implicit when several σ-algebras coexist. | drafter rule; patterns.md |

### C. Hypothesis & fidelity (refines existing gates)

| # | ★ | Lesson | Slot |
|---|---|---|---|
| C1 | ★★★ | **Hypothesis honesty**: derive what is derivable; hypothesize only proven-elsewhere facts about opaque operators. (= the foundry derivable-probe — validated by the grind.) | derivable gate (built) |
| C2 | ★★★ | **Unused-hyp strip by delete-and-re-elaborate, never the linter alone — BUT keep a binder that is the sole pin of an implicit variable** (removing `_hBmeas` broke `B` synthesis everywhere), and whitelist ≠0/positivity binders under grind/nlinarith (linter false-positives on binders consumed inside generated proof terms). | strengthen pass — **refine before it fires** |
| C3 | ★★ | Witness-carrying theorem → an ∃-discharged corollary is the benchmark statement. (= foundry corollary-shape — validated.) | corollary shape (built) |
| C4 | ★★ | Minimal-typeclass weakening is verifiable mechanically: weaken, then check all in-repo callers still elaborate (instance implication covers them). | strengthen pass |
| C5 | ★★ | **Non-vacuity / vacuous-premise detection**: a premise satisfiable by a junk witness ⇒ the theorem says nothing (a round-5 "fidelity fix" once made a spec uninhabitable). Ship a satisfiability `example` (the zero object) for ≥3-hypothesis bundles. Distinct from the existing "contradictory-premise" (prove `False`) and "trivial-conclusion" gates. | new gate |
| C6 | ★★ | Signature change → re-elaborate all snippets + diff the hypothesis set against the entry's prose metadata (the restriction-in-disguise detector). | fidelity/strengthen |
| C7 | ★ | External source = **source, not template**: machine-checkable `external_role`/`license`/`pin_compatible`/`their_axioms`; reject a draft that imports a source-role repo; feed the model their *statement*, never their proof. | intake policy |

### D. Infra, harness & telemetry

| # | ★ | Lesson | Slot |
|---|---|---|---|
| D1 | ★★★★ | **Daemon-green ≠ build-green**: pin `autoImplicit false` (done, `be1c83a`); `@[expose] public section` gate (done); the final gate is a cold build; classify autoImplicit "Function expected … ?m.1" as unknown-identifier, not a type error. | gates (mostly done) |
| D2 | ★★★ | **Corpus-persistent rename cache** `old→new`, applied as a sed pre-pass before the first check and appended on every rename fix. Kills a whole recurring class at every pin bump. | new (in main repo, both read) |
| D3 | ★★★ | **Verdict from the daemon's own log, not the client exit code**; a report emitted within N seconds of a respawn is untrusted (re-check); single-Lean-process invariant (no cold fallback while a daemon exists — the fallback double-loads Lean and OOM-kills the daemon: the observed death-spiral). | harness |
| D4 | ★★ | **Restale-blast-radius scheduling**: compute the transitive-importer set before any cross-file move; batch or defer if it exceeds a threshold. The ledger primitive exists; the scheduler is new. | telemetry/scheduling |
| D5 | ★★ | Axiom gate = `collectAxioms ⊆ allowlist` (via `run_cmd`), **not** exact-match `#guard_msgs` (which rejects proofs that use *fewer* axioms). (Foundry already does this in `axiom_guard_block` — keep it.) | gate (done) |
| D6 | ★★ | One canonical build path via the wrapper; an ad-hoc `--entrypoint` re-downloads the whole toolchain (multi-hour). | harness |
| D7 | ★ | Probe/scratch files live *outside* the `MathFin/` glob, or they poison the build gate. | prover rule |
| D8 | ★ | Never batch-apply multiple suggested diffs without an elaboration between them (they reference each other's dead scaffolding; the suggestion text itself may be malformed). | repair loop |
| D9 | ★ | Benchmark JSON edits = id-keyed, byte-preserving surgical inserts + round-trip parse. | emit |

---

## Foundry cross-reference

### Already wired — don't redo
- Two-stage intent + formalize, bounded compile-repair, semantic-repair cascade.
- Depth/defs/triviality gates; faithfulness + fidelity judges.
- Derivable-hypothesis probe (**C1**), ∧-bundle advisory, corollary shape (**C3**).
- Kernel/axiom gate as `collectAxioms ⊆ allowlist` (**D5**).
- `autoImplicit false` pinned in the emitter; `@[expose] public section` gate (**D1**).
- Dependency-closure context packs; embedding + loogle retrieval.
- Input-hash ledger (the primitive behind **D4**).

### The asymmetry (the headline gap)
The prover gets the full live `patterns.md` + doctrine + pins. The intent,
formalize, judge, fidelity, and golf stages get none of it. The tactic-exemplar
channel that *would* carry house-style goal→tactic examples is **dead** — the
`tactics.jsonl` index is never built (`SCOUT_TACTICS` unset; excluded from the CI
cache). And the entire post-gate polish suite (strengthen, trim, golf, derivable,
advisory, CI-parity, preflight) is younger than the last production run — **none
of it has fired in anger yet**, so its first live exercise is also its first test.

---

## Hardening backlog (ranked)

Tags: **[patterns.md]** = propagate through the shared authority (least code, both
repos); **[code]** = a foundry code change prose can't do; **[harden]** = closes a
silent-failure hole.

- **H1 — Equip the drafter with the shared authority. [code] [highest leverage]**
  Inject a *statement-design* subset of `patterns.md` + the pins block into the
  intent and formalize system prompts (`INTENT_SYSTEM`, `FORMALIZE_SYSTEM`,
  `house_context.build_system_prompt`). The prover subset is proof-mechanics; the
  drafter subset is statement design (H2). This attacks the depth-gate/formalize
  death cluster directly.

- **H2 — Add a "Statement design" section to `patterns.md`. [patterns.md]**
  It barely exists today, and it is where the machine dies. Draft content below.
  Covers B4 (definition-shaping for inherited side-conditions), coe-outward casts
  (E-jun6 L5), named-defs-for-derived-measures (EL L18), `=ᵐ`/condExp over pointwise
  (E-jun6 L14), eta-form hypotheses (EM L14), `Submodule`-not-`structure` (EL L15).

- **H3 — Add a "Repair table" section to `patterns.md`. [patterns.md]**
  The A1–A12 error→fix mappings as one compact table. The prover reads it live;
  the human loop gets it too. Draft content below.

- **H4 — Promote the cheap statement-elab transforms into code. [code]**
  Extend `_repair_hint` (currently 2 rules) with A3 (stuck-metavar → name the arg),
  A11 (`ℝ≥0` → `open scoped NNReal`), and A2's grep-the-pinned-source; add an
  emit-time pre-lint for A5 (`omit`/`set_option` before docstring; ban `Σ`/`Π` in
  idents). Each saves a full LLM repair round. These are deterministic and pure.

- **H5 — A wedged daemon must not silently pass the fail-open gates. [harden]**
  All four structural probes (depth/defs/triviality/derivable) fail *open*, and a
  wedged daemon returns an error-dict they read as "no verdict" = pass — so one
  wedged tick passes every structural screen. Fix per D3: take the verdict from the
  daemon log, treat a report within N s of a respawn as untrusted, and health-check
  the daemon before trusting a fail-open pass.

- **H6 — Corpus rename cache. [code]** `unknown_id → confirmed_replacement`, stored
  in the main repo (both repos read it), applied as a pre-pass and appended on every
  rename fix (D2). `abs_add→abs_add_le` re-discovered 4× in one session; recurs
  every pin bump.

- **H7 — Conclusion-head template retrieval in the context pack. [code]** Surface
  the nearest existing *proof* (by conclusion head-constant) as a skeleton, not just
  signatures (B2/B10). This is the live replacement for the dead tactic-exemplar
  channel.

- **H8 — Refine the strengthen pass before it fires. [harden]** Add the
  sole-implicit-pin syntactic guard (EM L23: don't strip a binder that is the only
  occurrence of an implicit variable) and the grind/nlinarith ≠0-binder whitelist
  (E1 L21) *before* attempting a strip. The full re-gate is a backstop, but the
  load-bearing-binder case will appear and this avoids a broken PR or a wasted round.

- **H9 — Close the small silent holes. [harden]** Make a refill crash
  distinguishable from an empty backlog (`pipeline-tick.sh` collapses both to
  `{"seeded":[]}`); fix the rfl-guard shell-pattern gap (`open-pr.sh:143`, a
  `:= by rfl` not at EOF escapes it); switch the vibe LSP-readiness check from a
  log-grep-that-proceeds-anyway to a port-probe (it regressed to the exact
  anti-pattern `wait_daemon.py` was built to kill).

- **H10 — Vacuous-premise + restriction-in-disguise gates. [code]** C5 (a premise
  satisfiable by a junk witness) and C6 (hypothesis-set vs prose-metadata diff after
  a signature change). Distinct from the existing contradictory-premise and
  trivial-conclusion gates.

- **H11 — Telemeter the silent channels. [code]** Log which retrieval backend served
  (the embedding→loogle fall-open is silent); add telemetry keys for the ∧-advisory
  and lint repair rounds (log-only today).

- **H12 — Feasibility census at intent time. [code]** Score a route by number of
  missing primitives at the pin; emit "blocked-on-infra" instead of drafting a stub
  doomed to die at the depth gate (B3).

---

## Trim candidates

Respecting the repo's "reflect before deleting orphans" value and the deliberate
calibration keeps (`a7035b4`): these are flagged with evidence, not auto-deleted.

- **T1 — Safe residue. [remove]** `targets/smoke.*` + `runs/smoke-*` (Jul 8
  residue); the stale `"autop": null` telemetry row; retired #67/#88 in
  `targets/queue/README.md`; the cosmetically-stale local `index/PIN`.
- **T2 — Vestigial token metering. [decide]** The vibe path hardcodes `"tokens": 0`
  (`vibe_prove.py:147`) and the monthly meter reads 0 after two passing ticks — so
  `can_afford`/`monthly_allowance`/`per-issue-cap` are inert on the live path. Either
  wire real token capture from the vibe run, or remove the accounting.
- **T3 — Duplicated lint layer. [simplify]** The `gate.gate` textual lint re-checks
  content the draft-time mirror already passed; the REGEN `lake lint` is the real
  gate. Drop the middle pass.
- **T4 — Consolidate, don't delete. [refactor]** The four structural probes share
  ~80% of their meta-block construction → one builder. `autoformalize.py` (1893
  lines, ~8 jobs) → split the gate-time transforms (strengthen/trim/golf, consumed
  only by `vibe_prove.py`) into their own module.
- **T5 — Config drift. [fix]** `AutoformalizeConfig` dataclass defaults vs
  `pipeline.toml` mirror the same numbers (budget 200k vs 400k, etc.) and drift
  silently. One source of truth.
- **T6 — Stale doctrine text. [fix]** `pipeline.toml:8-10` (token-sizing prose,
  moot on the vibe path); `autoformalize.py:1814` ("an unused import is harmless"
  now contradicts `trim_unused_imports`).
- **T7 — Keep (do not trim).** `probe.py prove` CLI is a deliberate calibration
  baseline (`a7035b4`, ML4TP control-arm lesson). `eval_draft.py` / `triage.py` /
  `contribute.sh` have zero live callers but are flagged for human judgment, not
  auto-deletion.
- **Dead config flags** (document or remove): `GOLF` (no setter), `SCOUT_TACTICS` /
  `tactics.jsonl` (never built), the `retrieval_backend="loogle"` alternative, the
  hard-difficulty budget branch (unreachable — selection is capped at `medium`).

---

## Proposed `patterns.md` additions (draft content, ready to apply)

`patterns.md` lives in the main repo and is the shared authority; these are drafted
here so H2/H3 are a paste, not a re-derivation. Both are written in the register of
the existing `patterns.md`.

### Draft — new section "Statement design (for the formalizer / drafter)"

> The drafter's job is a faithful, in-depth *statement* — the hardest failures are
> not proof failures but statement failures. Design for these before writing `:= by
> sorry`.
>
> - **Shape hard side-conditions to be inherited, not asserted.** When the object is
>   a limit/closure, define it inside a class closed under the operation so the
>   condition is free: `levyClosure := (LinearMap.range emb).topologicalClosure` makes
>   `DenseRange` a soft `IsInducing.subtypeVal.dense_iff` fact — no bespoke σ-algebra.
>   Carve a `Submodule` (carrier + three closure proofs) rather than a `structure` +
>   `Module` instance. Diagonalize a matrix problem so the ODE reduces to the scalar
>   lemma. A draft that instead *asserts* the side-condition as a new hypothesis is
>   the wrong shape.
> - **Casts go outward around lattice/arith ops** to match library normal form:
>   `↑(min p t)`, not `min ↑p ↑t` — a coe-inward statement fails to unify downstream,
>   and a co-occurring "stuck metavariable" is a *symptom* of the cast mismatch, not a
>   separate problem. Fix the cast, not the instance.
> - **Name derived measures/σ-algebras** (`trimMeasure_T`, a predictable σ-algebra) as
>   defs; never inline `(P.trim …)` in a statement — the inlined form carries a
>   σ-algebra-instance mismatch the named def avoids.
> - **State Lp-class facts in `=ᵐ`/`condExp` form, not pointwise.** For a process whose
>   value is an Lp class, honest pointwise `Adapted` is awkward; the conditional-
>   expectation identity is the real content and it elaborates.
> - **State shared hypotheses in the eta-form the consumers want** (`fun ω ↦ B t ω - B s
>   ω`, not Pi-`sub`), so a defeq `exact` propagates instead of a rewrite failing.
> - **Don't quantify integrals over `↥Submodule`** — instance synthesis (`BorelSpace
>   ↥K`) fails; stay in the ambient space with subset/membership hypotheses.
> - **Natural generality** (already in the drafter contract, restated here): `s.Nonempty`
>   over a member-witness; `A ≠ 0` over provable positivity; the minimal typeclass the
>   callees need.

### Draft — new section "Repair table (compiler error → fix)"

> The recurring error→fix mappings from the grind history. Try the mapped fix before
> a general search; most are one-line and defeq-driven.
>
> | Error signature | Fix |
> |---|---|
> | "did not find an occurrence" / "made no progress" with `(fun … ↦ …) x` or a Pi-`+`/`-`/`*` of lambdas in the goal | `show` the beta-reduced goal / `simp only []`; or type-annotate the `have` with the single-lambda form. Diagnose a stuck `convert` with `exact h`. |
> | Unknown identifier `X` | grep the **pinned** `.lake/packages/mathlib` for `X` and `Namespace.X` (loogle tracks a newer pin — upper bound only); if a sibling edited this session declares `X`, it's stale-olean: rebuild, don't respell. |
> | "typeclass instance problem is stuck `C args ?m.N`" | name the implicit at the call site (`(μ := μ)`), `@`-apply, or bind a fully-typed `have` first. |
> | "Function expected at `zero_le` … type `0 ≤ ?m`" | drop the applied argument, use the bare term; toggle the primed/unprimed variant. |
> | "unexpected token 'omit'/'set_option'; expected 'lemma'" | move the `… in` modifier **above** the docstring. |
> | mass "unknown namespace MeasureTheory" in a file importing a just-edited sibling | stale olean — rebuild the dep; do not edit the proof. |
> | Type mismatch of `Measurable`/`AEStronglyMeasurable` differing only in the `MeasurableSpace` instance | drop the explicit type annotation (let it infer the sub-σ), or `letI`-pin it. |
> | "Invalid field `f`" on an `And`/`Exists`/def-reducing-to-`And` | call `Namespace.f h …` by full name, or `obtain ⟨…⟩` first. |
> | "No goals to be solved" | delete the trailing tactic (cascade after a root error — fix only the first). |
> | "Ambiguous term X" (`intervalIntegral` vs `MeasureTheory`) | qualify by the goal's integral syntax. |
> | "failed to synthesize `LE Type`/`OfNat Type`" | `ℝ≥0` misparsed as `ℝ ≥ 0` — add `open scoped NNReal`; `𝓝` → `open Topology`. |
> | cast mismatch inside a `fun n : ℕ ↦ …` | put the ℕ-consuming atom leftmost, or ascribe the binder; write `(2:ℝ) •`, never `2 •`. |

---

## Appendix — the recurring time-sinks (what to prevent)

Across all seven eras, the wasted hours clustered into a handful of preventable
shapes:

1. **whnf / nlinarith timeouts on inline mega-constants** → extract the scalar
   inequality into a minimal-context standalone lemma (constant denominators,
   sum-of-envelopes); the `maxHeartbeats` bump is a diagnostic, never a first move.
2. **Rename/overload whack-a-mole across sessions** (`abs_add→abs_add_le` ×4, the
   `zero_le` overload ×12) → the corpus rename cache (H6).
3. **Corpus-scale re-verification on the 10 GB box** (daemon OOM-respawns, multi-hour
   sweeps) → delta-only local, clustered by import-set; batch sweeps are CI/big-box
   work; flag any single entry > 10 min as a stall.
4. **The daemon death-spiral** — a transient probe timeout triggers the cold-container
   fallback, which double-loads Lean and OOM-kills the daemon; stale post-respawn
   error counts then burn repair rounds → verdict-from-log + single-Lean-process
   invariant (H5/D3).
5. **Sub-prover thrash on a "mechanical remainder"** dispatched with a design-level
   spec instead of a line-level skeleton, with no monotone-progress kill-switch →
   full line-level context + revert-to-green-baseline on rising error count.
6. **Instance-diamond loops** (the `extendOfNorm`-into-a-submodule case: ~27 min, ~10
   rebuilds) → the diagnosis ladder is now in `patterns.md`; encode it as a repair rule.
