# How the top labs run autonomous Lean 4 formalization — and what upgrades our foundry (2026-07-11)

Deep-research survey (103-agent fan-out, 21 primary sources, 104 claims extracted,
24 adversarially confirmed / 1 refuted). Every claim below marked **[verified]**
survived a 3-vote adversarial verification pass against the primary source;
claims marked **[primary, unverified]** were extracted verbatim from primary
sources but did not go through the voting pass (verify before load-bearing use).

## The landscape: two production shapes

### Shape 1 — frontier search-based (does NOT transfer)

**AlphaProof (DeepMind)** [verified — Nature, s41586-025-09833-y; Schrittwieser blog]:
a 3B encoder-decoder policy+value transformer inside AlphaZero-style AND-OR tree
search over Lean tactic states; AND nodes backpropagate the minimum
(hardest-child) value. Feedback is per-tactic-state, kernel-checked. Curriculum:
a fine-tuned Gemini 1.5 Pro autoformalized ~1M informal problems into **~80M
formal variants (~80/statement, deliberately randomized prompts)** at ~100,000
TPU-days; the RL loop symmetrically attempts disproofs, and disproved (invalid)
formalizations are retired. Main RL: **~80,000 TPU-days**. At IMO 2024 the
algorithm actually run was **test-time RL**: an LLM generates a bespoke
curriculum of simplified/generalized variants of the single target problem and
RL continues on them — 2–3 TPU-days *per problem* (P1, P2, P6).

**Aristotle (Harmonic)** [primary, unverified — arXiv:2510.01346]: highly
parallel Monte Carlo *Graph* Search (PUCT variant over state/action equivalence
classes) with one large transformer as policy+value; AND/OR minimax structure;
every single-goal state gets a **negation transition** so search can disprove
and prune. Outer loop is Draft-Sketch-Prove: informal proof → restructure into
lemmas with individually short proofs → autoformalize the lemma statements →
Lean REPL feedback loop to fix them → hand the sketch to search; **proved
lemmas are kept, unproved ones revised**. Dedicated statement-autoformalization
subsystem (formalize → judge via REPL signals → correct) plus an **LLM
faithfulness judge** filtering unaligned proofs. IMO 2025 gold (5/6, formally
verified; only the 6 problem statements were hand-formalized) needed a >200B
policy model + test-time training on its own search traces. Beyond contests:
proved Mathlib-missing theorems (Niven, Gauss–Lucas), contributed to PFR, and
validated chapters of Tao's analysis textbook — **finding 4 exercises false as
written, with explicit counterexamples**.

Why it doesn't transfer: policy/value tree search and TTRL both require weight
access + datacenter compute. What *does* transfer: the lemma-sketch pipeline,
disproof-as-filter, faithfulness judging, keep-proved-lemmas bookkeeping.

### Shape 2 — whole-proof sampling + compiler-feedback repair (OUR shape)

This is the loop of every SOTA open prover, and it is structurally identical to
`probe.py`'s metered repair loop — the architecture is validated.

- **Goedel-Prover-V2** [verified — arXiv:2508.03613, ICLR 2026]: expert
  iteration + RL with scaffolded difficulty data, **verifier-guided
  self-correction (whole-proof compile results fed back, 2 revision rounds)**,
  checkpoint averaging for diversity. Self-correction adds **+2.3pp** (32B:
  88.1% → 90.4% pass@32 miniF2F, ~33% more tokens/sample); ablation shows the
  compiler error text is causal; self-correction at pass@32 (92.7%) **beats
  standard mode at pass@8192 (92.2%)** — repair is not substitutable by more
  parallel samples. **8B beats DeepSeek-671B at matched pass@32 (84.6% vs
  82.4%)** — pipeline quality > parameter scale. PutnamBench: 86 @ pass@184 vs
  DeepSeek's 47 @ pass@1024.
