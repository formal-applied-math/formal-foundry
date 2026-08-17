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

We have one crisp instance already: on the four autoformalized theorems merged into
`formal-mathfin`, **4 of 4 asserted a hypothesis the theorem does not need**
(`0 < ∑ r⁻` on gain-to-pain nonnegativity; `∑ b ≠ 0` on upside-capture homogeneity).
Every gate passed them. The elaborator's unused-variable pass does not fire, because both
proofs genuinely *consume* the guard (`have hden := h.le`, `field_simp [h]`). The
distinction no gate in the stack draws is **used by this proof** versus **needed by this
theorem**, and drawing it requires re-proving without the hypothesis.

Four of four is an anecdote. §2 turns it into a measurement.

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

**Population.** The 358 catalogued entries in `formal-mathfin/benchmarks/*.json`, which
carry provenance mechanically in `metadata.provenance.source`:

| arm | n | what it is |
|---|---|---|
| human-authored | 352 | hand-written, and reviewed under the repo's 8-lens values panel and idiomatic sweeps |
| machine (`leanstral-autoform`) | 4 | the merged pipeline output |
| translated (`afp-actuarial-mathematics`) | 2 | re-formalized from an external development |

The human arm is the point. It is a **strong** baseline — not naive code, but a mature
library that has been reviewed for statement quality on a CI-enforced cadence — and to our
knowledge the rate at which such a library carries hypotheses its theorems do not need has
never been measured. That base rate is what makes 4/4 interpretable, and it is a
contribution on its own whichever way it lands.

**The asymmetry, which is the methodological spine.** Every positive is kernel-certified:
the reduced statement was proved. No negative is evidence of anything — a hypothesis the
sweep cannot remove may still be unnecessary, since the sweep is eight fixed tactics and
not a prover. So what we report is a **lower bound**, and it must be labelled as one in
the abstract, not just in a limitations paragraph. A reviewer who reads it as a two-sided
estimate has been misled by us, not by the instrument.

**Non-uniform power, and how we report around it.** The sweep is algebra-shaped. It will
find far more in `mathematical_finance` than in `stochastic_calculus`, where the
hypotheses guard measurability and integrability and no fixed tactic list will discharge
them. Reporting one pooled rate would therefore be close to meaningless. We report
**per-domain**, and alongside each rate the sweep's **reach** on that domain — the fraction
of probes it closed at all — so the reader can separate "few unnecessary hypotheses here"
from "the instrument is blind here". Both arms are measured with the same instrument, so
the *comparison* survives the bias even where the absolute level does not.

**Outputs.** A per-hypothesis JSONL under `runs/necessity-sweep/`, a per-domain table, and
for every positive a diff that strengthens the library. Committed as telemetry, in the
repo's existing style.

## 3. Honest limitations, stated up front

- **One library, one domain, one curated queue.** Mathematical finance, one author's
  issue backlog. We do not claim the rate generalizes; we claim the *failure mode* does,
  and that the instrument transfers to any Lean development.
- **n=4 on the machine arm.** The comparison is a 352-entry base rate against four
  observations. We report it as such and refuse a p-value; the honest statement is "all
  four, against a base rate of X%", and if X is high the finding is *weaker* and we say so.
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
