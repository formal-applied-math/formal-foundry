"""The cheap prove probe: try a fixed menu of Lean's strong closing tactics as
whole-proof scripts, verified through the daemon. A win is a SCOUT (a lead to the
conceptually-right proof), never the merged author proof — the caller scout-tags
it and opens a DRAFT PR. Design inspiration: ulam's autop fallbacks (no code
vendored). See the 2026-07-16 spec.
"""
from __future__ import annotations

# Lean/Mathlib built-in closers, cheapest/most-common first. `grind` last (it is
# the heaviest search). These mirror ulam's autop set adapted to our toolkit.
AUTOP_MENU: tuple[str, ...] = (
    "simp", "norm_num", "ring_nf", "linarith", "nlinarith", "aesop", "grind",
)


def autop_candidate(statement: str, tactic: str) -> str:
    """The staged stub with its `:= by sorry` replaced by `:= by <tactic>`. The
    staged stub ends in exactly one `by sorry` (the target), so a plain replace
    is unambiguous."""
    return statement.replace("by sorry", f"by {tactic}")


def autop_prove(statement: str, *, check_fn, menu=AUTOP_MENU) -> dict | None:
    """First menu tactic whose whole-proof script elaborates with 0 sorries, as
    `{"tactic", "proof"}`; None if none close. Values gate: the caller must treat
    a result as a SCOUT (never a silent merge). Fails safe (None) if statement
    does not contain exactly one "by sorry" hole."""
    if statement.count("by sorry") != 1:
        # no unique hole to close → fail safe; never misattribute a close to a
        # tactic that was never inserted (values gate: no false scout).
        return None
    for tactic in menu:
        cand = autop_candidate(statement, tactic)
        res = check_fn(cand)
        if res.get("success") and res.get("sorry_count", 1) == 0:
            return {"tactic": tactic, "proof": cand}
    return None
