# Design — VeriCodeGen 2026 submission + Lean Refactor Arena entry

**Status:** proposed, 2026-08-17. **Owner:** R.

**Clocks.** VeriCodeGen (NeurIPS 2026 workshop, *AI for Verifiable Coding*): abstract
**2026-09-11**, paper **2026-09-13**, notification 09-29, camera-ready 10-14. Non-archival,
double-blind, 4–8 pages of main text plus unlimited appendix, arXiv preprints permitted,
and the CFP states that *work in progress and negative results are encouraged*. Lean
Refactor Arena (the workshop's competition track): warm-up now, full benchmark
**2026-10-01**, submission **2026-11-08**, 3–9 page tech report. The two do not contend.

**Evidence policy for this submission (R, 2026-08-17): key-free only.** No new pipeline
ticks, no Leanstral calls, no Claude judge re-runs. `MISTRAL_API_KEY` is empty in
`~/.vibe/.env` and there is no `CLAUDE_CODE_OAUTH_TOKEN`; the whole experiment below is
daemon-time and costs zero tokens by construction. This is a constraint on scale, not on
the claim — see §3.

---

## 1. The claim

The field's working definition of an accepted machine contribution is: it elaborates,
carries no `sorry`, its axioms stay inside `{propext, Classical.choice, Quot.sound}`, and
an LLM judge agrees the statement matches the request. This repo has run that stack
against a live queue for two months, and the claim of the paper is that **the stack is
sound about proofs and silent about statements** — it certifies that *something* was
proved without certifying that the something was what should have been proved.

We have a crisp instance already: on the four autoformalized theorems merged into
`formal-mathfin`, **4 of 4 asserted a hypothesis the theorem does not need**, and every
gate passed them. But the four are not one phenomenon, and collapsing them would put the
paper's weight on the wrong number. They split cleanly into two defect classes:

| | theorem | the hypothesis | how the proof treats it | what catches it |
|---|---|---|---|---|
| **A** | `payer_swap_value_eq_zero_iff…` (#66) | `hTn : T_n ∈ s` | never referenced | unused-variable warning |
| **A** | `premium_ge_mean` (#85) | `hσ_eq : σ = √σ2` | never referenced | unused-variable warning |
| **B** | `gainToPain_nonneg` (#161) | `0 < ∑ r⁻` | consumed, `have hden := h.le` | **nothing — must re-prove** |
| **B** | `upCapture_smul` (#162) | `∑ b ≠ 0` | consumed, `field_simp [h]` | **nothing — must re-prove** |

Class **A** is caught by a cheap syntactic pass, and the sequence is provable from the
repository's own history: both were drafted on 2026-07-17 (19:19:00 and 21:49:17, from the
candidate filenames in `runs/`), and `strengthen_candidate` — the warning-driven pass that
drops hypotheses the proof never used — landed the same evening at 22:51:44 in `a5fc423`.
The drafts predate their own remedy by hours. Worth stating plainly *why* they reached
merge: `probe_lib.lint_violations` implements only `defsWithUnderscore` and `docBlame`, and
only over definitions — it never inspects theorem binders — so the foundry's own lint gate
could not have flagged them, and the downstream repository's `lake lint` was the backstop.

Class **B** is the paper's actual claim, and it is n=2. No gate in the standard stack draws
the distinction it needs — **used by this proof** versus **needed by this theorem** — because
the proof genuinely consumes the guard, so no warning fires and deleting the binder simply
breaks the proof. Drawing that distinction requires re-proving without the hypothesis,
which is the instrument §2 builds.

Two things this framing buys. It survives a reviewer with the artifact in hand: someone who
opens `runs/pipeline-20260717-191900-cal-bk-66.candidate` sees `hTn` sitting unused in the
binder list within a minute, and a paper that had folded it into the headline would lose the
headline to "just run the linter". And it puts the contribution in the right tense — the
field's standard stack misses class B; we found it in our own output, built the prober, and
wired it into production (`probe/vibe_prove.py:267` for class A, `:296` for class B). Both
classes are closed today. The paper reports a gap it has already fixed, which is a stronger
position than one it merely suffers.

Two of two is an anecdote. §2 turns it into a measurement.

## 2. The experiment — a certified lower bound on unnecessary hypotheses

**Instrument.** `probe/strengthen.py`, already built and traced (backlog item R), driven
standalone. It takes injected `check_fn` / `prove_fn`, so it runs against
`probe.daemon_check` (the lean-repl daemon on 127.0.0.1:7878, already serving) with no
pipeline around it. Per theorem, per explicit hypothesis:

1. **Free filter** — drop the binder, elaborate the statement alone with `sorry`. A binder
   the rest of the signature depends on (`{ι : Type*}` under `(s : Finset ι)`) dies here
   at zero further cost.
2. **Re-prove** — a fixed 8-tactic sweep (`positivity` → `simp [defs]` →
   `unfold; positivity` → `simp [defs, ← Finset.mul_sum, mul_div_assoc]` →
   `field_simp; ring` → `grind`) on what survives, against the daemon. Zero tokens.
3. **Verdict** — a hypothesis whose removal leaves a statement the sweep closes is
   **certified unnecessary**, and we hold the stronger theorem *with its proof in hand*.

**Population: the library, not the catalogue.** The first two drafts of this section
measured `formal-mathfin/benchmarks/*.json` — 357 catalogued entries, 700 probe-worthy
binders. That population was abandoned on 2026-09-01 for a reason that would have
invalidated the result: **330 of its 332 `full` entries (99.4%) are term-mode
re-exports**, `:= MathFin.brownian_markov_property hXpb hX t₀`, whose real proof lives in
the library. A tactic sweep cannot reprove a research theorem from scratch, so the power
control fails on essentially all of them and the blind fraction is 100% *by construction*
— a measurement of the re-export layer, not of the mathematics. The stratification below
was written to defend against exactly this and keyed on `formalization_status`, which
labels 14 entries `library_wrapper`; the structural rate is 99.4%. The defence did not
separate what it was built to separate.

The instrument is therefore pointed at `formal-mathfin/MathFin/**/*.lean`, where the
proofs and the hypotheses are, and where both motivating cases actually live —
`gainToPain_nonneg` (#161) and `upCapture_smul` (#162) are library lemmas, not catalogue
rows. Measured in parse-only mode:

| | declarations | explicit binders | probe-worthy | across |
|---|---|---|---|---|
| `tactic_short` (proof ≤ 10 lines) | **615** | 2,120 | **609** | 343 decls |
| `tactic_long` | 745 | 3,480 | 1,729 | 581 decls |
| `term` | 186 | 687 | 237 | 99 decls |
| **total** | **1,546** | 6,287 | 2,575 | 1,023 decls |

Locatable by the sweep's own parser: **1,546 / 1,546**. That is enforced rather than
observed — the loader refuses to emit a declaration it cannot locate, because an
unlocatable one produces no probe, fails its power control, and is recorded as *blind*.
An earlier draft leaked 278 such declarations (272 `private`, which the shared parser's
regex does not accept, and 6 conjured out of prose in doc comments — `theorem for ±1
walks` yielding a declaration named `for`). That is a 15% inflation of the paper's
headline denominator arriving as a finding rather than as a bug, and it is the
characteristic failure mode of this study: **instrument breakage is indistinguishable
from a result unless something is built to tell them apart.**

The headline stratum is `tactic_short`: 609 binders over 343 declarations. The sweep's
power varies sharply with proof length, so the strata are reported separately and never
pooled.

The pre-filter is the cost lever and it is sound: if a binder's name occurs free in the
rest of the signature or in the conclusion, dropping it *cannot* elaborate, so the daemon
call is a certain failure and skipping it removes no possible positive. On the short
stratum it cuts 2,120 explicit binders to 609 (29%). Most of what it removes are data
binders (`r`, `δ`, `K`, `μ`, `σ`) rather than hypotheses.

Every run records the corpus commit it swept, and the paper quotes that row rather than
this one.

**Stratify by faithfulness status, or the result is an artifact of wrappers.** Grounding
the design against the entries turned this up: many carry a thin proof that just applies
the underlying result (`bs_put_formula ... := MathFin.bs_put_formula h`). Dropping such a
binder is unprovable by construction — not because the mathematics needs it, but because
the *wrapper* does, relative to a lemma we did not probe. Pooling those with real proofs
would depress the rate for a reason that has nothing to do with statement quality. So the
sweep runs over all 282 hypothesis-bearing entries but reports stratified by the existing
`full` / `library_wrapper` / `reduced_core` status, and the headline rate is computed on
`full` only. Wrappers are reported separately as what they are: a measurement of the
wrapper layer, not of the mathematics.

**The asymmetry, which is the methodological spine.** Every positive is kernel-certified:
the reduced statement was proved. No negative is evidence of anything — a hypothesis the
sweep cannot remove may still be unnecessary, since the sweep is eight fixed tactics and
not a prover. So what we report is a **lower bound**, and it must be labelled as one in
the abstract, not just in a limitations paragraph. A reviewer who reads it as a two-sided
estimate has been misled by us, not by the instrument.

**Non-uniform power, and the control that handles it.** The sweep is algebra-shaped. It
will find far more in `mathematical_finance` than in `stochastic_calculus`, where the
hypotheses guard measurability and integrability and no fixed tactic list will discharge
them. Reporting one pooled rate would therefore be close to meaningless.

The control (sharpened while writing the plan, and stronger than the "reach" fraction this
spec first proposed): for each theorem, **first ask the sweep to prove it with every
hypothesis present**. If it cannot, the instrument is blind on that theorem, and its
failure on a hypothesis-reduced version says nothing about the hypothesis. Those theorems
leave the rate's denominator entirely and are reported beside every rate as the **blind
fraction**, so a low rate can never be read as a clean library when it is really
instrument blindness. Cost is one extra daemon call per theorem. Rates are reported
per-domain and per-arm; both arms use the same instrument, so the comparison survives the
bias even where the absolute level does not.

**Outputs.** A per-hypothesis JSONL under `runs/necessity-sweep/`, a per-domain table, and
for every positive a diff that strengthens the library. Committed as telemetry, in the
repo's existing style.

### 2.1 Two corrections that grounding forced

**The merged corpus cannot reproduce 4/4, and expecting it to was an error in the first
draft of this spec.** The four autoformalized entries in the corpus are the *post-refinery*
state: `mf_performance_gain_to_pain` carries no hypothesis at all today, because the review
that merged it stripped the spurious one. What the corpus measures is therefore the
**residual** rate — what survives human review and the strengthen passes — not the
drafter's output. That is still worth measuring, and it is arguably the more interesting
number, but it is a different number and the paper must not conflate them.

**The refinery already recorded the defects, in machine-readable form.** Every autoform
entry carries `metadata.provenance.refined`, a prose record of what review changed about
the machine's statement — e.g. *"spurious `0 < pain` hypothesis dropped (the drafter
guarded a division Lean does not need guarding — x/0 = 0)"* and *"spurious `∑ b ≠ 0`
hypothesis dropped (mul_div_assoc needs no nonvanishing denominator)"*. This is ground
truth about what the gate stack passed and a human caught, written at merge time rather
than reconstructed for the paper. Five entries carry such a record; four are the merged
autoform theorems of §1, and those four split two-and-two across its defect classes. The
`refined` prose is what makes the split checkable — it says "unused hypotheses ... removed"
for class A and "spurious ... hypothesis dropped" for class B, in the reviewer's own words
at merge time. It is evidence rather than inference, at n=2 for the claim that needs it.

So the paper measures **two populations, kept separate**: the drafts (pre-review, from
`runs/*.candidate` and the `refined` records) for what the gates miss, and the shipped
corpus (post-review) for the residual base rate.

### 2.2 The second arm is Mathlib, not a sibling library

The first draft assumed `formal-econometrics` / `formal-macroeconomics` could supply a
cross-library arm. Measured: they hold 1 catalogued entry and 5 Lean files between them.
They cannot. The arm that actually strengthens the paper is **Mathlib**, sampled — the
most heavily reviewed Lean corpus in existence, and the one where a nonzero rate of
removable hypotheses is a result people will care about independently of anything we built.
Same instrument, same lower-bound semantics, no new machinery. Sampling size is set by the
measured daemon latency (step 1 of the plan), not chosen in advance.

**Dropped 2026-09-01, on the measured cost it was made conditional on.** The rule was: take
the largest of {1000, 500, 250, 100} whose projection is ≤ 10 h, else drop the arm. Measured
per-entry cost after both optimisations is 192.8 s (`runs/necessity-sweep/latency.md`), so
even n=100 is 5.4 h — and that sits *on top* of the MathFin arm's 7.2 h, on a box that runs
one Lean process at a time and is shared with an active development session. The extraction
is also weaker than the arithmetic suggests: `load_mathlib_entries` wraps each declaration
with an import of its own module but not the surrounding `namespace`/`open` context, so a
sizable share would fail to elaborate for reasons that have nothing to do with hypothesis
necessity, inflating the blind fraction on the very arm meant to be the credible one.

The paper says the arm was dropped and why. It does not quietly become a single-library
study — a cross-library claim withdrawn on measured cost is a different thing from one
never attempted, and the difference belongs in the text.

## 3. Honest limitations, stated up front

- **One library, one domain, one curated queue.** Mathematical finance, one author's
  issue backlog. We do not claim the rate generalizes; we claim the *failure mode* does,
  and that the instrument transfers to any Lean development.
- **n=2 on the machine arm, not four.** Four merged autoform theorems each carried an
  unnecessary hypothesis, but only two are the class no gate catches (§1, class B); the
  other two are plainly-unused binders a warning-driven pass handles, and the pass that
  handles them landed hours after those drafts were written. We report two, refuse a
  p-value, and state it as "two of two, against a residual base rate of X%". If X is high
  the finding is *weaker* and we say so.
- **The two populations measure different things** (§2.1) and the paper says so in the
  results section, not only in limitations.
- **Lower bound, per §2.**
- **The pipeline's own numbers are small**: 40 refill rows, 22 live obstructions, 16
  stored drafts. These support the supporting sections, not the headline.

## 4. Supporting sections (already recorded, no new runs)

- **The funnel dies at the drafter, not the prover.** `runs/obstructions-report.md` and
  `runs/refill-history.jsonl`: 40 rows, and every death at `depth`/`intent`/`formalize`/
  `unfaithful`. Not one reached the prover. Sharpened by Experiment Zero's Control B —
  issue #53 was recorded `needs_primitives` across four ticks, then closed by hand in one
  session with a ~6-line proof. A **false infeasibility verdict**, caused by inability to
  state rather than to prove.
- **The depth gate** — require the statement's *type* to consume a definition from the
  issue's cited modules, or it is a Mathlib identity in domain clothing (cal-bk-67 inlined
  a forward rate over raw reals rather than consuming `MathFin.zcb`). One `run_cmd` meta
  check; the cheapest specification guard we have.
- **A gate we retired on its own evidence** — the intent-fidelity gate, killed after
  **0/62 drift firings** and a clean off-arm. The CFP asks for negative results; this is
  one, and it is the section that shows the apparatus is willing to lose.

## 5. Paper structure (target 8pp)

1. The accept-criteria stack, and what it is silent about (~1p)
2. Setting: the pipeline, compressed — it is context, not the contribution (~1p)
3. The instrument: drop-and-re-prove, and why the unused-variable pass misses (~1p)
4. **Results**: per-domain rates, both arms, with reach (~2p)
5. The funnel, the depth gate, the retired gate (~1.5p)
6. Limitations and threats to validity (~0.75p)
7. Related work: autoformalization faithfulness, spec quality, Lean tooling (~0.75p)

**Deliverables and where they live.** The paper source as a single self-contained
`.tex` at `docs/superpowers/papers/vericodegen-necessity.tex`, built against the
workshop's `neurips_2026_vericode_workshop.tex` / `neurips_2026_vericode.sty` (Overleaf
template), matching the convention the three arXiv papers already follow. The sweep
driver at `probe/necessity_sweep.py` with tests beside the other 36 test modules; its
output under `runs/necessity-sweep/`. Submission through OpenReview
(`NeurIPS.cc/2026/Workshop/VERICODEGEN`).

## 6. Anonymization

Double-blind against three arXiv papers and public named repos. Plan: self-citation in the
third person throughout ("the library of Coelho" → "a Lean 4 mathematical-finance
library [N]"), no repo URLs in the body, artifact linked as an anonymized mirror, and the
Zenodo DOI held for camera-ready. The library is identifiable by anyone who searches for
it — that is normal for artifact-bearing submissions and is not a violation, but nothing
in our text should do the identifying.

## 7. Track 2 — Lean Refactor Arena prep

**Unverified:** `leanrefactor.github.io` renders client-side and returned nothing to
WebFetch; everything below is from the workshop page and must be confirmed against the
arena's own rules before we build to it.

Task as described: rewrite existing Lean 4 proofs to be shorter, faster to elaborate, and
portable across toolchain versions; two tracks (closed-source API under a per-problem
budget, open-source under a compute budget). That is what the refinery already does, and
`docs/patterns.md` (1,473 lines) is a written refactoring policy.

**Two lanes, discovered by search after the first draft of this spec.** The arena is
actively soliciting proofs for the benchmark — it wants long proofs with room to shorten,
expensive to compile, and stable across toolchains, which is a precise description of the
Ito tower (62 modules, ~20,000 lines, pinned and re-elaborated in CI on every commit).
Donating is cheap, independent of competing, and lands our library in a benchmark others
will run against. That is **Lane A**, and it is the higher expected value of the two.
**Lane B** is competing, and its bar is a published method: Lean Refactor
(arXiv:2605.20244) reports >70% token compression and up to 60% compile-time reduction.
We enter only on measured evidence that the refinery is in that range.

Prep before 10-01, in order:
1. Confirm the rules and register for the warm-up.
2. Build a measurement harness over our own corpus for the three metrics — proof length,
   elaboration time, and cross-toolchain survival — as a practice set with known answers.
3. Baseline: run the refinery loop over a sample of our own machine-generated candidates
   and record the deltas. This tells us whether we have a competitive method before we
   spend October on it.

Gate: if the baseline shows no reliable improvement on our own proofs, we do not enter.

## 8. Not doing

- No new pipeline ticks, no token spend (R, 2026-08-17).
- Not rewriting `docs/autoformalization-pipeline.tex` into the paper. That document is
  architecture; this paper is a measurement, and §2 there compresses to one page here.
- No claim that decomposition helps. `runs/ab-decomposer.jsonl` has zero rows, and the
  scoreboard says so.
