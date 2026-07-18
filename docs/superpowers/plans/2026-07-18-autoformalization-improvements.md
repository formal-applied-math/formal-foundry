# Autoformalization Improvements — Implementation Plan (v3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Phases 0–2 are execution-ready (Phase 2 opens with a short mechanics design doc, not a strategic brainstorm). Phase 3 is Phase-2-informed; Phase 4 remains **brainstorm-gated**.

**Revision note.** v1 → v2: (1) the discriminating experiment became **Phase 0** (was a buried acceptance test); (2) the decomposition loop moved to **Phase 2, on Magistral** — the general-reasoner *role* filled in-family (no new vendor/cost/traffic), model-agnostic interface, A/B'd as plain `cron` vs `decompose` (both Mistral), hard decision gate **2026-09-30** (Labs $0 retirement); (3) Phase 1 cut to trust-hardening + diagnosis, polish tail deferred; (4) the cron re-labeled **calibration + easy-harvest**, not the product, with a first-pass refinery-automation task; stale "run the cron on the vibe harness" task removed (already wired).

**Goal:** Make the mathfin-foundry pipeline the best version of what the evidence supports — an autonomous Mistral pipeline on GitHub CI (general reasoner decomposes, leaf-prover discharges) that opens ready-for-review PRs on `formal-mathfin`, with trust-hardened gates and tuning driven by the live queue — starting from the grind-history harvest (`docs/research/2026-07-18-mainrepo-grind-lessons-harvest.md`) and the ML4TP/survey research. R's only touchpoint is reviewing/editing those PRs.

**Architecture:** Two-engine Mistral stack today (Magistral specifies/judges → Leanstral proves via vibe⇄lean-lsp-mcp → kernel gate + polish → auto-PR). This plan adds: a discriminating experiment (locate the bottleneck), trust hardening at the gates, a **Magistral-driven decomposition loop** wrapped in Lean-side verification gates (skeleton-elaboration, leaf gates, recomposition), lightweight A/B telemetry (plain `cron` vs `decompose`, both Mistral), and a CI throughput substrate — all measured on the live issue queue.

**Tech stack:** Python 3.11+ stdlib (host orchestration), `pytest` (`probe/test_*.py`), Lean 4 v4.31.0 + Mathlib + BrownianMotion (pinned), Docker (verify image + lean-repl/lean-lsp), Mistral API (Magistral/Leanstral), `gh` CLI.

## Global Constraints

Every task's requirements implicitly include this section (from `CLAUDE.md`, the memory doctrine, and the values contract):

- **No Claude/assistant attribution anywhere** — commits, PR bodies, docs, CITATION. Autoform PRs may credit the prover (`Co-Authored-By: Leanstral <labs-leanstral-1-5@users.noreply.mistral.ai>`); nothing else.
- **`docs/patterns.md` lives in the MAIN repo (`formal-mathfin`) and is the shared authority for both repos.** The foundry prover live-reads it (`house_context.read_patterns`); changes there reach the prover with no foundry code change.
- **Secrets:** `MISTRAL_API_KEY` / `MAIN_PR_TOKEN` live in the MAIN repo `.env`, never logged. Never `${VAR:-fallback}` on a secret; use `${VAR:+SET}`.
- **Git:** specific `git add <path>` only — never `-A`/`.`. Never `open(f,"w")` a tracked file in a throwaway script. Commit/push only when the operator asks; branch first on `main`.
- **One Lean process locally, ever.** Daemon is the default slot occupant; flips use `docker compose stop`, never `down`; readiness = **port probe**, never `logs | grep READY`. Batch/corpus-scale work runs on the 16 GB CI runner, not the 10 GB box.
- **Never `docker compose build` locally; foundry code changes do NOT republish the image** (probe/, tools/, benchmarks/ are bind-mounted). Image rebuilds are CI-only.
- **Scout, not author.** Nothing merges to `formal-mathfin` without the human refinery + 8-lens pass. The pipeline OPENs ready-for-review PRs; it never merges.
- **Kernel-clean floor:** axioms ⊆ `{propext, Classical.choice, Quot.sound}`; forbidden tactics auto-rejected; axiom gate stays `collectAxioms ⊆ allowlist`.
- **API traffic carries only public-corpus + fresh-textbook statements** — never held-out eval content or named crown-theorem material.
- **Gates before push:** foundry — `python3 -m pytest probe/ -q` green. Main repo — rebuild then `lake build MathFin && lake lint`, regenerate `AxiomAuditGen`/`formalization.yaml` after benchmark edits, `ledger status` all-fresh.