- **Kimina-Prover 72B** [verified — arXiv:2504.11354]: whole-proof generation,
  NO tree search, binary kernel reward, structured think-tokens interleaving
  informal reasoning with Lean snippets (most snippets must survive into the
  final proof). **Sample-efficiency knee: pass@1 52.94%, pass@32 68.85% —
  ~85% of its pass@8192 value (80.7%) arrives by 32 samples.** Verification as
  a service: Kimina Lean Server, ~10x checking throughput (claimed peak 100
  it/s on 64 cores/512 GB); whole RL run needed only ~640 CPU cores.
- **DeepSeek-Prover-V2** [verified — arXiv:2504.21801]: a general LLM
  (DeepSeek-V3) **recursively decomposes hard problems into subgoals**, a 7B
  prover resolves them, and resolved subgoal proofs + informal reasoning are
  synthesized into cold-start CoT for RL. 88.9% miniF2F at pass@8192 (SOTA
  mid-2025, since surpassed). Statement-data gates: LLM quality score +
  **hypothesis rejection** (try to prove False from the hypotheses; discard on
  success) — both cheap, both transfer.
- **Leanabell-Prover-V2 7B** [verified — arXiv:2507.08649]: RL with the
  verifier inside multi-turn interactions (success/error details,
  feedback-token masking) → reflexive self-correction; +3.2pp / +2.0pp
  pass@128 over strong baselines. Together with Goedel: repair adds ~2–3pp at
  high k; **gains are larger in the pass@1/low-budget regime — exactly ours**.
- **Leanstral 1.5 (our prover)** [primary, unverified — Mistral coverage,
  marktechpost 2026-07-03; NYU RITS]: 119B MoE (6.5B active), Apache 2.0,
  trained with CISPO RL in **two environments mirroring our two harnesses** —
  a multiturn compiler-feedback theorem-proving loop and a code-agent
  environment with filesystem/bash/LSP; officially supports lean-lsp-mcp.
  **Test-time token budget is its dominant lever: PutnamBench pass@8 goes
  44 → 244 → 493 → 587 (of 672) at 50k → 200k → 1M → 4M tokens/attempt.**
  ~$4/problem vs Seed-Prover's ~$300+. NOTE: one source names a free endpoint
  `labs-leanstral-2603` (March 2026 release) vs our `labs-leanstral-1-5` —
  check Mistral's docs for the current best endpoint before acting.

### Production formalization ops (the "who watches the agents" layer)

**Gauss (Math Inc) on strong PNT** [primary, unverified — math.inc/gauss;
math-inc.github.io/strongpnt]: completed the Tao–Kontorovich strong PNT
challenge in ~3 weeks — ~25k LOC, ~1.1k theorems/definitions — where the human
crowdsourced effort had reached only intermediate progress in 18 months. Runs
on Morph Labs' Trinity infra: **thousands of concurrent agents, each with its
own Lean runtime, terabytes of cluster RAM** (does not transfer). Critically:
**not fully autonomous** — "relies on natural language scaffolding supplied by
human mathematicians, and requires high-level expert guidance," with human
review of key lemmas, organized around a blueprint. I.e. the most productive
formalization op in existence runs our exact scout-not-author model: human
decomposition + review above, agent fleet below.

**miniCTX / ntp-toolkit (CMU)** [verified — arXiv:2408.03350 lineage,
cmu-l3.github.io/minictx]: **file-tuned models (full preceding-file context)
nearly double success vs state-tactic models (35.94% vs 19.53%)** — context
richness is a first-order lever, first-class validation of our context-pack
design and a pointer to include *more* than signatures. Temporal splits keep
evals uncontaminated; ntp-toolkit mechanically converts any Lean repo into
next-tactic / full-proof eval examples.

