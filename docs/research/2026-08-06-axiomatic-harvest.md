# Axiomatic AI harvest — the frontier-agentic prover, and the memory we were missing (2026-08-06)

Prompted by [axiomatic-ai.com](https://axiomatic-ai.com/). The marketing site and
arXiv were both unreachable from the harvest session (egress policy), so **the
primary source here is their published code**, which is the better source anyway:
[`Axiomatic-AI/ax-prover-base`](https://github.com/Axiomatic-AI/ax-prover-base)
(the agent), [`ax-prover-base-mcp`](https://github.com/Axiomatic-AI/ax-prover-base-mcp)
(the hosted Lean MCP), plus the Ax-Prover paper's abstract/metadata
([arXiv:2510.12787](https://arxiv.org/abs/2510.12787)) via search snippets.

Everything below marked **[code]** was read directly out of their repository.
Claims marked **[vendor]** are their own README/paper numbers — self-reported,
no third-party replication, and Putnam-class benchmarks are not our queue. Treat
the numbers as a direction, not a measurement.

This closes one of the 07-11 survey's open questions ("closed-startup loops —
Axiom … no claims survived verification"): Axiomatic's loop is not closed.

## Who they are

$18M seed (Mar 2026), "Axiomatic Intelligence" — frontier LLMs plus formal and
physics-based verification, sold into semiconductor / photonics / fabless design.
Products: **Lemma** (equation derivation and validation) and **Ax-Prover** (Lean).
The commercial wedge is not proving mathematics; it is verified derivations for
engineering. Our shape in another vertical, and worth noting that the model they
sell is exactly scout-not-author: machine drafts, formal system certifies, human
signs.

## The architecture is ours

**[code]** A four-node LangGraph loop — Proposer → Builder → Reviewer → Memory,
repeating until approval, max iterations, or timeout:

| Their node | Ours |
|---|---|
| Proposer (ReAct agent, Lean tools) | the vibe ⇄ lean-lsp session (`vibe_prove.run`) |
| Builder (`lake env lean`, goal state at `sorry`) | `daemon_check` / the gate phase |
| Reviewer (statement preserved, no `sorry`, no cheating tactics) | `gate.gate` + the item-J statement pin |
| **Memory (`ExperienceProcessor`)** | **nothing — this was the gap** |

**[code]** Their Lean tools are called over MCP and are named
`lean_diagnostic_messages`, `lean_goal`, `lean_leansearch`, `lean_loogle`,
`edit_file` — i.e. **lean-lsp-mcp's exact tool surface**. See the correction to
the 07-11 survey below.

**[code]** `configs/default.yaml` in full:

```yaml
prover:
  prover_llm: ${llm_configs.claude_opus_4_5}
  proposer_tools: {search_lean: …, search_web: …}
  max_iterations: 50
  memory_config: {class_name: ExperienceProcessor, …}
runtime:
  max_tool_calling_iterations: 1
  lean: {max_concurrent_builds: 12}
```

Two details worth reading twice. `max_tool_calling_iterations: 1` — a *tight*
tool budget per node; they buy iterations, not tool sprawl. And
`max_concurrent_builds: 12` — their parallelism lives in **builds**, not in
sampling. That is item D (the CI verify pool) confirmed from the outside, and it
is the one thing our one-Lean-process memory doctrine structurally cannot do on
the local box.

## The numbers

**[vendor]** All at Claude Opus 4.5, 50 iterations, **pass@1**:

| Benchmark | ax-prover-base | Best comparable |
|---|---|---|
| PutnamBench | 54.7% | 13.0% (Goedel-V2, pass@**184**) |
| FATE-M | 98.0% | 62.7% (DeepSeek-V2, pass@64) |
| FATE-H | 66.0% | 3.0% (DeepSeek-V2) |
| FATE-X | 24.0% | 0.0% (all others) |
| LeanCat | 59.0% | 14.0% (Gemini 3 Pro) |

If these hold, they are a second independent confirmation of the Nexus ablation
behind item I — **one slot further down the pipe than we took it**. We put Claude
in DRAFT and kept Leanstral in PROVE on the explicit finding that the prover is
not our bottleneck (obstruction census: depth-gate 6 + no-elaborating-draft 5,
prover-max-rounds 0). That finding was about *our queue*, and it is still the
only evidence that is actually about us. What changes is the prior: a general
frontier model in a plain agentic loop scoring ~4× a specialist at ~1/180th the
sampling budget makes "the prover is not the bottleneck" a claim worth re-testing
rather than a settled one.

The sharper version of the argument is their *new* benchmarks. **[vendor]** They
built AbstractAlgebra and QuantumTheorems datasets precisely because the public
ones are saturated, and that is where they report the largest margin over
specialist provers — because specialists are trained on the competition-math
distribution. **MathFin is exactly that off-distribution case.** Measure theory,
stochastic calculus, and Degenne-consuming corpus reuse are about as far from
Putnam as Lean gets, and that is the regime where Leanstral's training is
thinnest. This is an argument about *where* to re-test, not a result.

## What we took: the rolling notebook (item K, LANDED)

**[code]** `ExperienceProcessor` is the one mechanism in their loop with no
counterpart in ours. Each iteration it calls an LLM with (previous summary +
this attempt's reasoning and code + build/reviewer feedback) and takes the reply
as the *replacement* summary, injected next iteration as:

```
What follows is additional context with relevant lessons learned from your
previous attempts at proving this theorem.
<experience> … </experience>
```

Three properties do the work, and we kept all three in `probe/experience.py`:

1. **Rolling, not appended.** The summariser sees the prior notebook and returns
   the new one. Their system prompt says the summary must ensure information from
   the previous context "is never lost" — the notebook is the only copy.
2. **Bounded.** It replaces transcript rather than growing beside it, so 50
   iterations do not crowd out the premises. Ours caps at 4000 chars.
3. **It is attached to the failure path**, so it must never *be* a failure. Ours
   fails open at every step: no key, a raising model, an empty reply, or a corrupt
   store all degrade to a deterministic mechanical digest, and a dead store
   degrades to no memory at all.

**Where ours differs, deliberately.** Theirs rolls *within* one 50-iteration
episode. Our prove path is a single headless vibe session with `max_turns`, and
the repetition we actually pay for is **across ticks** — the cron retries a
target and `runs/*-summary.jsonl` records that attempt 3 died `fail_gate` while
nothing carries *what was tried* into attempt 4. So ours is keyed by target and
folded at the gate phase, on failures only. That is item K's first half; its
second half (Nexus's rotating diversity instruction) rides along in `render()`
as a deterministic rotation rather than a sample, so it is reproducible in tests.

Off by default; `[autoformalize].experience = true` in `pipeline.toml` turns it
on to **measure**. The bar is the same one the state cache is held to: read it
with `python3 vibe_prove.py experience`, and if `retried targets` stays 0 then no
attempt has ever read a notebook, the memory is write-only, and it comes back out.

## What we did NOT take

- **Their hosted MCP.** **[code]** `ax-prover-base-mcp` points at
  `prover.axiomatic-ai.com/mcp/`: OAuth via GitHub, code shipped to their cloud
  for compilation *and* AI-assisted proving. Tempting as a free Mathlib sandbox;
  it is squarely against the API-traffic rule (no held-out eval content, no
  DMW/Dalang-named material) and the traffic would be *ours to explain*. The
  `ax-prover-base` agent itself is local and fine to read.
- **A frontier prover swap.** Not on a vendor README. The instrument that decides
  this is the obstruction census on the live queue, not Putnam.

## Correction to the 07-11 survey

That survey records, under lean-lsp-mcp, **REFUTED (0-3)**: "the claim that
Ax-Prover/Numina-Lean-Agent/MerLean/M2F build on it." For Ax-Prover specifically
that verdict is now wrong on the evidence: their Lean tool surface is
lean-lsp-mcp's tool names, exactly. Whether they vendor the package or
reimplement its interface is still unconfirmed — but "does not build on it" is
not the finding. Downgraded in place to *same tool surface, provenance
unconfirmed*. The original conclusion (our harness choice stands on its own
merits) is unaffected, and mildly strengthened.

## Adjacent find, different author

[arXiv:2506.07066](https://arxiv.org/abs/2506.07066), "From Axioms to Algorithms:
Mechanized Proofs of the vNM Utility Theorem" — a Lean 4 formalization of the
von Neumann–Morgenstern expected-utility theorem (completeness, transitivity,
continuity, independence; existence and uniqueness of the representation).
**Not Axiomatic AI's** — Jingyuan Li, Lingnan University; it surfaced on a topical
search and is recorded here only so the attribution does not drift. It is in
MathFin's domain and adjacent to `MathFin/RiskMeasures/UtilityDerivation.lean`,
so it is worth a duplicate check before any utility-theory issue is queued.

## Sources

github.com/Axiomatic-AI/ax-prover-base (agent, configs, CLAUDE.md, README) ·
github.com/Axiomatic-AI/ax-prover-base-mcp (hosted MCP) ·
arXiv:2510.12787 (Ax-Prover) · axiomatic-ai.com (products, positioning) ·
finance.yahoo.com "Axiomatic AI Raises $18M" (funding) ·
arXiv:2506.07066 (vNM, unrelated author).
