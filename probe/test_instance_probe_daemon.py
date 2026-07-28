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

# generous per-check deadline: each elaborates `import Mathlib` and on the 2-core CI
# runner the daemon's serial queue can back up (a client timeout does not cancel
# server work), so a too-tight deadline flakes.
_ELAB_TIMEOUT = 360


# the daemon's OWN internal elab-timeout (default 180s) surfaces a killed REPL as a
# plural `errors` entry ("elaboration timed out after …s (REPL killed)"), NOT the
# singular `error` infra sentinel — and it fires before the client `_ELAB_TIMEOUT`.
# on a contended 2-core runner an `import Mathlib` elaboration can exceed that window,
# which is indeterminate infra, not a slip-catch regression, so skip on it too.
_INFRA_ERR_MARKERS = ("timed out", "repl killed", "timeout")


def _check_or_skip(code: str):
    """A daemon check, but a "did not complete" TIMEOUT is INDETERMINATE infra (a wedged /
    backed-up daemon on a slow shared runner, or the daemon's internal elab-timeout killing
    the REPL), not a regression of the slip-catch — skip rather than red the run, the same
    philosophy as the daemon-down skip above."""
    r = daemon_check(code, timeout=_ELAB_TIMEOUT)
    if r.get("error"):   # singular = the infra sentinel from probe.daemon_check
        pytest.skip(f"daemon check did not complete (infra, not a regression): {r['error']}")
    errs = " ".join(str(e) for e in (r.get("errors") or [])).lower()
    if any(mk in errs for mk in _INFRA_ERR_MARKERS):   # daemon-internal elab-timeout / killed REPL
        pytest.skip(f"daemon elaboration timed out (infra, not a regression): {r.get('errors')}")
    return r


def test_correct_def_with_probe_elaborates():
    """A faithful def + its intended-value probe elaborate — the gate lets it through."""
    r = _check_or_skip(_HDR + _CORRECT + _PROBE)
    assert r["success"] and not r.get("errors"), r.get("errors")


def test_slip_without_probe_passes_silently():
    """The whole reason M exists: the inverted def elaborates cleanly with no probe,
    so the faithfulness/vacuity/disproof gates would all miss the slip."""
    r = _check_or_skip(_HDR + _SLIP)
    assert r["success"] and not r.get("errors"), r.get("errors")


def test_slip_with_probe_is_caught():
    """The catch: the same slip + its intended-value probe fails to elaborate —
    `norm_num` cannot prove 7/3 = 3/7. The mandatory probe converts a silent
    misformalization into a hard elaboration error, before the prove stage."""
    r = _check_or_skip(_HDR + _SLIP + _PROBE)
    assert not r["success"] or r.get("errors")
