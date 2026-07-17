"""Acceptance gate: forbidden-tactic screen + compiles-clean + axiom whitelist.

Extracted from `probe.run_target`'s accept path (probe.py) so every harness — the
retired text-loop AND the vibe ⇄ lean-lsp-mcp harness — enforces the identical bar.
`check_fn` is the daemon check (`probe.daemon_check`), injected so this module is
pure and unit-testable without a live Lean process.

The bar (unchanged, this is where we are ahead of the field):
  1. no forbidden constructs (sorry/admit/native_decide/exact?/apply?/… via slop_report)
  2. compiles with no errors and no residual `sorry`
  3. axioms ⊆ {propext, Classical.choice, Quot.sound} (axiom_guard_block succeeds)
"""

from __future__ import annotations

from probe_lib import axiom_guard_block, slop_report


def gate(candidate: str, sorry_name: str, *, check_fn) -> dict:
    """Return {passed, reason, axioms_clean, slop, errors}. `reason` is one of
    `forbidden:<list>` / `compile_or_sorry` / `axiom_dirty` / `ok`."""
    slop = slop_report(candidate)
    if slop["forbidden"]:
        return {"passed": False, "reason": f"forbidden:{slop['forbidden']}",
                "axioms_clean": None, "slop": slop, "errors": []}
    result = check_fn(candidate)
    if not (result.get("success") and result.get("sorry_count", 0) == 0):
        return {"passed": False, "reason": "compile_or_sorry", "axioms_clean": None,
                "slop": slop, "errors": result.get("errors", [])}
    guard = check_fn(axiom_guard_block(candidate, sorry_name))
    if not guard.get("success"):
        return {"passed": False, "reason": "axiom_dirty", "axioms_clean": False,
                "slop": slop, "errors": []}
    return {"passed": True, "reason": "ok", "axioms_clean": True, "slop": slop, "errors": []}
