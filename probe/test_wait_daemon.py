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
