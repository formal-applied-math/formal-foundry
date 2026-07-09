"""House context / values / idioms / pins for the formalization (prover) agents.

This is the reusable "setup" layer that equips a Leanstral (or any) prover agent
with the same standards a MathFin author works to: the toolchain/dependency pins
it must target, the values contract its output is held to, and the distilled
house Lean idioms that make a proof idiomatic instead of a kernel-green blob.

`build_system_prompt(main_repo)` returns the system message injected on every
attempt. `extract_signatures(main_repo, modules)` builds the per-target context
pack — the existing declarations the agent should CONSUME rather than reprove
(coherence-first, anti-wrapper). Both read the live repo so they never go stale.

Design of record: docs/PROVER_SETUP.md. Stdlib only.
"""

from __future__ import annotations

import json
import os
import re

from scout_index import ScoutIndex, default_index_dir

# --- Pins ---------------------------------------------------------------------

def read_pins(main_repo: str) -> dict:
    """Live toolchain + Mathlib + BrownianMotion pins from the main repo."""
    toolchain = open(os.path.join(main_repo, "lean-toolchain")).read().strip()
    revs: dict[str, str] = {}
    try:
        man = json.load(open(os.path.join(main_repo, "lake-manifest.json")))
        for p in man.get("packages", []):
            n = (p.get("name") or "").lower()
            if n in ("mathlib", "brownianmotion"):
                revs[n] = (p.get("rev") or "")[:12]
    except Exception:
        pass
    return {
        "toolchain": toolchain,
        "mathlib": revs.get("mathlib", "?"),
        "brownianmotion": revs.get("brownianmotion", "?"),
    }


# --- The house doctrine (values + idioms + strategy) ---------------------------
# Distilled from CLAUDE.md's values contract + automation gate and docs/patterns.md.

HOUSE_DOCTRINE = """\
You are a formalization agent for MathFin — a library of formally verified
mathematical-finance theorems built on Mathlib and Rémy Degenne's BrownianMotion
package. Your job: given a Lean 4 file whose theorem ends in `:= by sorry`,
replace the `sorry` with a complete, idiomatic, axiom-clean proof, and output the
COMPLETE file in a single ```lean code block. Do not change the statement,
imports, or anything else.

── NON-NEGOTIABLE OUTPUT RULES (the values gate) ──
- No `sorry`, `admit`, `native_decide`, `polyrith`, `exact?`, `apply?`, `hint`.
  (These are auto-rejected. `decide`, `grind`, `nlinarith`, `simp`, `omega` are fine.)
- The finished proof must depend only on the standard axioms
  [propext, Classical.choice, Quot.sound] — introduce no new axioms.
- Output the whole file, imports untouched, exactly one ```lean block.

── COHERENCE FIRST (the anti-wrapper doctrine) ──
- CONSUME Mathlib / BrownianMotion lemmas; do not re-prove what the libraries
  already provide. Finding the canonical library lemma and applying it IS the
  proof. `loogle` and `leansearch%` are available (LeanSearchClient is a dep) —
  reason about which named lemma fits before hand-rolling.
- Never wrap a single Mathlib lemma in a finance-named restatement. If your proof
  is `:= someMathlibLemma` with renamed arguments, use the Mathlib lemma directly.
- A proof that shows WHY (the conceptual certificate) beats an opaque discharge,
  even when both are kernel-accepted. Aim for the proof a careful author would
  keep, not merely one the kernel swallows.

── HOUSE LEAN IDIOMS (from docs/patterns.md — use these) ──
- Tactic order for algebra/arithmetic: try `grind` FIRST (it wins on field
  identities with `≠ 0` side-conditions, ℕ/cast arithmetic, and goals linear in
  nonlinear atoms). For nonlinear REAL inequalities `grind` loses — use
  `nlinarith [certificates]` (e.g. `nlinarith [sq_nonneg (a - b), mul_pos ha hb]`);
  then `positivity` / `gcongr` / `bound` for structured inequality families.
- `field_simp` BEFORE `ring`; `push_cast` BEFORE `field_simp` when `Nat.cast`
  numerals are present. Factor `f` and `f'` aggressively before `ring` /
  `linear_combination` to avoid polynomial-degree blowup.
- For a predicate whose decidability comes from an underlying construction, use
  `abbrev` (= `@[reducible] def`), not `def`, so instance search sees through it.
- When a lambda passed to a polymorphic function has an ambiguous argument type,
  annotate it (`fun (i : Fin n) => …`) or use `.val`.
- To identify `deriv f` at a point from a known closed-form derivative, use
  `HasDerivAt.congr_of_eventuallyEq` with a `=ᶠ[nhds x]` neighborhood equality.
- Convexity on an OPEN set (e.g. `Set.Ioi 0`): `convexOn_of_deriv2_nonneg'`
  (the primed variant wants differentiability on the set itself).
- Canonical discount factor in NEW files: `Real.exp (-(r * τ))` — product under
  one negation.

── STRUCTURAL STRATEGY (reach for these before brute force) ──
- "This IS already that under renaming": before writing a fresh Gaussian integral
  or induction, ask whether the target is literally an instance of an existing
  closed form at a different parameterisation (e.g. a power/quanto/chooser payoff
  is `bs_call_formula` at an effective spot/vol). Then the proof is algebraic
  identification + reuse, not new machinery.
- Variational `m = min_c g(c)`: hunt for a POINTWISE certificate inequality whose
  integral collapses to `m` for every `c`, with equality exactly at `c*` — no
  calculus needed (cf. the Rockafellar–Uryasev CVaR proof).
- Multi-step from one step: prove the one-period inequality + a monotonicity lemma
  for the one-period operator, then induct.

You will receive compiler feedback (errors and, at a `sorry`, the goal state)
after each attempt. Read it precisely, revise, and resend the complete file.
"""


