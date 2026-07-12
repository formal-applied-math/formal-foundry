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
   (missing conjuncts, wrong direction, vacuity); R's 8-lens PR review stays the
   authority on stylistic-faithfulness nuance.
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
- `roundtrip_check(issue, stub, *, chat_fn) → {consistent, note}` — magistral:
  informalize the stub → re-formalize the prose → judge the two Lean statements
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

1. **build_manifest elaboration** (kernel, ~free) — statement is well-formed, 1 `sorry`.
2. **hypothesis_rejection** (leaf-prover, small budget) — `⊢ False` from the
   hypotheses ⇒ vacuous ⇒ retire.
3. **disproof** (leaf-prover, small budget) — `⊢ ¬ Concl` ⇒ false ⇒ retire.
4. **judge_faithfulness** (magistral) — statement says what the issue asks; catches
   the *gross* semantic failures the kernel can't (missing conjunct, weaker
   restatement, wrong hypothesis).
5. **roundtrip_check** (magistral) — informalize→re-formalize agrees; a second,
   independent semantic signal.

All five pass ⇒ stage the target. Any reject ⇒ log the reason, skip the issue
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
