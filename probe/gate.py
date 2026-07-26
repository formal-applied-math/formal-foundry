"""Acceptance gate: forbidden-tactic screen + compiles-clean + axiom whitelist.

Extracted from `probe.run_target`'s accept path (probe.py) so every harness — the
retired text-loop AND the vibe ⇄ lean-lsp-mcp harness — enforces the identical bar.
`check_fn` is the daemon check (`probe.daemon_check`), injected so this module is
pure and unit-testable without a live Lean process.

The bar (unchanged, this is where we are ahead of the field):
  1. no forbidden constructs (sorry/admit/native_decide/exact?/apply?/… via slop_report)
     and lint-clean defs (defsWithUnderscore + docBlame via lint_violations — the
     classes that opened autoform PR #123 red on the main repo's `lake lint`)
  2. compiles with no errors and no residual `sorry`
  3. axioms ⊆ {propext, Classical.choice, Quot.sound} (axiom_guard_block succeeds)
"""

from __future__ import annotations

from probe_lib import axiom_guard_block, lint_violations, slop_report


def gate(candidate: str, sorry_name: str, *, check_fn, statement: str | None = None) -> dict:
    """Return {passed, reason, axioms_clean, slop, errors, warnings}. `reason` is one
    of `forbidden:<list>` / `lint:<list>` / `compile_or_sorry` / `axiom_dirty` /
    `statement_altered` / `ok`. `warnings` are the CANDIDATE check's elaborator warnings
    (the strengthen pass reads `unused variable` off them); textual screens surface none.

    `statement` (item J): the ORIGINAL stub. When supplied, an accepted candidate must
    still assert the same signature (binders + conclusion) for `sorry_name` — else the
    prover altered the statement to something it could prove, and it is rejected. The
    kernel bar checks whatever file the prover returns; this pins WHAT was proved to
    what was asked. Left None by our own post-accept transforms (strengthen drops unused
    hypotheses, so it DELIBERATELY changes the signature)."""
    slop = slop_report(candidate)
    if slop["forbidden"]:
        return {"passed": False, "reason": f"forbidden:{slop['forbidden']}",
                "axioms_clean": None, "slop": slop, "errors": [], "warnings": []}
    lint = lint_violations(candidate)
    if lint:
        return {"passed": False, "reason": f"lint:{lint}",
                "axioms_clean": None, "slop": slop, "errors": [], "warnings": []}
    result = check_fn(candidate)
    warnings = result.get("warnings", [])
    if not (result.get("success") and result.get("sorry_count", 0) == 0):
        return {"passed": False, "reason": "compile_or_sorry", "axioms_clean": None,
                "slop": slop, "errors": result.get("errors", []), "warnings": warnings}
    guard = check_fn(axiom_guard_block(candidate, sorry_name))
    if not guard.get("success"):
        return {"passed": False, "reason": "axiom_dirty", "axioms_clean": False,
                "slop": slop, "errors": [], "warnings": warnings}
    if statement is not None:
        from autoformalize import _probed_signature   # lazy: keep this module import-pure
        want = _probed_signature(statement, sorry_name)
        got = _probed_signature(candidate, sorry_name)
        if want is None or got != want:
            return {"passed": False, "reason": "statement_altered", "axioms_clean": True,
                    "slop": slop, "errors": [], "warnings": warnings}
    return {"passed": True, "reason": "ok", "axioms_clean": True, "slop": slop,
            "errors": [], "warnings": warnings}