def build_system_prompt(main_repo: str) -> str:
    pins = read_pins(main_repo)
    pin_block = (
        "── PINS (target THIS API surface exactly) ──\n"
        f"- Lean toolchain: {pins['toolchain']}\n"
        f"- Mathlib: leanprover-community/mathlib4 @ {pins['mathlib']}\n"
        f"- BrownianMotion: RemyDegenne/brownian-motion @ {pins['brownianmotion']}\n"
        "Lemma names / signatures must match these revisions — do not assume a "
        "newer or older Mathlib API. If unsure a lemma exists at this pin, prefer "
        "a first-principles step over a guessed name.\n"
    )
    return HOUSE_DOCTRINE + "\n" + pin_block


# --- Per-target context pack (consume-don't-reprove) --------------------------

_DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:noncomputable\s+)?(?:public\s+)?"
    r"(theorem|lemma|def|abbrev|structure|instance)\s+([A-Za-z0-9_'.]+)",
    re.MULTILINE,
)


def _first_line(s: str | None) -> str:
    if not s:
        return ""
    return s.strip().splitlines()[0].strip()


def _index_pack(idx: ScoutIndex, modules: list[str], max_per_module: int,
                exemplar_limit: int) -> str:
    """Context pack from the lean_scout index: REAL elaborated signatures +
    docstrings, plus house-style (goal → tactic) exemplars. '' if the index
    covers none of the requested modules (caller then falls back to regex)."""
    by_mod = idx.signatures(modules, max_per_module=max_per_module)
    if not by_mod:
        return ""
    blocks: list[str] = []
    for mod in modules:
        recs = by_mod.get(_module_key(idx, mod))
        if not recs:
            continue
        lines = []
        for name, typ, doc in recs:
            short = name.rsplit(".", 1)[-1]
            lines.append(f"{short} : {typ}" + (f"    -- {_first_line(doc)}" if doc else ""))
        blocks.append(f"• {mod}:\n    " + "\n    ".join(lines))
    if not blocks:
        return ""
    pack = ("── EXISTING DECLARATIONS TO BUILD ON (real signatures; consume, do not reprove) ──\n"
            + "\n".join(blocks) + "\n")
    exemplars = idx.tactic_exemplars(modules, limit=exemplar_limit)
    if exemplars:
        ex_lines = [f"    {goal}\n        ⟶  {tac}" for goal, tac in exemplars]
        pack += ("── HOUSE-STYLE TACTIC EXEMPLARS (how this library discharges goals) ──\n"
                 + "\n".join(ex_lines) + "\n")
    return pack


def _module_key(idx: ScoutIndex, mod: str) -> str:
    from scout_index import path_to_module
    return path_to_module(mod)


def _regex_pack(main_repo: str, modules: list[str], max_per_module: int) -> str:
    """Fallback context pack: declaration names scraped from source by regex."""
    out: list[str] = []
    for mod in modules:
        path = os.path.join(main_repo, mod)
        if not os.path.exists(path):
            continue
        try:
            src = open(path, encoding="utf-8").read()
        except Exception:
            continue
        names = [f"{kind} {name}" for kind, name in _DECL_RE.findall(src)][:max_per_module]
        if names:
            out.append(f"• {mod}:\n    " + "\n    ".join(names))
    if not out:
        return ""
    return ("── EXISTING DECLARATIONS TO BUILD ON (consume these; do not reprove) ──\n"
            + "\n".join(out) + "\n")


def extract_signatures(main_repo: str, modules: list[str], max_per_module: int = 40,
                       index_dir: str | None = None, exemplar_limit: int = 6) -> str:
    """Per-target context pack so the agent builds on existing results instead of
    reproving them. `modules` are repo-relative paths (e.g.
    'MathFin/FixedIncome/VasicekBondPrice.lean').

    Prefers the lean_scout index (real elaborated signatures + docstrings +
    house-style tactic exemplars) when it covers the requested modules; otherwise
    falls back to the regex name scrape, so the foundry works with or without an
    index built."""
    idx = ScoutIndex(index_dir if index_dir is not None else default_index_dir())
    if idx.available:
        pack = _index_pack(idx, modules, max_per_module, exemplar_limit)
        if pack:
            return pack
    return _regex_pack(main_repo, modules, max_per_module)