---

## The strategic posture this plan implements

1. **Autonomous pipeline; the human reviews the PRs.** The pipeline runs unattended on GitHub CI (`pipeline.yml`, cron every 3 days): Magistral decomposes, Leanstral proves, and it opens ready-for-review PRs on `formal-mathfin`. R's only touchpoint is reviewing/editing those PRs. The cron's honest job is **calibration + easy-target harvesting**; hard targets go through the decomposition loop. (R also authors hard proofs independently — that is separate author work, not a pipeline component.)
2. **The general-reasoner role is filled by Magistral for now.** No new vendor, auth, or traffic surface; $0 until 2026-09-30; precedent is DeepSeek-Prover-V2 (decomposer = their own house general model). Expect a smaller lift than the frontier headline numbers — compensate with **hard Lean-side verification gates around every reasoner step** (a mid-tier reasoner inside a verified protocol ≈ a frontier one free-styling). The interface is model-agnostic, so a frontier eval at the 2026-09-30 gate is a config swap — not a standing second arm running now.
3. **Decision gate 2026-09-30** (Labs $0 retires): choose keep-Magistral / switch-decomposer-to-frontier / hybrid, on real-queue evidence — leaves-closed-per-target, refinery-effort-per-PR, per-arm outcomes (Phase 2's A/B scoreboard) — and the actual price sheet.
4. **Depth-coherence, not theorem count.** Target selection continues to serve the theory (bridges, towers, promotions); Phase 4's supervised sourcing makes that machine-assisted, later.

### How we know a change helped (the feedback signal)

Tuning and the decision gate read the **live queue**:
- **The obstruction-family report (Task 1.7)** — every tick, which failure class dominates (unknown-id-despite-retrieval / depth-gate / no-elaborating-draft / prover-max-rounds / gate-fail / infra-indeterminate) and its trend. A change "helped" if its target family shrinks.
- **The A/B scoreboard (Task 2.6)** — per real target, per arm (`cron` / `decompose`, both Mistral): leaves-closed, outcome, and refinery-minutes-at-merge. It answers whether decomposition earns its tokens, and feeds the decision-gate.
- **Plain queue outcomes** — pass/fail and PR-merge rate on the actual `formal-mathfin` issue backlog.
Where the field has *already measured* a lever (e.g. depth > breadth at fixed budget — Delta, Numina), apply it as a field-validated default and watch the obstruction report for regression, with a one-line revert.

---

## Phase 0 — The discriminating experiment (days; run before building anything)

**Why first:** it locates the bottleneck (machinery vs task vs model) for a few sessions' cost and decides which later phases matter most. Building infrastructure before running it is planning around the question.

**Files:** Create `docs/research/2026-07-XX-experiment-zero.md` (the report). No production code.

- [ ] **Step 0.1 — Control A (easy target through the full pipeline).** Pick or author one `status:ready` issue that is *guaranteed provable and non-trivial*: a two-step corollary combining two existing corpus lemmas (must NOT be `rfl`/`simp`-closable, or the triviality gate rightly kills it; must consume a pointer-module def, or the depth gate rightly kills it). Run a forced tick end-to-end (refill → draft+gates → vibe prove → gate → open-pr). Record: outcome, which gate (if any) killed it, tokens, wall-clock.
  - Expected if machinery is sound: a ready-for-review PR. If it dies: the failure is a located machinery bug — fix it before any other phase (use the failure log + `refill-history.jsonl`).
- [ ] **Step 0.2 — Control B (strong general agent on a stuck real target).** Take one target the pipeline has repeatedly failed (from `runs/refill-history.jsonl` families `needs_primitives`/`undraftable` — e.g. #53 or #73). One timeboxed Claude Code session (a strong-agent diagnostic) in the main repo (daemon workflow, one Lean process, work on a branch): draft the statement + prove it, using the harvest's B-class playbook. Keep or discard the branch on merit.
  - Reading: **B succeeds where the pipeline failed** ⇒ bottleneck is the model/loop layer ⇒ Phase 2 (decomposition) is the priority, confirmed. **B also fails** (genuinely needs missing primitives) ⇒ bottleneck is target feasibility ⇒ Phase 1's feasibility census + issue curation is the priority.
- [ ] **Step 0.3 — Report + branch decision.** Write `docs/research/2026-07-XX-experiment-zero.md`: both outcomes, the branch table verdict, and any located bugs (each becomes a Phase 1 task). Update this plan's phase priorities if the verdict says so.

**Acceptance:** the report exists with an explicit verdict line; Phase 1/2 priorities confirmed or reordered on evidence.

---

## Phase 1 — Trust hardening + diagnosis (execution-ready; independent of Phase 0's verdict)

Only items that protect correctness/trust or produce diagnosis signal. The polish tail is deferred (end of phase). Each task is RED→GREEN→commit against `probe/test_*.py`.

### Task 1.1 — Two `patterns.md` sections (H2/H3) · MAIN repo
**Files:** Modify `formal-mathfin/docs/patterns.md` (append two sections, verbatim from the harvest doc's ready-to-paste drafts: "Statement design (for the formalizer / drafter)" and "Repair table (compiler error → fix)").
- [ ] **1.1.1** Append both sections. They reach the foundry prover automatically via `house_context.read_patterns`.
- [ ] **1.1.2** `cd formal-mathfin && python3 -m pytest tests/test_values.py -q` → PASS (no forbidden text). Commit (main repo).

### Task 1.2 — Drafter authority: statement-design + pins (H1)
**Files:** Modify `probe/house_context.py` (add `build_drafter_prompt`), `probe/autoformalize.py` (`formalize_messages`, `intent_messages`). Tests: `probe/test_house_context.py`, `probe/test_autoformalize.py`.
- [ ] **1.2.1 (RED)** `test_drafter_prompt_has_statement_design_and_pins_not_proof_tactics`:
```python
def test_drafter_prompt_has_statement_design_and_pins_not_proof_tactics(tmp_path):
    repo = _fake_main_repo(tmp_path)  # writes lean-toolchain, lake-manifest.json,
                                      # docs/patterns.md containing a "## Statement design" section
    p = build_drafter_prompt(str(repo))
    assert "Statement design" in p and "leanprover/lean4:v4.31.0" in p
    assert "nlinarith" not in p  # prover-only tactic ladder stays out of the drafter prompt
```
- [ ] **1.2.2 (RED)** `test_formalize_messages_inject_drafter_authority`: system content of `formalize_messages(intent, "")` contains the pins line when the module-level drafter prompt is wired.
- [ ] **1.2.3** Run both → FAIL. **1.2.4 (GREEN)** Implement `build_drafter_prompt(main_repo)`: pins block (reuse `read_pins`) + the "Statement design" section sliced from `read_patterns` output (slice by header; fail-open to a curated constant if the header is absent). Prepend to `FORMALIZE_SYSTEM`/`INTENT_SYSTEM` at message-build time (not import time — the doc must be read live).
- [ ] **1.2.5** `pytest probe/ -q` → PASS. Commit.

### Task 1.3 — Deterministic repair transforms + emit pre-lint (H4)
**Files:** Modify `probe/autoformalize.py` (`_repair_hint` ~l.390; `emit_target_files` ~l.1773). Test: `probe/test_autoformalize.py`.
- [ ] **1.3.1 (RED)**
```python
def test_repair_hint_stuck_metavar_names_the_implicit():
    h = _repair_hint(["typeclass instance problem is stuck\n  IsFilteredPreBrownian X ?m P"])
    assert "explicitly" in h and "(μ :=" in h

def test_repair_hint_nnreal_misparse():
    h = _repair_hint(["failed to synthesize instance of type class\n  LE Type"])
    assert "open scoped NNReal" in h
```
- [ ] **1.3.2 (RED)** `test_emit_prelint_reorders_omit_before_docstring` (a stub with `/-- d -/\nomit hB in\ntheorem …` is emitted with `omit hB in` first) and `test_emit_prelint_rejects_sigma_identifier` (identifier containing `Σ` → assembly error the repair loop surfaces).
- [ ] **1.3.3** FAIL. **1.3.4 (GREEN)** Two new regex-keyed `_repair_hint` rules (A3, A11); pre-lint pass in `emit_target_files` (A5 reorder + `Σ`/`Π` identifier rejection); extend the unknown-identifier feedback line to suggest the pinned-source grep (A2, one sentence).
- [ ] **1.3.5** PASS; commit. *Each rule converts a burned LLM round into a deterministic fix.*

### Task 1.4 — Wedged daemon must not pass the fail-open gates (H5)
**Files:** Modify `probe/probe.py` (`daemon_check` error-dict), `probe/autoformalize.py` (`depth_rejection`, `defs_rejection`, `triviality_rejection`, `derivable_hypotheses`), `scripts/pipeline-tick.sh` (indeterminate-tick handling). Test: `probe/test_autoformalize.py`.
- [ ] **1.4.1 (RED)** `test_structural_gates_indeterminate_on_daemon_error`: with `check_fn = lambda *_a, **_kw: {"error": "daemon check did not complete: TimeoutError"}`, each gate returns an indeterminate/retryable verdict — not a pass. (Fakes take `**_kw` — standing lesson.)
- [ ] **1.4.2** FAIL (today error-dict ⇒ "no verdict" ⇒ pass). **1.4.3 (GREEN)** Add an infra-error sentinel to `daemon_check` results; each gate returns `{"verdict": "indeterminate"}` on it; `refill` treats an attempt whose gates were indeterminate as `error` (retryable), never `seeded`; the tick logs it distinctly.
- [ ] **1.4.4** PASS; commit.

### Task 1.5 — Strengthen-pass guards before it first fires (H8)
**Files:** Modify `probe/autoformalize.py` (`unused_theorem_hypotheses` ~l.1462, `strengthen_candidate` ~l.1683). Test: `probe/test_autoformalize.py`.
- [ ] **1.5.1 (RED)** `test_strengthen_keeps_sole_implicit_pin` — binder `(hBmeas : Measurable B)` is the only occurrence of implicit `{B}` ⇒ not proposed for stripping. **1.5.2 (RED)** `test_strengthen_whitelists_nonzero_binder_under_grind` — `(hA : A ≠ 0)` flagged unused but proof body contains `grind`/`nlinarith` ⇒ not stripped.
- [ ] **1.5.3** FAIL. **1.5.4 (GREEN)** Pure-parse pre-checks in `strengthen_candidate` before any strip attempt (the full re-gate remains the backstop; these avoid the wasted round / broken PR).
- [ ] **1.5.5** PASS; commit.

### Task 1.6 — Silent-failure fixes (H9)
**Files:** Modify `scripts/pipeline-tick.sh`, `scripts/open-pr.sh` (~l.141-144), `scripts/leanstral-vibe.sh` (~l.34-37). Tests: extract the rfl-guard match into a tested helper (`probe/probe_lib.py::rfl_proof_present(text) -> bool` + `test_probe_lib.py`), fixture-drive the scripts where practical.
- [ ] **1.6.1** Refill crash ≠ empty backlog: tick emits `{"seeded": [], "refill_error": true}` on refill exception and records a distinct outcome.
- [ ] **1.6.2** rfl-guard: `test_rfl_guard_catches_by_rfl_before_end_namespace` — `:= by rfl\nend MathFin` is caught (the current shell pattern misses non-EOF and `:=byrfl` variants); open-pr.sh calls the tested Python helper instead of shell globs.
- [ ] **1.6.3** vibe LSP readiness: replace the log-grep-then-proceed with a port/health probe (reuse `wait_daemon.py`'s logic); on failure, abort the run as transient (exit 4 class) instead of proceeding.
- [ ] **1.6.4** `pytest probe/ -q` PASS; commit.

### Task 1.7 — Failure-log triage: obstruction families (the primary feedback signal)
**Files:** Create `probe/obstructions.py` + `probe/test_obstructions.py` (NOT the dormant legacy `probe/triage.py`). Wire: tick prints the aggregate at end; report file `runs/obstructions-report.md` committed by the persist step.
- [ ] **1.7.1 (RED)** `test_bucketing_families`: given fixture rows from `refill-history.jsonl` + `*-summary.jsonl`, the aggregator buckets into {unknown-id-despite-retrieval, depth-gate, no-elaborating-draft, prover-max-rounds, gate-fail, infra-indeterminate} with counts and per-issue history.
- [ ] **1.7.2 (GREEN)** Implement (stdlib json/collections); render markdown with a per-tick trend line. **1.7.3** Wire into `pipeline-tick.sh` (non-fatal); commit. *This is the plan's standing feedback instrument — it names which fix the pipeline needs next, every tick, on real targets (see "How we know a change helped").*

### Task 1.8 — Feasibility census at intent time (H12)
**Files:** Modify `probe/autoformalize.py` (intent stage). Test: `probe/test_autoformalize.py`.
- [ ] **1.8.1 (RED)** `test_route_feasibility_blocks_on_missing_primitives`: an intent whose statement names constants absent from the pointer modules and from a (fixture) pin index yields `blocked_on_infra` with the missing list — recorded to refill-history, no draft attempted.
- [ ] **1.8.2 (GREEN)** `route_feasibility(intent, pointers, lookup_fn)`: count needed-but-absent primitives (lookup = scout index, fallback grep); ≥1 missing and not on the defs route ⇒ emit the distinct outcome + a suggested-defs note (feeds the defs route or a human issue comment). **1.8.3** PASS; commit. *Directly targets the #1 recorded death family (`needs_primitives`).*

### Task 1.9 — Telemetry for silent channels (H11)
**Files:** `probe/autoformalize.py` (`build_retrieve_fns`, advisory/lint rounds). Test: `test_retrieval_backend_is_recorded`.
- [ ] **1.9.1 (RED→GREEN)** Record `retrieval_backend ∈ {embedding, loogle}` per resolved unknown; add `advised_bundle`/`lint_repaired` counters to refill-history rows (they feed Task 1.7's report). Commit.

### Task 1.10 — Safe residue (5-minute trims only)
- [ ] **1.10.1** `git rm` `targets/smoke.lean`, `targets/smoke_manifest.json`, `runs/smoke-*`; prune retired #67/#88 + hand-seeding prose from `targets/queue/README.md`; fix the two stale comments (`pipeline.toml:8-10` token-sizing prose; `autoformalize.py:1814` "unused import is harmless"). `pytest probe/ -q` PASS; commit.

**Deferred tail (explicitly NOT on the critical path; do opportunistically or drop):** H6 rename cache (pin is frozen; renames bite mainly at bumps), H7 conclusion-head template retrieval (G-Research evidence says this class of scaffolding barely moves strong agents — deferred; revisit only if the obstruction report shows a retrieval-shaped failure family dominating), T3 duplicate lint layer, T5 config-drift consolidation, vacuous-premise probe (H10 — revisit when sourcing relaxes in Phase 4). **Keep (do not trim): T7** — `probe.py prove` CLI, `eval_draft.py`, `triage.py`, `contribute.sh` are deliberate calibration keeps.

**Acceptance for Phase 1:** `pytest probe/ -q` green; patterns.md sections live and reaching the prover; drafter carries statement-design + pins; H5/H8/H9 closed with tests; obstruction report generated per tick; feasibility census recording `blocked_on_infra` instead of doomed drafts.

---

## Phase 2 — The decomposition loop on Magistral (the fork, taken now)

**Posture:** general-reasoner role = Magistral for now (see strategic posture). Interface model-agnostic (`chat_fn` injection, same as everywhere else in `probe/`). Every reasoner output passes a Lean-side gate before any budget is spent downstream. Cron demoted to calibration + easy-harvest; decomposition runs on stuck/hard targets.

### Task 2.0 — Mechanics design doc (short; R reviews before 2.1+)
**Files:** Create `docs/superpowers/specs/2026-07-XX-decomposer-design.md`.
- [ ] **2.0.1** Write the design of record (this is a *mechanics* doc, not a strategic brainstorm — the strategy is fixed above): lemma-DAG JSON schema; the decomposer system prompt = the harvest's B-class playbook (spike the riskiest kernel first; recon by conclusion-head; definition-shaping so side-conditions are inherited; skeleton-with-sorries; bank green rungs; scope-fork with declared deferral) + the statement-design authority from Task 1.2; gates per step; leaf budgets; recomposition; keep-and-revise; A/B telemetry fields; the 2026-09-30 decision-gate inputs. Get R's sign-off on the doc before 2.1.

### Task 2.1 — Lemma-DAG schema + validation
**Files:** Create `probe/decompose.py`, `probe/test_decompose.py`.
- [ ] **2.1.1 (RED)**
```python
def test_dag_parse_and_validate():
    dag = parse_dag(FIXTURE_DAG_JSON)  # nodes: [{name, statement, pointers, depends_on}]
    assert [n.name for n in topo_order(dag)] == ["lemA", "lemB", "main"]

def test_dag_rejects_cycles_and_oversize():
    with pytest.raises(DagError): parse_dag(CYCLIC)
    with pytest.raises(DagError): parse_dag(too_many_leaves(MAX_LEAVES + 1))
```
- [ ] **2.1.2 (GREEN)** Implement `parse_dag`/`topo_order`/`DagError`; `MAX_LEAVES` from `pipeline.toml` (default 5 — leaves are individually short by design). Commit.

### Task 2.2 — Decomposer call (Magistral, swappable)
**Files:** `probe/decompose.py` (`draft_decomposition(target, context_pack, *, chat_fn) -> dag`), prompt constant `DECOMPOSE_SYSTEM`. Test with fake `chat_fn(**_kw)`.
- [ ] **2.2.1 (RED)** `test_draft_decomposition_returns_valid_dag_or_error`: fake chat returns the fixture JSON → valid dag; malformed reply → one re-ask round then structured failure (no infinite loop).
- [ ] **2.2.2 (GREEN)** Implement; `DECOMPOSE_SYSTEM` embeds the B-class playbook + output contract (JSON only, each leaf statement in Lean with pointers, main node's proof sketched as leaf applications). Engine = whatever `chat_fn` is injected (Magistral in production; a Claude/manual arm can inject transcripts). Commit.

### Task 2.3 — Skeleton-elaboration gate (the decomposition's kernel check)
**Files:** `probe/decompose.py` (`assemble_skeleton(dag, meta) -> lean_text`, `skeleton_gate(lean_text, *, check_fn)`). Test: `probe/test_decompose.py`.
- [ ] **2.3.1 (RED)** `test_skeleton_gate_requires_full_elaboration_with_n_sorries`: assembled file = all leaf lemmas `:= by sorry` + the main theorem proved by a term/tactic applying the leaves (NOT `sorry`); gate passes iff elaboration is clean and `sorry_count == n_leaves`; daemon infra-error ⇒ indeterminate (reuse Task 1.4's sentinel).
- [ ] **2.3.2 (GREEN)** Implement. *This is the load-bearing gate: a bad decomposition dies here for one elaboration's cost, before any leaf gets proving budget — the mid-tier-reasoner compensation.* One bounded re-decomposition round on gate failure (feedback = the elaboration errors), then structured failure. Commit.

### Task 2.4 — Leaf routing through the existing prove+gate path
**Files:** Modify `probe/build_manifest.py` (lemma-DAG support: leaves are targets with `parent` + `dag_order`), `scripts/pipeline-tick.sh` (decompose path for targets tagged `decompose` or after N failed plain attempts), `pipeline.toml` (`[decompose] enabled/max_leaves/leaf_max_turns`).
- [ ] **2.4.1 (RED)** `test_manifest_accepts_dag_leaf_targets` (parent linkage, per-leaf single-sorry stub contract unchanged). **2.4.2 (GREEN)** Implement; leaves reuse `vibe_prove.py run/gate` verbatim (they are ordinary single-sorry targets). **2.4.3** Tick wiring: on a `pass` for all leaves, proceed to 2.5; on partial, record per-leaf outcomes. Commit.

### Task 2.5 — Recompose + keep-and-revise
**Files:** `probe/decompose.py` (`recompose(dag, proved_leaves, *, check_fn)`), `scripts/open-pr.sh` (a DAG-shaped contribution places leaves + main in one module; PR body lists the DAG).
- [ ] **2.5.1 (RED)** `test_recompose_full_and_partial`: all leaves proved → assembled module (leaves with real proofs + main) passes the full gate (`gate.gate`); partial → proved leaves are BANKED as run artifacts + flagged as standalone-PR candidates if independently valuable, unproved leaves recorded as a declared remainder (`deferred`, `refs` not `closes`) — never a silent gap.
- [ ] **2.5.2 (GREEN)** Implement. Keep-and-revise: a proved leaf's statement+proof is appended to the target's context pack on the next decomposition attempt. Commit.

### Task 2.6 — A/B scoreboard + cron demotion (the decision-gate evidence)
**Files:** `probe/vibe_prove.py` / `probe/decompose.py` (summary rows gain `arm: cron|decompose`, `leaves_total/leaves_closed`, `refinery_minutes` filled at merge time by hand), `docs/research/ab-decomposer.md` (the running scoreboard, updated per tick), README/overview one-paragraph update: the cron's stated job is **calibration + easy-harvest**; hard targets go to the decomposition path.
- [ ] **2.6.1** Add the fields + the scoreboard doc with its update protocol (one row per target-attempt per arm, on real targets). **2.6.2** Docs updated. Commit. *By 2026-09-30 this table is the Mistral-vs-frontier evidence — measured on the real queue.*

### Task 2.7 — First-pass refinery punch list (the unpriced bottleneck)
**Files:** `probe/refinery_notes.py` + test; wire into `open-pr.sh` PR body (a "first-pass review" section).
- [ ] **2.7.1 (RED→GREEN)** A Magistral review call over the final candidate (prompt = the mechanical half of the 8 lenses: unused/gratuitous constructs, wrapper smell, docstring/register, obvious golf) producing a checklist the human refinery starts from. Soft — never gates. Calibration note in the prompt doc: the taste half (inspired math, architecture) stays human/Claude. Commit.

**Acceptance for Phase 2:** one real stuck target driven end-to-end through decompose→leaves→recompose (any honest outcome: full pass, partial-with-declared-remainder, or structured failure with the skeleton gate's evidence); the A/B scoreboard exists with ≥1 row per arm; the cron's demoted role documented.

---

## Phase 3 — Throughput substrate + field-evidence tuning (Phase-2-informed)

Slim — the two throughput/tuning items that survive on their own merits; both are watched via the real-queue feedback signal.

### Task 3.1 — CI verification substrate (parallel Lean REPLs) · substrate
**Files:** `probe/verify_pool.py` + `probe/test_verify_pool.py` (injected fake REPLs; N workers, env-cache reuse, recycle-on-OOM), a `workflow_dispatch` batch-verify job in `.github/workflows/` (16 GB runner).
- [ ] **3.1.1** Design note in the module docstring: the pool runs **only on CI/big boxes** — the local one-Lean-process doctrine is untouched. It exists to (a) run the decomposition loop's leaves in parallel, and (b) speed the `ledger verify --exec` corpus sweep.
- [ ] **3.1.2 (RED→GREEN)** Pool scheduling/recycling unit-tested with fakes (no real Lean). **3.1.3** Wire as the parallel backend for (a) Phase 2's leaf-proving and (b) the CI `ledger verify --exec` sweep. Commit. *This uncaps Phase 2's decomposition depth (N leaves at once) and shortens the corpus re-verify after a deep-module change.*

### Task 3.2 — Budget-shape retune toward depth (field-evidence-based)
**Files:** `pipeline.toml` (`fanout`, `repair_rounds`, `tokens_per_attempt`).
- [ ] **3.2.1** Apply the field-measured default: `fanout=2 × repair_rounds≥6` at equal total token spend (Delta/Numina both measured depth > breadth at fixed budget). One-line `pipeline.toml` change.
- [ ] **3.2.2** Watch the obstruction report (Task 1.7) and the queue outcomes over the next several ticks. If the `prover-max-rounds` family shrinks and merge-rate holds/rises, keep it; if outcomes worsen, one-line revert. Record the call + its real-queue evidence in `docs/upgrade-backlog.md`.

**Acceptance for Phase 3:** the parallel pool runs the ledger sweep on CI (not the 10 GB box) and backs Phase 2's leaves; the budget-shape decision is recorded with its real-queue evidence.

---

## Phase 4 — Flywheel remainder + faithfulness + supervised sourcing · **BRAINSTORM-GATED**

Genuinely hinges on an unmade decision: how far to relax all-human target sourcing, which makes active faithfulness load-bearing. Slimmer than earlier drafts because triage moved to Phase 1 and keep-and-revise moved to Phase 2. Remaining scope for the brainstorm:
- **Flywheel remainder:** proved-lemma promotion into the premise index beyond the parent target; exemplar accumulation from our own accepted proofs.
- **Faithfulness load-bearing:** activate disprove-and-retire + hypothesis-rejection beyond the current soft roles (backlog C) the moment any target is not R-authored; vacuous-premise probe (H10) joins here.
- **Supervised sourcing, theory-weighted:** blueprint-gap oracle (`lean_decl`-less nodes with `\uses` demand) + "prove the axioms we assume" + reduced_core-promotion candidates, ranked by architectural centrality (bridges/towers), surfaced to R for approval — connecting the pipeline to `mathematical-architecture.md`.

**Gate:** a `superpowers:brainstorming` session → spec in `docs/superpowers/specs/` → its own plan.

---

## The 2026-09-30 decision gate (standing item)

Labs $0 retires. Decide **keep-Magistral / frontier-decomposer / hybrid** on real-queue evidence: the A/B scoreboard (Task 2.6), refinery-minutes-per-merged-PR, leaves-closed-per-target per arm, and the actual price sheet. Put a reminder in `docs/upgrade-backlog.md` now; the evidence collection starts the day Phase 2 lands.

---

## Self-review

- **Review findings addressed:** engine choice surfaced as an explicit, dated decision with an evidence protocol (not inherited); discriminating experiment is Phase 0; cron demoted in writing; refinery bottleneck priced (2.7); feasibility census + failure triage pulled into Phase 1; polish tail explicitly deferred; stale R2 task removed.
- **Coverage vs sources:** harvest H1–H5, H8, H9, H11, H12 → Phase 1; H6/H7/H10/T3/T5 → deferred tail or Phase 4 with reasons; T1/T6 → 1.10; T7 kept. Survey #3/DSP → Phase 2; survey #5 (live-queue telemetry) → 1.7; D substrate → 3.1; R1 budget → 3.2; R5 triage → 1.7; ML4TP experiment → Phase 0. Backlog C/E/F/G/H: C+H10 → Phase 4; E partially superseded by existing embed retrieval; F → Phase 2 (the point of v2); G/H → Phase 4.
- **Placeholder scan:** no TBDs; every code step names its test and file; Phase 2.0's doc and Phase 4's brainstorm are the two intentional design pauses, both marked.
- **Type consistency:** `parse_dag/topo_order/DagError`, `assemble_skeleton/skeleton_gate`, `recompose`, `build_drafter_prompt`, `route_feasibility` used consistently across their producing and consuming tasks; all fakes take `**_kw`.

## Execution handoff

Order: **Phase 0 now** (days), **Phase 1 in parallel** (independent of 0's verdict), **Phase 2 after 2.0 sign-off**, **Phase 3 alongside/after Phase 2** (3.1 enables Phase 2's parallel leaves), **Phase 4 after its brainstorm**. Two execution options per task batch:
1. **Subagent-Driven (recommended)** — fresh subagent per task, task review between, broad review at the end (`superpowers:subagent-driven-development`).
2. **Inline** — `superpowers:executing-plans` with checkpoints.