**LeanHammer** [verified — arXiv:2506.07477, cmu-l3.github.io/lean-hammer]:
premise selection → Lean-auto HOL translation → external ATPs → Duper/Aesop
kernel-checked reconstruction; generalizes to unseen projects (73.5% Mathlib →
79.4% miniCTX-v2, no OOD drop) and **dynamically indexes local-project
premises** (first call ~10–20s). **Default premise selector is a cloud server
(leanpremise.net)** — a private foundry must self-host lean-premise-server or a
local selector (our existing hammer privacy doctrine already mandates this).
Known gaps: dependent types, no induction/arithmetic — untested on
measure-theory-heavy code.

**lean-lsp-mcp (our agentic harness)** [verified — github.com/oOo0oOo/lean-lsp-mcp,
v0.28.0 2026-07-06]: per-file diagnostics + line/column goal states via
`lake serve`; bundles LeanSearch/Loogle/Lean Finder/LeanHammer-search/State
Search + local ripgrep; **hosted endpoints rate-limited ~3 req/30s**, bypassable
via `LOOGLE_URL`, `LEAN_STATE_SEARCH_URL`, `LEAN_HAMMER_URL` (local Loogle
build ~2 GB); opt-in **REPL-backed `lean_multi_attempt` (~5x faster,
maintainer estimate)** for cheap candidate fan-out. REFUTED (0-3): the claim
that Ax-Prover/Numina-Lean-Agent/MerLean/M2F build on it — our choice stands on
its own merits, not on lab adoption.

**Statement-side faithfulness** [mixed]: typecheck acceptance is an
insufficient gate [verified]; concrete tools: BEqL/BEq+ symbolic equivalence,
LeanScorer LLM decomposition scoring, roundtrip
formalize→informalize→re-formalize→equivalence-check [primary, unverified];
AlphaProof's disprove-and-retire and DeepSeek's hypothesis rejection are the
two cheap kernel-grade filters [verified].

## What categorically does NOT transfer

RL training of any kind (weights + compute); AlphaZero/MCGS tactic-state search
(needs policy/value access); real TTRL (2–3 TPU-days/problem); Gauss's
thousands-of-runtimes fleet; Kimina's 640-core verification farm. The 10 GB box
+ $0 Leanstral API + one lean-repl daemon is the fixed frame; everything below
fits it.

## Ranked upgrade backlog

1. **HARNESS — pass@k fan-out + bounded repair in `probe.py`** (evidence:
   Kimina knee by pass@32; Goedel exactly 2 revision rounds; Leanabell repair
   gains largest in low-budget regime). Replace the purely sequential loop:
   per round, sample k=4–8 whole-proof candidates at temperature > 0 in
   parallel API calls, batch-check them against the daemon (one round-trip
   each, daemon serializes anyway), then ≤2 repair rounds on the best failure.
   In the vibe harness, enable REPL-backed `lean_multi_attempt`.
2. **HARNESS — spend the budget as fewer, bigger attempts** (evidence:
   Leanstral's own PutnamBench curve 44→587 solves is driven by
   tokens-per-attempt, not attempt count). Our 500k/issue cap likely serves
   better as 2–4 attempts × high reasoning budget than many small rounds.
   Measure on MathFin-Bench (item 5) before hard-coding.
