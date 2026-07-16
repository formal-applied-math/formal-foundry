# Embedding premise retrieval + a cheap prove probe (ulam-inspired)

Status: IMPLEMENTED 2026-07-16 (branch `feat/embedding-retrieval-prove-probe`,
8-task subagent-driven build, 203 unit tests green). Two tracks:
**A** = embedding premise retrieval; **B1** = a cheap prove probe. Explicitly
DEFERS Track B2 (goal-state best-first search) and k-candidate typecheck-filtered
self-consistency statement selection — see § Non-goals.

**Live-smoke deferral:** the embedding cache build (`scripts/build-embeddings.sh`)
and the autop-through-daemon smoke need `MISTRAL_API_KEY` / the lean-repl daemon,
which are not in the local env — deferred to a key-available run (or CI). The
feature **fails open to loogle** when the cache is absent, so an unbuilt cache is a
graceful degradation, not a regression. Vector-cache commit-vs-CI decision (§ Risks)
is likewise pending that first build.

Inspiration: the UlamAI Prover (github.com/ulamai/ulamai, ulam.ai — a "truth-first"
LLM-propose / Lean-verify scaffold). Two of its pieces are reusable for us:
`retrieve/EmbeddingRetriever` (cosine over a premises file, cached by hash) and
`search/best_first.py`'s autop fallback tactics. **No ulam code is vendored** — the
repo carries no license (all-rights-reserved by default), so this is a clean-room
reimplementation of the *design* only. Same honest-source practice as the AFP survival
work: cite the source, build in our idiom.

## Problem

We have two open weaknesses — one we have evidence for, one we have never observed.

**(a) Retrieval is pin-mismatched and pointers-scoped.** Two mechanisms exist today:
- `loogle_candidates` (`autoformalize.py:485`): reactive, fires only on
  `unknown identifier X` during formalize-repair, hits the *public* loogle index —
  which tracks a NEWER Mathlib than our pin, so its hits are unverified and often
  false.
- `extract_signatures` / the `ScoutIndex` context pack (`house_context.py:264`):
  real elaborated signatures from the committed `index/types.jsonl`, but **scoped to
  the issue's declared `-- pointers:` modules** and their dependency closure.

The logged formalize failures are premise-surfacing failures: leanstral hallucinates
or under-applies names (`unknown identifier MathFin.zcb`; `HMul ℝ (ℝ → ℝ → ℝ)` from
writing `zcb r` instead of `zcb r t T`). When the premise leanstral needs sits
*outside* the declared pointer modules, nothing surfaces it — the pointers-scoped pack
can't, and loogle only answers a name it already knows is unknown.

**(b) The prove stage is a single leanstral call we have never observed.** Every
#67-class run has failed at the formalize/gate boundary — no draw has reached the prove
stage cleanly. With **zero end-to-end autoform successes** we cannot honestly rank
bottlenecks: we have evidence the formalize boundary is *a* wall, and zero evidence
about whether proving is *also* a wall. The disciplined move is to both (i) strengthen
the evidenced formalize failure and (ii) instrument the prove stage so we finally see
it work or fail.

## Decision

Land Track A (retrieval) and Track B1 (cheap prove probe) together. A hits the
evidenced failure; B1 starts gathering prove-stage evidence at low cost.

### Corpus — reuse what is already committed

The premise corpus is the **already-committed `index/types.jsonl`** (2785 pin-accurate
elaborated declarations: `{name, module, type, docString}`, produced by `lean_scout`
via `scripts/build-index.sh`, one rebuild per pin). No new extractor is written; the
embedding index reads the same JSONL as `ScoutIndex`.

Scope note: `build-index.sh` runs `lean_scout` over **MathFin only**, so the corpus is
our 2785 MathFin signatures — not Mathlib lemma entries. Embedding retrieval therefore
surfaces *our* premises (precisely the `MathFin.zcb`-class under-application failures);
Mathlib-lemma discovery remains loogle's job. The two are complementary, not competing —
another reason loogle is kept (§ Section 2), not deleted.

### Section 1 — embedding index

- `mistral_embed(texts, *, api_key, model="mistral-embed") -> list[list[float]]`: a
  stdlib-`urllib` mirror of `mistral_chat` (`probe/probe.py:44`) hitting
  `https://api.mistral.ai/v1/embeddings`, batched over the input list. (`codestral-embed`,
  Mistral's code-specialized embedder, is a build-time upgrade to A/B — default stays
  `mistral-embed`.)
- `EmbeddingIndex`: embeds each premise as `"{name} : {type}"` (docString optionally
  appended), caches the vectors to disk keyed by `(embed_model, sha256(corpus_text))`
  so a pin rebuild or model change invalidates. Cosine ranking is pure stdlib
  (2785 × ~1024-dim dot products per query is sub-50ms — no numpy).
- Built once per pin by a `scripts/build-embeddings.sh` (host-side HTTP, **no Lean
  process** — orthogonal to the daemon slot), analogous to `build-index.sh`. The vector
  cache is committed alongside `index/` (deterministic per pin, keeps ticks
  self-contained) OR CI-built if it proves too large — decided in the plan. Per-tick cost
  is one query embedding + a local rank.

### Section 2 — `retrieve_fn` upgrade + proactive injection

- `embedding_retrieve_fn(query) -> str`: cosine-ranks the **whole** 2785-premise index,
  returns the top-k as `name : type` lines. **Drop-in** for the existing reactive seam
  (`formalize_with_repair` at `autoformalize.py:569–573`) — same `(str) -> str` shape as
  `loogle_candidates`.
