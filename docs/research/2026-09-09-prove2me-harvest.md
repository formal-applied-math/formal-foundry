# Prove2Me harvest — what survives dropping the platform

**Status:** 2026-09-09. R declined the platform integration
([spec](../superpowers/specs/2026-09-06-prove2me-integration-design.md), now superseded).
This is the residue: what the paper offers a **private, single-library loop that
publishes nothing**.

**Source.** *Prove2Me: An Open Collaborative Platform for Scaling Math Formalization*
(Chen, Marwaha, Lu, Yuen, Peng — [arXiv 2608.28433v2](https://arxiv.org/abs/2608.28433),
31 Aug 2026), plus `references/` in
[`prove2me/prove2me_workspace`](https://github.com/prove2me/prove2me_workspace)
(`SKILL.md` v0.9.7), which is public and stays readable with the platform dropped.

Quotes are verbatim; whitespace is normalized from the PDF extraction and the LaTeX
mangling is the extractor's. Where the paper says nothing, this document says so
rather than filling the gap.

---

## The short list

Two things to build, two to know, one deadline. Nothing else transfers.

| | what | why it's here |
|---|---|---|
| **A** | require a proof-idea account with every candidate | a real gap; small; feeds the refinery's expensive half |
| **B** | the blind read-back auditor | stronger instrument than our judge — but unevaluated, see §3 |
| **C** | external validation of three shipped mechanisms | no work |
| **D** | their confound caveat validates our A/B design | one line in the 09-30 gate |
| **E** | cite it in the VeriCodeGen submission | abstract due 2026-09-11 |

---

## 1. §3.3 blind read-back — the protocol

The paper's entire treatment is one paragraph (p. 5):

> **Sub-agent read-back.** Auditing faithfulness usually requires reading Lean, which
> limits participation to formalization experts. Prove2Me lowers this barrier with a
> *sub-agent read-back* step. For each candidate statement, an independent auditor agent
> is given the Lean declaration and its dependent definitions, but not the original
> source reference. The agent translates the Lean code back into ordinary LaTeX
> mathematics, making binders and hypotheses explicit and unfolding non-standard
> definitions where needed. The human auditor then compares two mathematical statements:
> the source LaTeX statement and the agent's read-back from Lean. Faithfulness is judged
> by whether these two statements match, rather than by requiring the auditor to inspect
> Lean directly. Garg [2026] apply a related back-translation check.

The Figure 3 caption (p. 6) is the only worked example:

> A read-back of a drafted statement. The auditor agent sees only the Lean code (top) and
> renders what it literally asserts (bottom), unfolding the two problem-specific
> definitions and accounting for every binder and hypothesis, including those a source
> statement would leave implicit: that the two finite sets are quantified independently,
> that an empty product is 1, and that the hypothesis is only n < M. The human auditor
> compares this testimony against the source statement.

Answering the questions that decide whether we can build it:

- **Shown:** the Lean declaration **and its dependent definitions** — not the statement
  alone.
- **Denied:** "the original source reference". Nothing else is named.
- **Output:** a back-translation into natural-language/LaTeX mathematics. Not a verdict,
  not a structured schema. Testimony about the artifact.
- **Comparison:** **human**, explicitly, in both the paragraph and the caption. §3.3 adds
  that the captain "cannot delegate the audit: the human is required to click each
  statement individually to confirm it, ensuring none enters the audited core unread."
- **Prompt, rubric, output schema in the paper:** none. There is no appendix prompt.

**The rubric exists, but in the harness repo** — `references/mission_auditor.md`. Its
seven principles, verbatim, are the adoptable artifact:

> 1. **Translate the code, not the intent.** State only what the Lean statement actually
>    says. Never import context from the informal description, the mission, or your own
>    understanding of what the author "meant". If the code says less than the intent,
>    your read-back must say less.
> 2. **Account for every binder and hypothesis.** Every universally or existentially
>    quantified variable, every explicit and implicit argument, every typeclass
>    assumption must appear in the read-back. Omitting a hypothesis is the worst failure
>    mode.
> 3. **Expand non-standard definitions.** If the statement refers to definitions from
>    this bundle (or anything that is not a well-known notion), unfold what they mean
>    inline. A read-back that says "the inner product" when the code uses a custom
>    `demo_innerProduct` has hidden exactly what the auditor needs to see.
> 4. **Surface degenerate and edge cases.** Make explicit what the quantifiers silently
>    include: n = 0, empty sets, junk values from total functions (division by zero,
>    `Nat` subtraction), vacuously satisfiable hypotheses. If a hypothesis could be
>    impossible to satisfy, say so — a vacuous theorem is the classic faithfulness trap.
> 5. **Preserve logical precision.** Keep the exact strength of every connective: ≤ vs <,
>    ∃ vs ∃!, iff vs implication, the precise direction of every inequality and
>    inclusion. Do not round to the "morally equivalent" claim.
> 6. **Write for a mathematician who does not read Lean.** Plain mathematical English,
>    standard notation where it helps. Try not to mention Lean syntax.
> 7. **No judgment, no advocacy.** Do not assess whether the formalization is correct,
>    faithful, or well-designed, and do not defend it. Discrepancies are for the human
>    auditor to find by comparing your read-back with the stated intent.

Three operational rules from the same file:

> Never give the auditor the informal statement, the source material, the mission pitch,
> or your own intent. An auditor who knows what the code is "supposed to say" will read
> that meaning into it — and the discrepancies the human needs to see disappear.

> do not write your own read-backs — launch an **independent sub-agent** with a fresh
> context

> Re-run the auditor after any edit to the Lean statement: a read-back of an older
> version of the code is worse than none, because it testifies about the wrong artifact.

**One discrepancy to resolve in our favour.** The paper says the auditor gets "the Lean
declaration and its dependent definitions"; the harness file says the `formal_statement`
plus "the `preamble` it depends on". Principle 3 only works on the paper's reading — give
it the defs.

**What this is not.** It never emits faithful/unfaithful. Our judge returns
`{"faithful": bool, "verdict", "issues"}`; a read-back returns prose and stops. Making it
*gate* rather than inform means designing the comparison step ourselves — the paper gives
nothing for it, because theirs is a human clicking a button.

**Why it is still stronger than what we have.** `domains/mathfin/prompts/judge-system.md`
is handed the issue prose and the candidate Lean *together* and asked whether they agree.
That is anchored by construction: the judge can read the intended meaning into the code.
A read-back auditor cannot, because it has never seen the intent. That is the whole
mechanism, and it is aimed squarely at the defect class our gates demonstrably miss —
the **Class B** over-assumption of `e3e0d25` (#161, #162), where the proof genuinely
consumes a guard the theorem does not need, so no unused-binder warning and no deletion
probe fires. Class A (#66, #85) is caught by a warning pass and is not an argument for
this.

## 2. The ~43% figure — what the paper actually claims

One clause, p. 3, verbatim:

> Because automated faithfulness checks remain imperfect (a recent Lean-as-judge audit
> finds only about 43% of proved statements faithful [Bourigault et al., 2026]), a common
> and effective mitigation is to shrink the human audit surface, reviewing only the
> definitions and top-level statements rather than the proofs

That is all of it. **The paper does not state the population, the task, or the
unfaithfulness criterion.** It is a secondhand citation to:

> Pauline Bourigault, Xiaotong Ji, Matthieu Zimmer, Rasul Tutunov, and Haitham Bou Ammar.
> Risk-controlled lean-as-judge for natural-language mathematical reasoning. arXiv
> preprint arXiv:2605.28365, 2026.

**Do not use this number to justify the work without reading the source.** Prove2Me's
phrasing — "proved statements" — reads like formalized statements, but the cited title is
about *natural-language mathematical reasoning*, which is not obviously the same
population, and "risk-controlled" suggests a selective-prediction setup in which 43% could
be a coverage figure rather than a base rate. Neither reading is established by the paper
in hand. The real population and criterion are in 2605.28365.

## 3. Does the paper evaluate the auditor? No.

**It is a protocol with no evaluation.** No catch rate, no false-positive rate, no
agreement-with-human number, no ablation, no count of statements it rejected. Figure 3 is
one illustrative read-back, not a result.

Section 5 is the only empirical section and it measures something else: Table 1 reports
LOC, cost, agent count, models and days for four completed missions plus a comparison row
from Gloeckle et al. The authors disclaim it themselves:

> Table 1 reports case studies, not a controlled experiment. The corpora, their
> difficulty, the working patterns, and the model generations differ across rows, and the
> two cost conventions are not comparable.

So adopting the read-back is adopting **an argument, not a measurement**. The argument is
sound — blind back-translation removes an anchoring our judge is exposed to — but the
evidence would be ours to generate. Garg [2026] ([arXiv 2606.13306](https://arxiv.org/abs/2606.13306))
is credited with "a related back-translation check" and is the other place to look for
anyone's numbers.

## 4. Item A — require a proof-idea account with every candidate

§3.2, verbatim:

> Alongside the proof file, agents are required to upload a detailed natural-language
> explanation of the proof idea, which is linked to the proof for the benefit of human
> readers and other agents.

and the conclusion:

> Agents are also required to write a detailed natural-language account of every statement
> and proof they submit, so that what they produce stays readable and legible to people.

**This is a live gap here.** `grep -rn 'explanation\|proof_idea' probe/*.py` returns
nothing — no stage emits a prose account of a candidate. `refinery_notes.py` covers the
mechanical half and says so in its own docstring: it "deliberately does NOT touch the
taste half (inspired math, architecture, statement faithfulness), which stays
human/Claude." So the human refiner opens a PR holding a compiling candidate, a mechanical
punch list, and **no statement of why the proof works**. That is the input the expensive
half of the pass lacks, and `refinery_minutes` is the field measuring what its absence
costs.

The harness's rubric (`references/prove.md`) is specific and directly usable:

> - Open with what you proved: the statement in a display-math block, and the hypotheses
>   your argument actually uses.
> - Give the proof idea in a short paragraph before any details: the one observation that
>   makes the argument work, and the technique it belongs to.
> - Then give the argument in steps, each saying what is being shown and why it follows.
> - No commentary about yourself, the platform, or how many attempts this took, and no
>   "elegant" / "clever trick" adjectives.

Note "the hypotheses your argument actually uses": prose written by the prover, naming
its own load-bearing hypotheses, is a free cross-check against `strengthen.py`'s Class-B
finding. The last rule is `prose_slop.py`'s job description, already built.

## 5. Items C–E — no work, and one clock

**C. External validation of three shipped mechanisms.** Independently arrived at in the
paper: statement/proof type-match verification (their Curry–Howard check = our
`gate.gate(statement=…)` / `_probed_signature` pin); disproof as a misformalization
detector (§3.2's `¬(target statement)` submission = our ⊢¬Concl probe); and
search-before-submit dedup (§4.3: "reuse an existing theorem where one exists, and
introduce a new statement only when none does" = backlog item T, built 2026-07-31).

**D. Their confound caveat validates our A/B design.** §5:

> Two explanations are confounded in every row: a stronger model generation, and a harness
> built for multi-agent proving. The case study data alone cannot separate them; doing so
> requires holding the model fixed and varying only the harness, which we leave to future
> work.

That is exactly what `ab-decomposer.md` does — "Both arms are Mistral." Worth a line in
the 09-30 gate write-up: the design choice we already made is the one this paper names as
the thing it could not do.

**E. Cite it — VeriCodeGen abstract is 2026-09-11.** On-point related work for that
spec's §1 claim that the standard stack is sound about proofs and silent about statements.
Their §3.3 opening is nearly our thesis:

> an AI-generated statement can be vacuous, miss a hypothesis, or drift semantically, yet
> still be "proved"

and Table 1 is a cost/scale reference class for project-scale Lean development, citable
honestly because the authors attach the caveat themselves.

## 6. What does not transfer, and why

Milestones, proof-sketches, Formalpedia, the discussion channel, reputation, immutability
— each is a consequence of decentralization and publication, and a private single-library
loop gets nothing from any of them.

The proof-sketch is the one that looks transferable and is not. §4.2's Property 1 —

> Theorem 4.1 is verified **if** all imported child lemmas are verified.

— is the guarantee our skeleton gate already establishes when it checks that the assembly
elaborates with `sorry_count == n_leaves`. We built that in July. What the platform adds
on top of it is other people's compute, which is the part R declined.
