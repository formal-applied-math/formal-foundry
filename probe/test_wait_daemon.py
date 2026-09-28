"""Pure tests for the daemon readiness probe — injected check_fn/sleep, no socket."""
from __future__ import annotations

import wait_daemon


def test_returns_true_once_probe_succeeds():
    seq = iter([{"success": False}, {"success": False}, {"success": True}])
    assert wait_daemon.wait_ready(tries=5, sleep=0, check_fn=lambda c: next(seq),
                                  sleep_fn=lambda s: None) is True


def test_times_out_when_never_ready():
    assert wait_daemon.wait_ready(tries=3, sleep=0, check_fn=lambda c: {"success": False},
                                  sleep_fn=lambda s: None) is False


def test_tolerates_check_exceptions_then_succeeds():
    n = {"i": 0}

    def flaky(_code):
        n["i"] += 1
        if n["i"] < 3:
            raise OSError("connection refused")  # port not open yet
        return {"success": True}

    assert wait_daemon.wait_ready(tries=6, sleep=0, check_fn=flaky,
                                  sleep_fn=lambda s: None) is True


def test_a_live_daemon_without_its_environment_is_not_ready():
    """The REPL answers `example : True := by trivial` with or without Mathlib."""
    import domain_pack
    pack = domain_pack.load("mathfin")
    no_mathlib = lambda code: {"success": False, "sorry_count": 0,  # noqa: E731
                               "errors": ["line 18:5: unknown namespace `MeasureTheory`"]}
    assert wait_daemon.environment_ready(check_fn=no_mathlib, pack=pack, tries=2,
                                         sleep=0, sleep_fn=lambda s: None) is False
    healthy = lambda code: {"success": True, "sorry_count": 0, "errors": []}  # noqa: E731
    assert wait_daemon.environment_ready(check_fn=healthy, pack=pack, tries=2,
                                         sleep=0, sleep_fn=lambda s: None) is True
