# Issue → stub autoformalizer (the self-feeding refill phase)

**Status:** design ratified (R, 2026-07-12) — reopens the "one strategic fork"
in `docs/upgrade-backlog.md` and builds the parked issue→stub sub-probe.

## Goal

Make the hands-off pipeline **self-feeding**: today the 3-day cron *drains* a
queue that a human *fills* by hand-writing stubs. This adds the missing half — a
sub-probe that turns the next `status:ready`+`type:proof` GitHub issue into a
*validated* queue target (stub `.lean` + `.entry.json` + manifest row), so the
existing prover always has something to prove. After this, a new issue R files
becomes a ready-for-review PR with **no manual step** — R reviews only at merge.

This reverses the parking decision in `upgrade-backlog.md` §F/G ("no general
reasoner now"): we add a **second engine** — a Mistral *general reasoner* above
the Leanstral leaf-prover — because drafting a faithful statement from prose,
judging NL-equivalence, and the roundtrip check are not proving tasks.

## Decisions (R)

1. **Reasoner placement:** a Mistral general model **inside the CI tick** (not a
   separate seeder, not a scheduled Claude run). Self-contained on the runner,
   reuses `MISTRAL_API_KEY`, no new secret.
2. **Model:** **`magistral-medium`** (Mistral's reasoning model) for draft +
   judge + roundtrip; **`labs-leanstral`** for the kernel gates + the proof.
   Both reachable on R's free tier (validated live 2026-07-12: a faithful #85
   draft in 402+5396 tok/127 s; the judge cleanly separated a full 3-principle
   draft from a 1-of-3 restatement). Billing is R's free-tier assertion — the
   API shows access, not price; **one-line fallback to labs-only** if a bill appears.
3. **Faithfulness:** the full suite — **kernel gates (hypothesis-rejection +
   disproof) + semantic judge + roundtrip**. The judge catches *gross* failures
   in what a statement asserts (a stated fact wrong/weaker/vacuous, wrong
   direction, or a *silently* missing conjunct — an undeclared gap). **Honest
   subsetting is allowed:** the drafter may formalize a coherent SUBSET of a
   multi-part issue and DECLARE the remainder (json `deferred` → a `-- deferred:`
   header → a "suggested follow-up issues" section in the PR body, and `refs`
   rather than `closes` so a subset never auto-closes its parent). A *declared*
   subset is not a failure; only an undeclared gap is. R's 8-lens PR review stays
   the authority on stylistic-faithfulness nuance and opens the follow-ups.
4. **Integration:** **refill-at-start-of-tick** (Approach 1). One workflow,
   one self-feeding loop, reusing the daemon the tick already boots.

## Architecture

New module **`probe/autoformalize.py`**, invoked as a **refill phase** prepended
to `pipeline-tick.sh` before the existing plan → prove → PR flow:

```
refill-if-empty:  next ready issue → draft → gates → judge → roundtrip
                  → emit stub+entry+headers → build_manifest validate → stage
   → plan  →  prove  →  open PR              (existing flow, unchanged)
```

Two engines, unchanged division of labor:
- **`magistral-medium`** (reasoner) — `draft_stub`, `judge_faithfulness`, `roundtrip_check`.
- **`labs-leanstral`** (leaf prover) — the two kernel gates *and* the proof step.

**Independence preserved:** the sub-probe *reads* formal-mathfin issues
(`issues.py`) + the live repo for context packs (`house_context`) and *writes*
only to the foundry queue. The main repo is touched solely by the existing
`open-pr.sh` PR path. Memory doctrine holds: one Lean process — the sub-probe
reuses the tick's daemon; magistral runs on Mistral's servers.

## Components — `probe/autoformalize.py`

Pure orchestration + model-mediated steps, each independently testable with
injected `chat_fn`/`check_fn`. Reuses `mistral_chat`, `daemon_check`,
`run_target`, `extract_lean_code`, `slop_report`, `normalize_content` (probe.py);
`build_system_prompt`, `extract_signatures` (house_context); `select_issues`
(issues.py); `parse_meta`, `load_entry`, `sha256_hex` (build_manifest/probe_lib).

- `select_next_issue(raw, state, queued_ids) → issue|None` — `issues.select_issues`
  minus ids already attempted/queued. Pure.
- `draft_stub(issue, context_pack, pins, *, chat_fn) → (stub, meta)` — magistral.
  Prompt = issue Task+Pointers + context pack + pins + the stub-format contract →
  a ```lean block (`theorem NAME <binders> : Concl := by sorry`) + a ```json block
  `{module_name, benchmark_id, docstring}`. Theorem name parsed from the lean block.
- `split_statement(stub) → (name, binders, concl)` — deterministic: scan the header
  after the name tracking `(){}[]` depth; the first depth-0 `:` splits binders from
  `Concl` (stop `Concl` at the depth-0 `:=`). Daemon-elaboration fallback if the
  scan is ambiguous. Pure + unit-tested on tricky binders (subscripts, `: ℝ` inside
  a group, a `↔`/`:`-bearing Concl).
- `hypothesis_rejection(stub, ctx, *, chat_fn, check_fn) → bool` — build
  `theorem NAME_vac <binders> : False := by sorry` in the stub's import/open context,
  short pass@k via `run_target` (small budget); a clean proof ⇒ contradictory
  hypotheses ⇒ **retire (vacuous)**.
- `disproof(stub, ctx, *, chat_fn, check_fn) → bool` — build
  `theorem NAME_neg <binders> : ¬ (Concl) := by sorry`, short prove; a clean proof
  ⇒ **retire (false-as-written)**.
- `judge_faithfulness(issue, stub, *, chat_fn) → {faithful, verdict, issues}` —
  magistral, JSON verdict. `faithful=false` ⇒ reject.
- `roundtrip_check(issue, stub, *, reason_fn, prove_fn) → {faithful, tokens}` —
  CROSS-MODEL: magistral (`reason_fn`) informalizes → **Leanstral** (`prove_fn`)
  independently re-formalizes → magistral compares the two Lean statements
  agree. `consistent=false` ⇒ reject.
- `emit_target_files(issue, stub, meta) → (lean_text, entry_json, placement)` —
  **mechanical, no model** (see next section).
- `refill(main_repo, raw_issues, state, *, chat_fn, check_fn, budget, max_issues=1)
  → [seeded_ids]` — orchestrator loop: per candidate issue, context-pack → draft →
  kernel gates → judge → roundtrip → on all-pass emit + `build_manifest` validate +
  stage; on any reject, log the reason and skip to the next issue; stop after
  `max_issues` committed or the attempt budget is spent.

## Placement & mechanical emit

`emit_target_files` assembles the target with **no model call** (deterministic,
fully unit-testable):

- **Stub** `cal-bk-<N>.lean` — license header, `module`, `public import Mathlib`,
  the placement comment headers, the `meta.docstring` as a `/-! … -/` doc,
  `@[expose] public section`, `namespace MathFin`, the drafted theorem (with its
  `sorry`), `end MathFin`. Stream `bk` (backlog); `<N>` = the issue number.
- **Placement headers** — `source-issue: N` (the issue); `benchmark:
  benchmarks/<file>.json` from a `domain → file` map (finance → `mathematical_finance`);
  `main-module: MathFin/<Section>/<module_name>.lean` where `Section` comes from an
  `area-label → subdir` map (`fixed-income→FixedIncome`, `actuarial→Actuarial`,
  `fx→FX` (new dir; umbrella import + lake build absorb it), …) and `module_name`
  from `meta`; `benchmark-id: <meta.benchmark_id>` (`mf-<area>-<slug>`);
  `pointers:` from the issue's Pointers section.
- **Re-export `.entry.json`** — mechanical from the parsed signature: `import
  MathFin.<Section>.<module_name>` + `open MathFin` + the docstring + `theorem
  mf_<area>_<slug> <binders> : <Concl> := MathFin.<name> <explicit-arg-names>`,
  where the explicit args are the parenthesized binder names from `split_statement`.
  `metadata.formalization_status = "full"`, `metadata.provenance = {statement_source:
  "magistral-autoform", statement_model: "magistral-medium", source: "leanstral-autoform",
  model: "labs-leanstral-1-5", issue: N}`.
- Validation: rebuild `targets/queue/manifest.json` via `build_manifest` (its
  `daemon_check` confirms the stub elaborates with exactly one `sorry`); a stub that
  does not elaborate is rejected (skip to the next issue), never staged.

## Faithfulness gates — order & semantics

Run in increasing cost, cheapest-rejects-first:

1. **draft_with_repair elaboration** (kernel, ~free) — the drafted statement is
   well-formed: **empty `errors` and exactly one `sorry`** (gate on `errors`, NOT the
   daemon's `success` — a valid stub whose `sorry` remains reports `success=False`;
   this bit us, see Build log). On a Lean error, feed it back (+ a `^`-not-`²` hint)
   and re-draft up to `draft_rounds` (default 2) times — the compiler-feedback lever.
2. **depth_rejection** (elaborator, ~free — pointers-scoped, option B) — a `run_cmd`
   meta block requires the theorem's TYPE to USE ≥1 constant DEFINED in one of the
   issue's `-- pointers:` MathFin modules (`ci.type.getUsedConstants` attributed via
   `env.getModuleIdxFor?`). None ⇒ `throwError "depth-gate: …"` ⇒ shallow ⇒ skip. This
   catches the true-but-shallow class the kernel gates pass — a Mathlib identity in
   domain clothing (cal-bk-53 = `integral_add_compl`; cal-bk-67 inlined
   `F = (P₁/P₂−1)/δ` over raw reals instead of consuming `MathFin.zcb`). With NO
   pointers the gate is inapplicable and SKIPS (a missing Pointers section is a metadata
   gap, not a shallowness verdict; the stub carries no MathFin import to consume anyway).
   Fails OPEN on a daemon-communication error (keys on the `depth-gate:` marker, not any
   error). Config `[autoformalize].depth_gate` (default on).