- **Proactive:** also rank against the intent's statement and inject the top-k into the
  *initial* formalize grounding — retrieval's real value is preventing the wrong/partial
  name up front, not only repairing it after it errors.
- **Complements, does not replace** `extract_signatures`: the pointers-scoped pack stays
  the structured in-scope grounding; embedding retrieval adds *semantic reach beyond the
  declared pointers*.
- Config: `retrieval_backend: "embedding" | "loogle"` (default `embedding`),
  `retrieval_k`. Falls back to `loogle` when the index or embeddings are absent —
  fails-open, like the depth/faithfulness gates.

### Section 3 — cheap prove probe (Track B1)

At the proof-filling stage, around the leanstral prove call:
- **Autop fallback menu** — `simp`, `ring_nf`, `linarith`, `nlinarith`, `aesop`,
  `norm_num`, `grind` — tried as candidate *whole-proof scripts*, elaborated through the
  **existing file-elaboration daemon** (no goal-state stepping infra). First script that
  closes the goal (0 sorries, no errors) wins.
- **Retrieval-into-prove:** inject the top-k embedding-retrieved premises into the
  leanstral prove prompt too. (Tactic exemplars via `ScoutIndex.tactic_exemplars` are a
  further nice-to-have but need a `SCOUT_TACTICS=1` index rebuild — currently absent —
  so deferred.)
- **Instrumentation:** every path's outcome (which script/model closed it, or all
  failed) is logged to the JSONL trace. This is how we finally *observe* whether proving
  is a wall.
- **Values gate:** an autop-closed proof is a **scout**, not an author. It is
  provenance-tagged (`proof_source: "autop-<tactic>"`) for the mandatory
  refactor-to-conceptually-right pass and **never auto-merged as the final proof**. A
  green kernel from a 1-line `nlinarith` is a lead to the real proof, not the deliverable.

## Data flow

```
build (once per pin, host-side):  types.jsonl ──mistral_embed──▶ vector cache
per tick:  issue
  └▶ draft_intent (magistral)
  └▶ formalize_with_repair (leanstral)
        proactive: top-k embedding premises in the initial grounding
        reactive:  embedding_retrieve_fn(unknown-id) in repair feedback
  └▶ depth / hypothesis / disproof / judge / fidelity gates
  └▶ stage
  └▶ prove probe:  autop menu (daemon) ∥ leanstral prove (+ top-k premises)
        └▶ trace outcome; autop win ⇒ scout-tagged
```

## Constraints honoured

- **Memory doctrine:** embedding build + query are host-side HTTP (no Lean process); the
  prove probe reuses the one existing daemon. No second Lean-loaded process anywhere.
- **Privacy:** embeddings go to `api.mistral.ai` (our existing provider/key), never a
  cloud premise-selector; local cosine ranking. Consistent with the "retrieval is local,
  never leansearch/cloud" rule.
- **Provenance:** our own code; ulam cited as design inspiration only; no vendored code.

## Testing (TDD)

Pure functions first, all with injected `embed_fn` / `check_fn` (no network, no daemon
in unit tests):
- cosine ranking + top-k ordering on hand-built vectors;
- cache key = `(model, sha256(corpus))`; stale-on-change; hit/miss;
- premise formatting (`name : type` lines, k-truncation);
- `embedding_retrieve_fn` drop-in parity with the `loogle_candidates` `(str)->str` shape;
- proactive grounding injection (top-k present in the initial formalize messages);
- backend selection + loogle fallback when index absent (fails-open);
- autop menu assembly (each tactic → a well-formed whole-proof script);
- scout tagging (autop win ⇒ `proof_source` set; leanstral win ⇒ not).
Live smoke separately: build the vector cache, one real retrieval, one real prove probe.

## Non-goals (deferred, with reasons)

- **Track B2 — goal-state best-first search** (ulam `best_first.py`: priority queue by
  proof length, transposition table, beam). Needs tactic-level goal stepping exposed
  through the daemon (lean-interact proofState/tactic mode) — a real infra lift. Deferred
  until B1 gives evidence that single-shot + autop + retrieval is insufficient. Building a
  search engine before observing the prove stage would repeat the ranking-without-evidence
  mistake.
- **k-candidate typecheck-filtered self-consistency at the statement level** (Poiroux
  2024, the research direction ulam's paper itself points to). This targets our evidenced
  statement-quality variance and is the strongest *next* candidate — but it is a separate
  change to the formalize stage, kept out of this spec to keep it focused.
- **Lemma-first planning / expansion** (ulam `formalize/*`): out of scope.

## Risks / open questions (resolve in the plan or at build time)

- **Embed model choice** — `mistral-embed` vs `codestral-embed` for Lean/math
  signatures; A/B at build time, default `mistral-embed`.
- **Vector cache: commit vs CI-build** — ~11–30 MB derived, deterministic per pin.
  Committing keeps ticks self-contained (like `types.jsonl` is committed); decide in the
  plan.
- **Free-tier embedding limits** — one-time corpus embed is ~batched requests; per-tick
  is one query embed. Should sit well under limits; confirm at live smoke.
- **Autop noise** — the menu may close vacuous or trivially-restated goals; the existing
  hypothesis(⊢False)/disproof/vacuity gates upstream already guard the *statement*, and
  the scout tag guards the *proof* from silent merge.
