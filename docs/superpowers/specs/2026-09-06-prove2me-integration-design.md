# Design — Prove2Me integration: statements out, proofs in

**Status:** proposed, 2026-09-06. **Owner:** R.

**Source.** *Prove2Me: An Open Collaborative Platform for Scaling Math Formalization*
(Chen, Marwaha, Lu, Yuen, Peng — [arXiv 2608.28433v2](https://arxiv.org/abs/2608.28433),
31 Aug 2026) plus the live harness at
[`prove2me/prove2me_workspace`](https://github.com/prove2me/prove2me_workspace)
(`SKILL.md` v0.9.7 + 13 reference files). Read against this repo at
`c419f0f` / queue manifest of 11 targets.

---

## 0. The recommendation in one paragraph

Integrate Prove2Me as an **overflow lane for decomposition leaves**, not as a
publishing target for finished work. The foundry's binding failure today is
`prover-max-rounds` (18 of 57 live obstructions, spread over six of the eleven
queued targets), and the mechanism built for it — the lemma-DAG decomposer — has
produced exactly one routable split in its life (`cal-bk-71`, 2026-09-03). Prove2Me's
proof-sketch is the same object as our `*.dag.json`, with one property we cannot build
ourselves: **someone else's token budget closes the leaves**. So publish *statements*
(where our gate battery is stronger than the platform's) and consume *proofs* (where
the platform's crowd is bigger than our quota). Do not publish proofs: the platform is
immutable and kernel-graded, the refinery is neither, and a candidate published there
is a contribution we never reviewed. The whole integration is one new module and one
pack file; nothing in the cron changes until a manual spike says it should.

---

## 1. What Prove2Me actually is

Three mechanisms, of which only two matter to us.

- **Statement/proof separation + type-checked submission** (§3.1–3.2). A theorem is an
  immutable object: `preamble` + `formal_statement` ending in `:= by sorry`. A proof is
  a separate file declaring `theorem solution` whose *type* matches the target's
  exactly; the server compiles it in the target's pinned environment and rejects
  `sorry` or new axioms. Same acceptance predicate as `probe/gate.py`, including our
  `_probed_signature` statement-integrity pin, computed on someone else's hardware.
- **Proof-sketches** (§4.2). A submission may `import` other platform theorems,
  *including open ones*. A sorry-free file that discharges the parent from imported
  children is `SKETCH_ACCEPTED`; each child becomes an independent open problem, and the
  parent auto-resolves to Proved when every child closes. This is `decompose.py`'s DAG
  with a distributed prover pool attached, and it is the reason to care.
- **Audited missions + milestones** (§3.3, §4.4). Humans audit a curated core — goal,
  definitions, milestone lemmas — and nothing else; agents generate intermediate lemmas
  freely. Structurally identical to our doctrine that `formal-mathfin` issues are
  human-curated and the machine only fills them.

The paper's own honest caveat is worth carrying: Table 1 is case studies, not an
experiment. "Two explanations are confounded in every row: a stronger model generation,
and a harness built for multi-agent proving. The case study data alone cannot separate
them." We should not adopt this expecting the 151K-lines-for-$400 row to transfer.

---

## 2. What each side has that the other wants

| | foundry has | platform has |
|---|---|---|
| **statements** | depth gate · triviality · ⊢False · ⊢¬Concl · judge · `strengthen.py` — five independent elaborator/kernel-grade checks before a statement is queued | human read-back audit on a mission core only; open submission otherwise |
| **proofs** | one Lean process, one ~10 GB box, 2000 Actions-min/month | server-side verification, 100 concurrent submissions, other people's agents |
| **decomposition** | a splitter + skeleton gate that has produced 1 routable split | the sketch mechanism, plus a crowd to prove the leaves |
| **quality bar** | refinery, 8-lens, human merge | kernel acceptance, immutable |

Read the table by row and the trade is obvious in both directions. Our statement
pipeline is genuinely stronger than what the platform requires of a submitter — the
depth gate alone is the mechanism the AlphaProof Nexus team reported as
prompting-resistant (backlog §2026-07-23). Our proving *capacity* is the weakest part
of the operation and is capped by a quota we cannot buy our way out of on a private
repo.

**So: statements out, proofs in.** That asymmetry is the whole design, and it is also
the direction the platform rewards — §4.3 credits a contributor whose statements other
proofs later import.

---

## 3. The five things that must be true first

Measured, not assumed. Each is a gate on the phase plan in §5.

**(a) The environment pin does not match, and there is no v4.32.0 lane.**
`GET /environments` currently offers Lean `v4.33.1` (Mathlib `0df444a`, default),
`v4.30.0` (`c5ea003`), `v4.29.0-rc3` (`777aaa6`). We are on `leanprover/lean4:v4.32.0`
(`scout-lake/lean-toolchain`, `targets/queue/manifest.json`). Nothing we publish can be
verified against the Mathlib we actually build on. Two ways out, and the cheap one is
first: **ask** — the paper invites contact and environments are per-Mathlib-commit by
construction, so a v4.32.0 lane is a config row on their side. Otherwise bump to
v4.33.1, which by `[autoformalize]` policy drops `gate-cache.json`, `state-cache.json`
and `experience.json` wholesale. That cost is smaller than it reads: `state_cache` is
on to *measure* and its cross-target recurrence is the number that decides whether it
survives at all, and `experience`'s `retried targets` is under the same standing
threat. A pin bump is not free but it is not load-bearing either.

**(b) Every stub opens `public import Mathlib`.** All 11 queued targets do. `prove.md`
is explicit that whole-Mathlib imports "may timeout" on the server. The import closure
has to be minimized per statement before anything is published — mechanical, but it is
work, and it is work no current pipeline stage does.

**(c) The module-system header has no home in the platform payload.** Our stubs carry
`module`, `public import`, `@[expose] public section`, a license block and a `/-! … -/`
doc; the platform stores `preamble` + `formal_statement` and *silently drops a
declaration whose statement carries a leading docstring*. `domains/mathfin/target.toml
[module]` already owns exactly this preamble composition, so the transform belongs
there — but note the platform also rejects `theorem_name`s containing primes, unicode
subscripts or greek. Our identifiers are ASCII today; the refill log's
`identifier uses Σ_` rejection (issue #75, 2026-08-19) shows the hazard is live.

**(d) MathFin's definitions are not on the platform, and one dependency may not be
publishable at all.** A statement consuming `portfolioReturn`, `MathFin.zcb` or
`mertonCallPrice` needs those defs published as `Definitions.Def_*` first — that is
what `references/upload_full_project.md` is for, and it is a real project (262 MathFin
modules; skeleton-subtraction via two Lean meta-programs, not hand-splitting). Worse,
we vendor Degenne's **BrownianMotion**, which the platform's Mathlib-only environments
do not carry. **To check before Phase 1:** whether any queued statement's *type*
reaches a BrownianMotion declaration (`cal-bk-144` / `Foundations/WienerIntegralL2` is
the suspect). If it does, that family is out of scope for the platform until the
package itself is transplanted.

**(e) Immutability collides with the refinery, and our own postmortem says so.**
"Machine proofs are scouts, not authors" is a hard rule; a kernel-passing candidate is
not a contribution until the refinery rewrites it under the 8-lens bar. Prove2Me
accepts on the kernel and freezes forever — only `explanation` is patchable. Publishing
a candidate there publishes an unreviewed proof under our name, permanently. And the
statement side is no safer: **4 of 4 autoformalized theorems merged into
`formal-mathfin` asserted a hypothesis the theorem does not need, and every gate passed
them** (VeriCodeGen design §1). `strengthen.py` closed that hole in July. It must run on
every statement before publication, because on this platform there is no revising it
afterward.

---

## 4. The one thing to adopt regardless: the blind read-back

Independent of any integration, §3.3's **sub-agent read-back** is a strictly stronger
instrument than our faithfulness judge, and it costs one prompt file.

Our judge (`domains/mathfin/prompts/judge-system.md`) is handed *the issue's prose and
the candidate Lean together* and asked whether they agree. That is not blind: it can
read the intended meaning into the code, which is precisely the failure the paper cites
(Bourigault et al. find ~43% of proved statements faithful under a Lean-as-judge audit).
A read-back auditor sees **only** the Lean and its preamble — never the issue, never the
source — and renders what the declaration literally asserts, with every binder,
hypothesis and degenerate case made explicit. The comparison against the issue then
happens outside the model that wrote the rendering.

This is the same instrument class as the retired intent-fidelity gate, but pointed the
other way round, and it is aimed at the class of defect our gates demonstrably miss:
`h_entries`-style over-assumption, vacuous hypotheses, silently-narrowed conclusions.
Ship it as `domains/<name>/prompts/readback-system.md` + a second `claude -p` call in
`af_gates`, rendered into the refill history like any other gate verdict. Zero platform
dependency, and it is the prerequisite for ever captaining a mission (read-backs are a
required field on a proposal item).

---

## 5. The plan

### Phase 0 — decide, no code (this week)

1. `GET https://prove2.me/api/v1/environments` and ask about a **v4.32.0 / MathFin-pin
   lane**. One email; it changes the cost of everything below.
2. Answer (d): grep the 11 queued statement *types* for BrownianMotion reach.
3. R rules on publication scope. Uploading is *publication*, not the API traffic the
   hard rules govern — standard finance results restated in our own Lean are ours to
   publish, but the `benchmark_entry.metadata.reference` fields trace to specific
   textbook exercises, and eval-adjacent material is out by the existing rule.

### Phase 1 — the spike: two leaves, by hand (cost: ~2 statements, 0 Actions-min)

Take `cal-bk-71`'s split — `apt_zero_beta_return_constant` and
`apt_factor_insertion_step`, the only real leaves the decomposer has ever produced,
sitting in `runs/pipeline-20260903-112359-cal-bk-71-leaves/` with a skeleton the gate
passed. Publish the `MathFin.Portfolio` definitions they need, publish the two leaves as
open theorems, and submit the assembled main proof as a **proof-sketch** against
`apt_exact_factor_pricing`. Run it in a **private mission** (`make-public` later), so a
bad first upload is not a permanent public artifact.

This is the whole chain end to end — environment, defs, naming, import minimization,
`/submit-problem` → `/publish-jobs` → `/verify` → `SKETCH_ACCEPTED` — at a cost of two
statements, and it produces the first row `docs/research/ab-decomposer.md` has ever
had. **Success = `SKETCH_ACCEPTED` on the parent.** Whether a stranger's agent then
closes a leaf is the second measurement, and it is the one that decides Phase 2.

### Phase 2 — `probe/publish.py`, hooked at decompose (only if Phase 1 lands)

Keep `probe/` domain-free (`test_no_domain_leakage.py` enforces it): the endpoint base,
environment id, upload namespace and credential path go in a new
`domains/<name>/platform.toml`, read through `domain_pack.py` like everything else.

Hook the **decompose** phase, not prove. When `skeleton_gate` passes:

1. publish the leaf statements as open theorems (after `strengthen.py`, after the
   read-back, with a read-back attached);
2. submit the assembled skeleton as a proof-sketch against the parent;
3. record `theorem_id`s in the leaf manifest and poll asynchronously.

Two properties make this the right seam. The leaves are *already* the atomized,
independently-provable objects the platform's whole design is about — no new
decomposition work. And a leaf handed to the crowd costs us nothing per attempt, while
the same leaf on our own box costs a `max_turns=60` vibe session against a 2000-min
quota. Local proving continues in parallel; whoever gets there first wins, and the
parent auto-resolves either way.

Non-negotiable invariants for this module:

- **Never publish a proof of a MathFin target.** Statements and skeletons only. A
  closed leaf comes *back* as a candidate and enters the refinery like any other.
- **`strengthen.py` runs before every publish.** Immutability makes 4-of-4 permanent.
- **Fail-open, always.** A platform outage, a 429 at 100 pending, an expired token must
  degrade the tick to today's behaviour, never redden it — same doctrine as
  `experience.py`.
- Credentials in `.env` / CI secrets. Never committed, never sent anywhere but
  `prove2.me`.

### Phase 3 — pull-back and refinery

A leaf proved by a stranger's agent arrives as a **candidate**, not a contribution: it
goes through `gate.py`, then the refinery, then a human-authored PR, exactly as a
Leanstral candidate does. Attribution is the open question for R — a third-party
contributor is a co-author in a way `Co-Authored-By: Leanstral` was not, and the
platform's explanation field means the mathematical idea arrives in prose alongside the
Lean.

### Not now

**Captaining a MathFin mission** — uploading the corpus and running it as a public
formalization campaign. It is the version of this with the largest upside and it is a
project, not an integration: 262 modules through the skeleton-subtraction pipeline, a
pin bump, a human audit of every core statement, and a public commitment. Revisit when
Phase 2 has run for a month and the A/B scoreboard has rows.

---

## 6. What would make this a no

- No v4.32.0 lane **and** R declines the pin bump → the whole thing waits for the next
  Mathlib bump anyway; do §4 alone.
- Phase 1's sketch is `SKETCH_ACCEPTED` but no leaf is touched by anyone in a month →
  the crowd is not there for mathematical finance, and Phase 2's only remaining benefit
  is server-side verification we can already do locally. Stop at Phase 1 and keep the
  read-back.
- The decomposer keeps failing at the skeleton gate. It failed again on 2026-09-05
  (`cal-bk-80`, `Unknown identifier Measure` — the preamble's `open` lines are still not
  reaching the assembled skeleton, one layer under the 09-01 `target_preamble` fix).
  **The platform cannot help with a split that never elaborates.** Fixing that is
  upstream of this entire design and worth more than any of it.