3. **SCHEDULER — subgoal decomposition for stuck targets** (DeepSeek-V2 +
   Aristotle lemma pipeline; the single biggest capability jump). After a
   target exhausts its budget: general LLM writes an informal proof →
   restructures it into lemma statements with individually short proofs →
   autoformalize each (with context pack) → Leanstral proves lemmas
   independently → recompose. Keep proved lemmas (Aristotle's keep-and-revise);
   queue them as first-class targets. New `probe/decompose.py` + manifest
   support for lemma DAGs.
4. **RETRIEVAL — enrich context packs with dependency-closure + preceding-file
   content** (miniCTX: file context ~doubles success). `scout_index.py`
   already has `dependencies()` — for each `-- pointers:` target, walk the
   statement's cited constants through const_dep and inject the *exact*
   signatures of its dependency closure, plus the target file's preceding
   source, not just per-module signature lists.
5. **EVALUATION — MathFin-Bench + Leanstral's pass@k curve on OUR
   distribution** — **⛔ DROPPED as a recommendation (R, 2026-07-18): do not
   propose or plan a held-out eval/benchmark until R reopens it**
   ([[feedback_bench_dropped]]; `docs/upgrade-backlog.md` top). The field-finding
   below stays as record; it is no longer a backlog item. Tuning now reads the
   live queue (obstruction-family report + A/B scoreboard on real targets), not a
   synthetic set. *(Historical description:* nobody had measured the knee on OOD
   measure-theory finance; the proposal was to freeze ~30 proved theorems, strip
   proofs, run pass@1/4/8 at 2–3 budgets — the former BIG-LEAP Phase-1 idea.*)*
6. **STATEMENT SIDE — faithfulness gates in the autoformalize stream**
   (AlphaProof disprove-and-retire; DeepSeek hypothesis rejection; Harmonic
   judge). Before spending proof budget on an autoformalized statement:
   (a) 2–4 formal variants per informal statement, randomized prompts;
   (b) hypothesis rejection — one cheap attempt to prove `False` from the
   hypotheses (or the statement's negation); (c) roundtrip back-translation
   judged against the original. Record outcomes in `formalization.yaml`'s
   fidelity field.
7. **RETRIEVAL — self-host the search stack in the vibe harness** (hosted
   endpoints 3 req/30s; privacy doctrine). Wire `LOOGLE_URL` (local build
   ~2 GB, host-side is fine — it's not a Lean-env process... verify memory
   footprint first against the one-Lean-process doctrine) or lean towards
   `lean_local_search` (ripgrep) + our lean_scout packs, which already run
   local. If we ever adopt `by hammer` in-loop, self-hosted
   lean-premise-server only.
8. **SCHEDULER — variant warm-up (cheap TTRL analogue)** [own inference,
   flagged as such by the research]: for a stuck target, generate simplified
   variants (special case, stronger hypotheses, n=1/n=2 instances), prove
   those first, feed them back as context/lemmas next tick.
9. **OPS — keep scout-not-author; check the newer Leanstral endpoint.**
   Gauss — the most productive formalization op — keeps expert scaffolding +
   human review of key lemmas above the fleet: our model is the production
   norm. Separately, verify whether `labs-leanstral-1-5` is still the newest
   free endpoint (one source names `labs-leanstral-2603`).

## Open questions the research could not settle

- Closed-startup loops (Harmonic ops details beyond the paper, Math Inc/Gauss
  internals, Axiom, Morph Trinity) — no claims survived verification.
- Leanstral's sample-efficiency knee on MathFin-like OOD content (item 5
  measures this ourselves).
- Whether LeanHammer's premise selection + reconstruction holds up on
  measure-theory/stochastic-calculus code (dependent types, no induction).
- How Blueprint-driven crowdsourced ops (PFR/FLT/equational theories) route
  tasks — transferable mechanics for a solo pipeline where the scheduler
  replaces the crowd.

## Sources (primary)

Nature s41586-025-09833-y (AlphaProof) · julian.ac/blog/2025/11/13 (Schrittwieser)
· arXiv:2508.03613 (Goedel-V2) · arXiv:2504.21801 (DeepSeek-V2) ·
arXiv:2504.11354 (Kimina) · arXiv:2507.08649 (Leanabell-V2) ·
arXiv:2510.01346 (Aristotle) · math.inc/gauss + math-inc.github.io/strongpnt
(Gauss/strong PNT) · cmu-l3.github.io/lean-hammer + arXiv:2506.07477
(LeanHammer) · cmu-l3.github.io/minictx (miniCTX) · github.com/oOo0oOo/lean-lsp-mcp
· lean-lang.org/use-cases/flt · marktechpost 2026-07-03 + NYU RITS (Leanstral
coverage; blog-tier) · arXiv:2510.01346 / 2604.25031 / 2505.23486 / 2507.07399
(faithfulness metrics).
