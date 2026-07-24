"""Item M — the REAL slip-catch test on the live lean-repl daemon.

This is deliberately NOT a parser tautology. It elaborates actual Lean and proves
the instance-probe MECHANISM end to end: a definition with an inverted
numerator/denominator (a sign/normalization slip) elaborates cleanly on its own —
so the faithfulness / vacuity / disproof gates all pass it — but its
intended-value `example` FAILS to elaborate. That is exactly what makes the
mandatory instance probe catch the slip before the (expensive) prove stage, the
class of misformalization the #73 maxDD `∀c` episode exposed.

Runs against the daemon on 127.0.0.1:7878; skips when it is not serving (so the
fast pure-logic suite stays daemon-free). Each check elaborates `import Mathlib`,
so this file is slow by design — run it when validating item M, or in CI with the
daemon up.
"""
from __future__ import annotations

import pytest

from probe import daemon_check

_HDR = "import Mathlib\n\nopen scoped BigOperators\n\n"
# upCapture: (sum of portfolio returns over `up`) / (sum of benchmark returns over `up`).
_CORRECT = ("noncomputable def upCapture {S : Type*} (up : Finset S) (p b : S → ℝ) : ℝ :=\n"
            "  (∑ s ∈ up, p s) / (∑ s ∈ up, b s)\n\n")
# the slip: numerator and denominator inverted (b/p) — reads plausibly, wrong semantics.
_SLIP = ("noncomputable def upCapture {S : Type*} (up : Finset S) (p b : S → ℝ) : ℝ :=\n"
         "  (∑ s ∈ up, b s) / (∑ s ∈ up, p s)\n\n")
# the intended-value probe: over Fin 2, p = ![1,2] (sum 3), b = ![3,4] (sum 7) → 3/7.
_PROBE = ("example : upCapture (Finset.univ : Finset (Fin 2)) ![1, 2] ![3, 4] = 3 / 7 := by\n"
          "  norm_num [upCapture, Fin.sum_univ_two]\n")


def _daemon_up() -> bool:
    return daemon_check("example : True := trivial", timeout=60).get("success") is True


pytestmark = pytest.mark.skipif(
    not _daemon_up(), reason="lean-repl daemon not serving on 127.0.0.1:7878")


def test_correct_def_with_probe_elaborates():
    """A faithful def + its intended-value probe elaborate — the gate lets it through."""
    r = daemon_check(_HDR + _CORRECT + _PROBE, timeout=200)
    assert r["success"] and not r.get("errors"), r.get("errors")


def test_slip_without_probe_passes_silently():
    """The whole reason M exists: the inverted def elaborates cleanly with no probe,
    so the faithfulness/vacuity/disproof gates would all miss the slip."""
    r = daemon_check(_HDR + _SLIP, timeout=200)
    assert r["success"] and not r.get("errors"), r.get("errors")


def test_slip_with_probe_is_caught():
    """The catch: the same slip + its intended-value probe fails to elaborate —
    `norm_num` cannot prove 7/3 = 3/7. The mandatory probe converts a silent
    misformalization into a hard elaboration error, before the prove stage."""
    r = daemon_check(_HDR + _SLIP + _PROBE, timeout=200)
    assert not r["success"] or r.get("errors")