3. **hypothesis_rejection** (leaf-prover, small budget) — `⊢ False` from the
   hypotheses ⇒ vacuous ⇒ retire.
4. **disproof** (leaf-prover, small budget) — `⊢ ¬ Concl` ⇒ false ⇒ retire.
5. **judge_faithfulness** (magistral) — statement says what the issue asks; catches
   the *gross* semantic failures the kernel can't (missing conjunct, weaker
   restatement, wrong hypothesis).
6. **roundtrip_check** (CROSS-MODEL back-translation) — magistral informalizes the
   draft → **Leanstral independently re-formalizes** the prose → magistral compares
   the two Lean statements. The independence (a different model re-formalizes) makes it
   a genuine consistency cross-check, not a self-check; soft + lenient (rejects only on
   an explicit divergence; a failed Leanstral re-formalize is inconclusive, not a reject).

All gates pass ⇒ stage the target. Any reject ⇒ log the reason, skip the issue
(it stays `status:ready` on GitHub — never auto-closed on a reject — so R or a
later tick can revisit). The gates are a **safety net on a machine-authored
statement**; R's 8-lens PR review remains the final faithfulness authority.

## Failure handling & budget

- **Skip-to-next, bounded:** `refill` tries up to `max_attempt_issues` (config,
  default 3) candidate issues per tick; the first to pass all gates is staged and
  the loop stops. If none pass, the tick logs `refill: no faithful stub` and falls
  through to prove any already-queued target (or no-ops, exactly as today).
