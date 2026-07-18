"""Tests for the parallel Lean-REPL verification pool (Task 3.1). Injected fake workers —
no real Lean, no subprocess, no network. Results are deterministic (each depends only on
the task + its attempt count), so thread interleaving cannot flake the assertions."""

from __future__ import annotations

from verify_pool import VerifyPool, is_oom


def test_pool_runs_all_tasks_in_order_and_reuses_workers():
    spawned = []
    def spawn():
        w = {"id": len(spawned)}
        spawned.append(w)
        return w
    def check(w, t):
        return {"ok": True, "t": t}
    pool = VerifyPool(2, spawn_fn=spawn, check_fn=check)
    res = pool.run(["a", "b", "c", "d"])
    assert [r["result"]["t"] for r in res] == ["a", "b", "c", "d"]   # order preserved
    assert all(r["ok"] for r in res)
    assert len(spawned) == 2       # env-cache reuse: 2 workers serve 4 tasks, no respawn
    pool.close()


def test_pool_recycles_worker_on_oom_then_retries():
    spawned = []
    def spawn():
        w = {"id": len(spawned)}
        spawned.append(w)
        return w
    state = {"hit": False}
    def check(w, t):
        if t == "bad" and not state["hit"]:
            state["hit"] = True
            return {"error": "worker killed (exit 137, out of memory)"}
        return {"ok": True}
    pool = VerifyPool(1, spawn_fn=spawn, check_fn=check, max_recycle=2)
    res = pool.run(["bad"])
    assert res[0]["ok"] and res[0]["attempts"] == 2 and res[0]["recycled"] == 1
    assert pool.recycled == 1 and len(spawned) == 2   # 1 initial + 1 respawn
    pool.close()


def test_pool_gives_up_after_max_recycle():
    def spawn():
        return object()
    def check(w, t):
        return {"error": "oom"}
    pool = VerifyPool(1, spawn_fn=spawn, check_fn=check, max_recycle=2)
    res = pool.run(["x"])
    assert not res[0]["ok"] and res[0]["attempts"] == 3   # initial + 2 recycles, then give up
    assert res[0]["recycled"] == 2
    pool.close()


def test_pool_closes_workers_that_expose_close():
    closed = []
    class W:
        def __init__(self, i): self.i = i
        def close(self): closed.append(self.i)
    seq = {"n": 0}
    def spawn():
        seq["n"] += 1
        return W(seq["n"])
    pool = VerifyPool(2, spawn_fn=spawn, check_fn=lambda w, t: {"ok": True})
    pool.run(["a", "b"])
    pool.close()
    assert sorted(closed) == [1, 2]


def test_is_oom_default_detects_kills_not_type_errors():
    assert is_oom({"error": "process killed (exit 137)"})
    assert is_oom({"error": "OOM"})
    assert not is_oom({"ok": True})
    assert not is_oom({"errors": ["type mismatch"]})   # a real Lean error is NOT an infra recycle
