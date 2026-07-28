# The MathFin operation — a map for a new collaborator

You're joining a two-repo operation that machine-checks mathematical-finance
theorems in Lean 4 and uses an LLM prover to help draft them. This doc is the
orientation: what the two repos are, how the autoformalization pipeline works,
and the outside reading that makes it click. It assumes you're a strong engineer
but new to Lean, proof assistants, and formal math — no prior exposure needed.

## The one-paragraph mental model

There are two repos:

- **`formal-mathfin`** (public) — a Lean 4 library of ~324 formally verified
  theorems in mathematical finance. This is *the artifact*. A theorem is "done"
  only when Lean's kernel accepts a proof with zero gaps.
- **`mathfin-foundry`** (private, this repo) — a two-engine, self-feeding
  autoformalization pipeline. From an open proof *issue* it drafts a Lean
  *statement* (Mistral's **Magistral**, faithfulness-gated), then proves it
  (Mistral's **Leanstral**), checks both against Lean's kernel, and surfaces the
  good ones for a human to refine and merge into `formal-mathfin`.

If you've built agentic coding loops, you already understand the foundry. It is
an agent proposing code (a Lean proof) into a **compiler-feedback repair loop** —
with two twists: "compile passes" means a *proof kernel* certified a gap-free
proof, and "tests pass" means the proof depends on nothing but three standard
axioms. The prover is a **scout, not an author**: nothing it produces merges
without a human lifting it to the library's standard.

That's the whole thing. The rest is detail.

---

## Part 1 — The library (`formal-mathfin`): what gets proved

A Lean 4 library built on top of two dependencies:

- **Mathlib** — the community's ~1.5M-line mathematics library (analysis,
  measure theory, topology, …). We *consume* Mathlib rather than reprove it.
- **Degenne's `BrownianMotion`** — a Lean formalization of Brownian motion we
  build the stochastic-calculus layer on.

Pinned to **Lean v4.32.0 · Mathlib @81a5d257 · BrownianMotion @4d52fa77**. The
pins are exact and load-bearing; a version bump is a real event.

### The two commitments that make it trustworthy

1. **The build is the proof.** A clean `lake build` re-elaborates every theorem
   against the pinned toolchain. There is no `sorry` (Lean's "trust me" escape
   hatch) anywhere, and every finished theorem depends only on the three standard
   axioms `propext, Classical.choice, Quot.sound` — pinned as a CI invariant.
   There is no separate "test suite"; the type-checker *is* the test.
2. **Honest scope, enforced.** Every entry declares a faithfulness status —
   `full`, `library_wrapper`, or `reduced_core` (an honest special case) — and an
   input-hash **verification ledger** records exactly what each theorem was
   checked under. A multi-agent **values review** (eight judgment lenses) runs on
   a cadence. The README never claims a result the kernel hasn't accepted.

### The architecture is the point

Formalized finance is usually a scattering of isolated results. The ambition here
is a *theory*: four pillars, with the **connections between them made
load-bearing** rather than decorative.

| Pillar | The principle |
|---|---|
| **No-arbitrage as convex duality** | the separating hyperplane *is* the risk-neutral (martingale) measure |
| **Stochastic calculus** | every model is `dX = b dt + σ dB`; Itô's formula makes functionals computable |
| **Probabilistic ⟷ analytic duality** | a price is both a risk-neutral expectation *and* a PDE solution (Feynman–Kac) |
| **Intensity & exponential families** | closed forms as the "exp of an integrated intensity" |

The depth lives in the **bridges** — each makes two pillars *one theorem*: the
FTAP ⟷ coherent-risk unification (both are the same Hahn–Banach separation),
Feynman–Kac (BS PDE from the risk-neutral expectation), Donsker/CLT (binomial →
Black–Scholes), Girsanov (the EMM as an explicit change of measure), and the
numéraire change. The headline towers are the **Itô tower** (a from-scratch L²
Itô integral up to Itô's formula for general C³ functions) and the **FTAP tower**.

### Where to read next (inside `formal-mathfin`)

Read roughly in this order:

1. `README.md` — the landmark results and the live status counts.
2. `docs/mathematical-architecture.md` — the four pillars and the bridges, seam
   by seam. This is the conceptual core.
3. `docs/coverage.md` — the per-theorem audit: what's `full` vs `reduced_core`,
   and the exact claim wording.
4. `docs/patterns.md` — the distilled Lean proof idioms this library holds code
   to (also fed to the prover as "house idioms").
5. `CONTRIBUTING.md` + `docs/onboarding.md` — the first-contribution workflow
   (assumes you already know Lean syntax — Part 3 below gets you there).
6. `CLAUDE.md` — the operational bible: build commands, the memory doctrine, the
   values gates. Dense but authoritative.

---

## Part 2 — The autoformalization (`mathfin-foundry`): how proofs get drafted

The pipeline is a metered repair loop. Full ASCII/Mermaid diagram in
`docs/leanstral-architecture.md`; the compact version:

```
 TARGET            a Lean stub `theorem … := by sorry` + pointers. When the queue
 (from an issue)   is empty the tick SELF-FEEDS: Magistral drafts the stub from the
                   next ready issue and five faithfulness gates validate it
                   (elaborate · ⊢False · ⊢¬Concl · judge · roundtrip) before it is
                   queued — `probe/autoformalize.py`.
        │
        ▼
 HOUSE DOCTRINE    injected as the system prompt on every attempt:
 (system prompt)   values gate · the LIVE `docs/patterns.md` (first-class) ·
                   exact pins · "consume Mathlib/Degenne, don't reprove" ·
                   per-target context pack (signatures of the modules to reuse)
        │
        ▼
 LEANSTRAL         Mistral's Lean-4 prover. Emits a candidate .lean file;
 (the prover)      on failure it is re-fed the compiler errors and resends
        │  ▲       the whole file. This is the loop it was trained for.
        ▼  │ goal states + compiler errors
 LEAN ENVIRONMENT  Docker, memory-capped, ONE Lean process at a time
 (checks it)       (a persistent REPL daemon XOR the lean-lsp server)
        │
        ▼
 ACCEPTANCE GATE   no errors · no sorry · axioms ⊆ {propext, Classical.choice,
                   Quot.sound} · no forbidden tactics (native_decide, exact?, …)
        │
        ▼
 REFINERY          8-lens values review → the conceptually-*right* proof →
 (scout→author)    a human authors the PR into formal-mathfin
```

A machine proof that merely passes the kernel is a **candidate**, not a
contribution. The refinery rewrites it into the proof that shows *why* the result
is true, in the house idiom, before it merges. An opaque 20-premise discharge is
"slop" even when the kernel accepts it.

### The knobs (this is where the ML-systems intuition pays off)

`pipeline.toml` configures a **pass@k** harness. Per proof task, it samples
`fanout` whole-proof candidates in parallel (currently 8), batch-checks them, then
runs up to `repair_rounds` (2) compiler-feedback repairs on the best failure. The
research finding baked in: **tokens-per-attempt is the dominant lever** — a bigger
per-attempt reasoning budget beats more small attempts (Leanstral's own
PutnamBench curve climbs 44 → 587 solves as the per-problem budget goes
50k → 4M tokens). A monthly token allowance caps spend; hard tasks escalate.

### The hard rules (read these before touching anything)

- **The foundry reads `formal-mathfin`; it never *merges* to it.** The pipeline may
  OPEN a ready-for-review PR (with `MAIN_PR_TOKEN`), but every PR runs the refinery +
  8-lens and a human owns the merge. Nothing reaches `main` unreviewed.
- **Scout, not author.** Nothing merges without the human refinement pass and the
  8-lens bar. This is non-negotiable and it's the whole ethical spine of the op.
- **API traffic carries only public-corpus and fresh-textbook statements** — never
  held-out eval content, never named crown-theorem material.
- **The memory doctrine.** This is a ~10 GB box and a Mathlib-loaded Lean
  environment is ~4–5 GB, so **exactly one Lean-loaded process runs at a time**.
  Every OOM and machine freeze in this project's history was two Lean processes at
  once. When the REPL daemon is up, don't `lake build`; when you run the lean-lsp
  server, the daemon must be down. `CLAUDE.md` has the full doctrine.

### The hands-off PR pipeline

`.github/workflows/pipeline.yml` runs on a cron. When the queue has no
unattempted target it first **self-feeds** — Magistral autoformalizes and
faithfulness-gates a stub from the next ready issue (`autoformalize.py`) — then it
proves the target and, on a pass, opens a *ready-for-review* PR on `formal-mathfin`
that closes the source issue (assembling the proof into its module + a re-export
benchmark entry, regenerating the audit, and building green-or-abort in the
verify image). A multi-part issue may be answered by a faithful **subset**: the
drafter declares the remaining facts (`deferred`), the PR `refs` (not `closes`) the
parent and lists the remainder as **suggested follow-up issues** for R to open —
the gate rejects a *silent* gap, never a *declared* one. The first such PR (#120, a contango result) opened 2026-07-11 —
CI-green but **unmerged** (both early autoform PRs are now conflicting as `main`
moved on). An opened PR is a *proposal* R reviews + revises before merge, not proof
of quality; the merge is. (Likewise the Magistral judge is a *soft self-check*; the
roundtrip is a *cross-model back-translation* — Leanstral independently re-formalizes —
genuinely independent but still soft. Neither soft check is a faithfulness guarantee;
the kernel gates + human review are the rigorous ones.) A candidate that won't assemble
green files a blocked issue on
the foundry instead of opening a red PR. The one credential that grants write
access to main is a fine-grained PAT (`MAIN_PR_TOKEN`); revoking it fully disables
auto-PR. Details in `docs/PROVER_SETUP.md`.

### Where to read next (inside `mathfin-foundry`)

1. `README.md` — the operational hard-rules and repo layout.
2. `docs/leanstral-architecture.md` — the full pipeline diagram.
3. `docs/PROVER_SETUP.md` — how the prover agents are equipped (the system
   prompt, the per-target context pack, the loop, the lean-lsp-mcp harness, the
   PR activation).
4. `docs/research/2026-07-11-world-class-autoformalization-survey.md` — how the
   top labs run Lean autoformalization and why *our* shape is the validated one.
   The single best doc for the research context; read it after Part 3's papers.
5. The code: `probe/probe.py` (the repair loop), `probe/pipeline.py` +
   `pipeline_lib.py` (cadence + budgeting), `probe/house_context.py` (the system
   prompt assembly), `scripts/open-pr.sh` (the assemble-and-PR path).
6. `docs/superpowers/specs/2026-07-08-leanstral-foundry-design.md` — the
   design-of-record (mission, posture, the decision log).

---

## Part 3 — Getting fluent (curated reading)

You don't need to become a Lean expert to be useful, but you do need the ideas —
and a feel for the type-checker-as-tests loop. Read a couple of the essays first
(why this matters, what formalization *is*, no syntax required), then get
hands-on. Everything below is live as of 2026-07-11.

### Start here — the big picture (accessible essays, no Lean required)

Pick two or three. They give you the *why now* and the core concepts before you
touch a single tactic.

- **[The AI Revolution in Math Has Arrived](https://www.quantamagazine.org/the-ai-revolution-in-math-has-arrived-20260413/)**
  (Quanta, 2026) — the best single overview of this moment: large language models,
  formal provers, and Mathlib converging. Start here.
- **[Building the Mathematical Library of the Future](https://www.quantamagazine.org/building-the-mathematical-library-of-the-future-20201001/)**
  (Quanta, 2020) — what Mathlib and Lean actually are, for a general reader. The
  concept intro to the thing `formal-mathfin` is built on.
- **[The Deep Link Equating Math Proofs and Computer Programs](https://www.quantamagazine.org/the-deep-link-equating-math-proofs-and-computer-programs-20231011/)**
  (Quanta, 2023) — the Curry–Howard idea that *a proof is a program*. The single
  concept that most demystifies how a computer can check mathematics at all.
- **[How Terry Tao Became an Evangelist for AI in Math](https://www.quantamagazine.org/how-terry-tao-became-an-evangelist-for-ai-in-math-20260608/)**
  (Quanta, 2026) — a Fields medalist's route to Lean + AI, and where he thinks it
  goes. The human story, and a good map of the near future.
- **[Machine-Assisted Proof](https://www.ams.org/journals/notices/202501/noti3041/noti3041.html)**
  (Terence Tao, *Notices of the AMS*, 2025) — a working great mathematician's own
  expository essay. Meatier than the Quanta pieces; worth the extra effort.
- **[AI achieves silver-medal standard at the IMO](https://deepmind.google/blog/ai-solves-imo-problems-at-silver-medal-level/)**
  (Google DeepMind, 2024) — AlphaProof, the landmark that put Lean-checked AI
  proofs on the map, told accessibly.
- **[In Math, Rigor Is Vital. But Are Digitized Proofs Taking It Too Far?](https://www.quantamagazine.org/in-math-rigor-is-vital-but-are-digitized-proofs-taking-it-too-far-20260325/)**
  (Quanta, 2026) — the skeptical, balanced view. Read it so you know the live
  debates, not just the hype.
- **[Kevin Buzzard, ICM 2022 — "The Rise of Formalism in Mathematics"](https://gilkalai.wordpress.com/2022/07/17/icm-2022-kevin-buzzard-the-rise-of-formalism-in-mathematics/)** —
  a mathematician's full case for machine-checked proof (talk video + summary).
- **[Fermat's Last Theorem in Lean](https://lean-lang.org/use-cases/flt/)** — the
  flagship in-progress project; concrete calibration of what the tooling can do.

### Then get hands-on with Lean (in order)

- **[Natural Number Game](https://adam.math.hhu.de/#/g/leanprover-community/NNG4)** —
  the canonical first hour with Lean. Prove basic arithmetic in the browser; you
  learn the tactic loop by doing. Start here, today.
- **[Theorem Proving in Lean 4](https://leanprover.github.io/theorem_proving_in_lean4/)** —
  the official book (Avigad, de Moura, Kong, Ullrich). Ch. 1–5 is enough to read
  our proofs.
- **[Learning Lean 4 hub](https://leanprover-community.github.io/learn.html)** —
  the community's index of every learning resource, kept current.

### The search tools you'll live in

Finding the right existing lemma *is* the job (our doctrine is "consume, don't
reprove"). These are how:

- **[LeanSearch](https://leansearch.net/)** — natural-language search over Mathlib
  ("the derivative of a product is…"). Best when you don't yet know what exists.
- **[Loogle](https://loogle.lean-lang.org/)** — search by *shape*: type
  signatures, subexpression patterns, constant names. Best once you know roughly
  what you want.
- **[Mathlib4 API docs](https://leanprover-community.github.io/mathlib4_docs/)** —
  the generated reference for everything in Mathlib.
- **[Lean Zulip](https://leanprover.zulipchat.com/)** — the community chat.
  Astonishingly responsive; the `#new members` stream expects beginner questions.

### Going deeper — technical blogs & tutorials (start here for the internals)

More accessible than the papers below, and often clearer on the engineering.
Read these first:

- **[Machine Learning for Theorem Proving](https://machine-learning-for-theorem-proving.github.io/)**
  (Sean Welleck et al., NeurIPS tutorial) — the field's best teaching resource:
  how neural provers, autoformalization, and premise retrieval actually work, with
  runnable notebooks ([ntptutorial](https://github.com/wellecks/ntptutorial)). The
  single best "learn the whole space" link.
- **[AlphaProof, by one of its authors](https://www.julian.ac/blog/2025/11/13/alphaproof-paper/)**
  (Julian Schrittwieser, DeepMind) — the AlphaProof Nature paper in plain
  engineering terms, by someone who built it. Much faster than the paper.
- **[Kimina-Prover RL](https://huggingface.co/blog/AI-MO/kimina-prover-rl)**
  (Project Numina, HF blog) — a hands-on writeup of the RL recipe and the
  reasoning-then-generation design, with the open training pipeline attached.
- **[Introducing Gauss](https://www.math.inc/gauss)** (Math Inc.) — the
  autoformalization agent that formalized the strong Prime Number Theorem in ~3
  weeks. The clearest window into a production formalization op, and it runs the
  same scout-not-author shape we do: human decomposition + review above the fleet.
- **[Lean community blog](https://leanprover-community.github.io/blog/)** — ongoing
  technical posts from the people who build Mathlib (proof automation, large
  formalization projects, tooling).

### The architectures — the primary sources

The system designs the field is built on. Read these for the *shape* of an
autoformalizer, not the latest leaderboard number — each introduced a pattern
still in use:

- **[Autoformalization with Large Language Models](https://arxiv.org/abs/2205.12615)**
  (Wu et al., NeurIPS 2022) — the seminal result that LLMs can translate
  natural-language math into formal statements (few-shot). The origin of
  "autoformalization" as an LLM task; it is the *statement*-side problem our
  targets sidestep by starting from R-curated stubs.
- **[Draft, Sketch, and Prove](https://arxiv.org/abs/2210.12283)** (Jiang et al.,
  ICLR 2023) — informal proof → formal *sketch* → let an automated prover fill the
  gaps. The decomposition pattern Aristotle and Gauss still run on, and the
  blueprint for our roadmap's subgoal-decomposition step.
- **[LeanDojo / ReProver](https://arxiv.org/abs/2306.15626)** (Yang et al., NeurIPS
  2023; [leandojo.org](https://leandojo.org/)) — the open Lean-interaction
  environment plus **retrieval-augmented** proving (pull the right premises from
  the library before generating a tactic). That retrieval idea is our
  "consume-don't-reprove" context pack, mechanized.
- **[AlphaGeometry](https://deepmind.google/blog/alphageometry-an-olympiad-level-ai-system-for-geometry/)**
  (DeepMind; [Nature, 2024](https://www.nature.com/articles/s41586-023-06747-5)) —
  the **neuro-symbolic** design: a language model proposes constructions, a sound
  symbolic engine deduces. The clearest case study in trading neural search
  against a symbolic core, and a contrast to the LLM-only shape we run.

### The recent open provers (our pipeline's shape)

Our loop is the "whole-proof sampling + compiler-feedback repair" pattern shared
by every state-of-the-art open prover. The full, adversarially-verified survey is
internal — `docs/research/2026-07-11-world-class-autoformalization-survey.md` — and
these are the papers behind it:

- **[Leanstral 1.5 — Mistral](https://mistral.ai/news/leanstral-1-5/)**
  ([weights](https://huggingface.co/mistralai/Leanstral-1.5-119B-A6B)) — our
  prover. 119B MoE (6.5B active), Apache-2.0, trained for a multiturn
  compiler-feedback loop and an in-repo code-agent mode, and officially supports
  `lean-lsp-mcp`. Released 2026-07-02.
- **[Goedel-Prover-V2](https://arxiv.org/abs/2508.03613)** — the clearest writeup
  of verifier-guided **self-correction** (feed compile errors back, ~2 rounds).
  Shows repair is *causal* and not substitutable by more parallel samples — the
  exact justification for our repair loop.
- **[Kimina-Prover](https://arxiv.org/abs/2504.11354)** — whole-proof generation,
  no tree search; documents the **pass@k sample-efficiency knee** (~most of the
  value by 32 samples) that sets our `fanout`.
- **[DeepSeek-Prover-V2](https://arxiv.org/abs/2504.21801)** — **subgoal
  decomposition**: a big model splits a hard theorem into lemmas a small prover
  can close. The next capability jump on our roadmap.
- **[AlphaProof (Nature, 2025)](https://www.nature.com/articles/s41586-025-09833-y)** —
  the frontier, *tree-search* shape (needs a datacenter). Read it to understand
  what does **not** transfer to a one-box operation — and why we deliberately
  don't chase it.
- **[lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp)** — the MCP server that
  exposes the Lean language server's live goal states/diagnostics/search as agent
  tools. The harness Leanstral is trained to drive.

### The stack it's built on

- **[Mathlib4](https://github.com/leanprover-community/mathlib4)** — the math
  library we consume.
- **[BrownianMotion (Rémy Degenne)](https://github.com/RemyDegenne/brownian-motion)** —
  the Brownian-motion formalization our stochastic layer sits on.

### The finance math (as you need it — don't front-load)

- **[Yuri Saporito](https://www.yurisaporito.com/)** (FGV EMAp) — the corpus's
  benchmark files are organized by chapters of his stochastic-processes course;
  he works on functional Itô calculus and stochastic volatility.
- **Steven Shreve, *Stochastic Calculus for Finance II: Continuous-Time Models*
  (Springer Finance, 2004)** — the standard graduate text for the continuous-time
  half (Itô, Girsanov, Black–Scholes, Feynman–Kac).
- **Tomas Björk, *Arbitrage Theory in Continuous Time* (Oxford)** — the standard
  arbitrage-pricing / FTAP reference.

---

## Your first day, concretely

1. Skim this doc and the two `README.md` files.
2. Play the [Natural Number Game](https://adam.math.hhu.de/#/g/leanprover-community/NNG4)
   to level ~4. You now understand the tactic loop.
3. Read `formal-mathfin/docs/mathematical-architecture.md` for the *what*.
4. Read `mathfin-foundry/docs/leanstral-architecture.md` for the *how*.
5. Pull the pinned Docker image and run one `lake build` end to end
   (`CONTRIBUTING.md` has the commands) so the toolchain is real to you.
6. Read one pipeline tick's telemetry in `runs/` against `probe/probe.py`.

Ask early — the Zulip for Lean questions, and the team for operation questions.