- **No red PRs:** unchanged — the existing green-or-abort in `open-pr.sh` still
  files an `autoform-blocked` issue rather than open a failing PR. The refill only
  adds targets; it never touches main.
- **Budget:** magistral draft/judge/roundtrip tokens are charged to the same
  monthly ledger via `pipeline.py record` (a new `statement_tokens` field, summed
  into `tokens_spent_this_month`). Gate-prove tokens (leaf-prover) likewise. A
  refill budget cap (config) bounds a runaway drafting loop.
- **Rate limits:** 1 issue / 3 days × ≤ (3 attempts × ~5 magistral calls) is far
  inside any free-tier limit; `mistral_chat` already backs off on 429.

## Config (`pipeline.toml`) & provenance

New `[autoformalize]` block: `enabled` (bool, default true), `draft_model`
(`magistral-medium-latest`), `max_attempt_issues` (3), `gate_budget` (20_000,
the per-gate leaf-prover cap), `draft_max_tokens` (8_000). Provenance:
`formalization.yaml`'s generator already counts `provenance.source ==
leanstral-autoform`; extend the disclosure to also surface `statement_source ==
magistral-autoform` so the automation note stays mechanically honest ("statement
drafted by Magistral, proof by Leanstral, human-reviewed at merge").

## Shell / workflow wiring

- `pipeline-tick.sh`: a new step before "Plan" — if `manifest.json` has no
  unattempted target (or is absent), run
  `python3 autoformalize.py refill --main-repo "$MAIN" --budget "$REFILL_BUDGET"`;
  it needs the daemon (already up) + `MISTRAL_API_KEY` (already sourced).
- `.github/workflows/pipeline.yml`: the final "Persist state" commit extends its
  `git add` to `targets/queue/` so a newly-seeded stub+entry+manifest is committed
  back (with `[skip ci]`), same as `pipeline_state.json`.
- No new secret, no new job, no second daemon.

## Testing

Pure logic unit-tested with injected `chat_fn`/`check_fn` + temp trees (no Lean,
no API, no network), matching the existing `test_*.py` style:
- `test_autoformalize.py`: `select_next_issue` filtering; `split_statement` on
  tricky binders; `draft_stub`/`judge`/`roundtrip` parsing of canned magistral
  replies (incl. reasoning-block content); the kernel-gate goal construction
  strings; `emit_target_files` producing a byte-exact stub + entry on a temp tree;
  `refill` orchestration (a stub-`chat_fn` that returns a good draft → staged; one
  that returns a vacuous/weaker draft → skipped) — asserting the gate order and
  skip-to-next. Target: keep the suite green (74 → ~90+).
- **Live smoke** (daemon + magistral up, one manual run): `refill` on a real
  ready issue end-to-end → a staged, elaborating stub — before enabling in CI.

## Phasing

1. `split_statement` + `emit_target_files` + their tests (pure; no model).
2. `draft_stub` + `judge` + `roundtrip` + kernel-gate builders + tests (injected `chat_fn`).
3. `refill` orchestrator + tests; `pipeline.toml` block; provenance/formalization.yaml extension.
4. Shell/workflow wiring; live smoke; enable `[autoformalize].enabled`.

## Safety envelope

- Reversible: `[autoformalize].enabled=false` reverts to hand-seeded queue; labs-only
  fallback is a model-string change.
- The statement is machine-authored but **gated 5 ways + human-reviewed at merge**;
  a rejected issue is never auto-closed.
- No new credential; main-repo independence unchanged; one Lean process preserved.

## Build log (2026-07-12) — built + validated; yield-tuning is open

Implemented Phases 1–5 TDD (120 tests). Phase 5 (`draft_with_repair`, a
compiler-feedback loop on the draft + a `^`-not-`²` hygiene rule) was added after
the live smokes; `draft_stub` was subsumed and removed. The **live smoke earned its
keep — it caught five things unit tests structurally could not:**

1. the `if __name__ == "__main__"` guard landed BEFORE the `emit_target_files` defs
   → `NameError` when run as a script (import-only tests skip the guard). Moved to EOF.
2. the elaboration gate keyed on the daemon's `success`, which is `False` for a
   valid stub whose `sorry` remains → every valid draft was rejected. Gate on
   `errors` (like `build_manifest`).
3. `mistral_chat` didn't retry an empty/truncated response body (free tier under
   load) → now retried.
4. `daemon_check` raised `JSONDecodeError` on a degraded-daemon reply → now returns
   an error dict so the repair loop retries.
5. the judge over-enforced formality (rejected a correct #85 draft for abstracting
   `E[X]` as a real) → recalibrated to a gross-failure net; drafter now names
   defined quantities explicitly.

**Validated end-to-end:** a full cascade run (draft → elaborate → kernel gates →
judge, ~45k tokens) exercised every stage correctly. The pipeline is **safe by
construction** — it stages only a draft that elaborates AND passes all gates, and
skips transient failures without crashing.

**On self-reference + PR status (do NOT overclaim):** the *independent*, rigorous
checks are the Lean kernel (elaboration), the Leanstral probes (a different model +
the kernel), the full `lake build` + axiom-clean, and **R's merge review**. The
Magistral **judge is a soft SELF-check** (magistral grading its own draft), useful as
a budget pre-filter, NOT a faithfulness guarantee. The **roundtrip is now CROSS-MODEL**
(R's call 2026-07-12): magistral informalizes the draft → **Leanstral independently
re-formalizes** the prose → magistral compares — a genuine back-translation cross-check
(a different model does the critical re-formalize), the research-ideal form, though
still soft + `[unverified]` per the survey (efficacy TBD; Leanstral's statement-
autoformalization ability is the live unknown). An opened
autoform PR is a **proposal** that passed CI and is *unmerged* (and can go
stale/conflicting as `main` moves) — never proof of quality. The merge, with
changes, is the bar. Do not few-shot the drafter on the pipeline's own unmerged
output (a self-referential trap — reverted 2026-07-12).

**Open (yield, not correctness):** across local smokes the free tier throttled hard
(empty bodies) and the judge↔draft calibration on a few issues is still finding its
level, so no stub *staged* locally yet. Getting a first stage is a tuning matter
(calibration is R's faithfulness-bar call; the free-tier flakiness is now
retry-hardened) — the machinery, gates, and safety are proven. Deployment note:
the refill's `gh issue list` needs **issues:read** on `formal-mathfin`; the
`MAIN_PR_TOKEN` PAT is currently Contents+PRs only.

## Robustness pass (2026-07-12) — the yield-0 blocker was infra, not calibration

The "runs keep timing out" symptom (local `--only 67`, CI #53 exit-143), earlier filed
under *free-tier throttle / daemon-OOM-under-load*, had a single concrete root cause found
by a systematic dig: **the lean-repl daemon had no elaboration timeout.** lean-interact's
`DEFAULT_TIMEOUT` is `None`, and `tools/verify/lean_backend.py::run_raw` called
`server.run(Command(cmd=code))` with no `timeout`, so the REPL read (`t.join(None)`)
**blocked forever** on a spinning tactic. A leanstral kernel-gate candidate running
`nlinarith` on an unprovable `⊢ False` never replies → the daemon wedges holding its lock
→ the only thing that ever fires is the client's own 300s socket timeout. Non-deterministic
(leanstral's tactic choice is stochastic), which is exactly why a draft-only diagnostic
always passed while the full cascade hung.

Three fixes, each TDD'd:

1. **Bounded elaboration / recycle-on-stuck** (`tools/verify/lean_backend.py`, main repo):
   `run_raw` now passes `timeout=LEAN_ELAB_TIMEOUT` (default 180s). lean-interact kills the
   stuck REPL on timeout; the next request respawns a fresh one. A timeout is surfaced as a
   clean elaboration failure and is **not** retried (the same code just re-spins). This is
   the meaningful form of "daemon recycling." `tests/test_lean_backend.py` verifies it with
   a fake server (no Lean boot): a finite timeout is passed, and a `TimeoutError` surfaces
   as a single non-retried failure.
2. **Client defense-in-depth** (`probe/probe.py`): `daemon_check` catches
   `socket.timeout`/`OSError` and returns a failed-check dict — a wedged or respawning
   daemon degrades to a failed candidate, never an uncaught exception that skips the issue.
3. **Lightened gates** (`probe/autoformalize.py`): the two faithfulness gates drop from
   fanout-2 × 2-rounds (~8 daemon checks/issue) to pass@1 / single round (`_GATE_FANOUT=1`,
   `_GATE_ROUNDS=1`) → 2 checks. They are a cheapest-first safety net, not proofs to
   maximize; a subtle vacuity is left to the judge + human merge. Per-issue daemon load
   ~9 → ~3 checks.

**Clean `--only 67` run on a fresh daemon: EXIT 0, 712s wall, zero hang.** The full
cascade ran to completion; the wedge is structurally gone. Outcome: `#67 retired —
vacuous`. This is **not** a false positive: a gate "pass" requires an axiom-clean,
sorry-free proof of `⊢ False` (the axiom guard), so by Lean's consistency the draft's
hypotheses were genuinely contradictory (magistral over-constrained the statement). The
safety net worked. Net: the infrastructure is fixed; the remaining zero-stage state is now
a **draft-quality** matter (re-draft luck / a broader ready-issue sweep), not a hang.

**CI deploy gotcha:** the CI daemon (`pipeline.yml`, `docker run`) uses the **baked image**,
not a `tools/` bind-mount, and `publish-image.yml`'s trigger paths **exclude `tools/`**
(it is bind-mounted locally). So Fix 1 reaches the CI daemon **only** via a manual
`gh workflow run publish-image.yml` after pushing `lean_backend.py`; a local daemon restart
suffices locally.

## Depth gate (2026-07-13) — the yield ceiling was modeling depth, and it is now gated

The robustness pass fixed the infra; the first proved stage (`cal-bk-67`,
`fra_value_zero_iff_fair`, leanstral-proved axiom-clean) then exposed the *real* ceiling:
the statement was true, non-vacuous, faithful-to-structure — and **shallow**. Stripped of
FRA vocabulary it is `δ·P₂·(F−K) = 0 ↔ K = F` (a nonzero-factor cancellation) over opaque
reals `P₁ P₂ : ℝ`, and it **inlined** the forward rate `F := (P₁/P₂−1)/δ` as a `let` instead
of **consuming** `MathFin.zcb` / the ForwardRate lemmas its own pointers name. Every kernel
and judge gate passed because none of them measures whether the statement *engages the
domain*. R rejected it (as with cal-bk-53 = `integral_add_compl` in barrier clothing).

The fix (option B — pointers-scoped, R's call): a **structural depth gate**, first among
the gates. It is elaborator-grounded, not an LLM judge (rigorous-vs-soft rule): the probe is
`lean_text` + a `run_cmd` meta block that looks the decl up (`env.find? \`MathFin.<name>`),
takes `ci.type.getUsedConstants`, attributes each via `env.getModuleIdxFor?` →
`env.header.moduleNames[i.toNat]!`, and `throwError "depth-gate: …"` unless ≥1 constant is
defined in a `-- pointers:` module. `depth_rejection` keys on the `depth-gate:` marker so it
fails **open** on a daemon-communication error, and **skips** entirely when the issue has no
pointers (a metadata gap, not a shallowness verdict). Config `[autoformalize].depth_gate`
(default on); CLI `--depth-gate/--no-depth-gate`.

Validated: 8 new unit tests (probe construction, reject/pass/fail-open/skip, refill wiring,
config) + the whole 146-test suite green; and live against the daemon — the Python-emitted
probe **rejects** the staged cal-bk-67 (`depth-gate: … consumes no def from pointer modules`)
and **passes** a variant that consumes `MathFin.zcb`. The Lean meta API was locked against
the live daemon before writing the Python (the elaborated term confirmed `ModuleIdx.toNat` +
`getModuleIdxFor?`). Known limit: pointers-scoped only — a deep statement consuming a MathFin
def from a *non-pointer* module would be rejected; that is the deliberate strictness of
option B, and the issue stays `status:ready` for a human, never auto-closed.
