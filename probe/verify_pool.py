"""Parallel Lean verification pool (Phase 3, Task 3.1) — a CI/big-box substrate.

DESIGN NOTE — this pool runs **only on CI or a big box (≥16 GB)**; the local
one-Lean-process doctrine is untouched (memory doctrine: two Mathlib-loaded Lean processes
overcommit the 10 GB dev box). It exists to (a) prove the decomposition loop's independent
leaves in parallel and (b) speed the `ledger verify --exec` corpus sweep after a deep-module
change — both wall-clock-bound by a single Lean slot today.

Scope: it parallelizes ELABORATION/verification (the leaf gate + the corpus sweep), not the
agentic prove itself — the vibe ⇄ lean-lsp harness keeps its single-lean-lsp slot. So on CI
it gates N proved leaves at once and re-verifies the ledger's stale entries concurrently.

The pool is generic: it schedules `tasks` across `n_workers` reusable workers (a worker holds
a warm Lean env — env-cache reuse), preserves result order, and on an OOM/crash it recycles
the worker (discard + respawn) and retries the task up to `max_recycle` times. `spawn_fn` /
`check_fn` are injected, so the scheduling + recycle logic is unit-tested with fakes; the real
Lean worker (`lake env lean` per check, exit-137 ⇒ recycle) is the CI wiring, validated on the
first `batch-verify` run (like open-pr's first-run path — it cannot be exercised on the dev box).
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

_OOM_MARKERS = ("oom", "out of memory", "exit 137", "exit 143", "killed", "signal 9")


def is_oom(result) -> bool:
    """A worker crash needing RECYCLE (not a real Lean error): the check raised, or returned
    an infra `error` (the daemon-style singular sentinel) mentioning an OOM/kill. A plural
    `errors` list (a genuine type mismatch etc.) is a real verdict, never a recycle."""
    if isinstance(result, BaseException):
        return True
    if isinstance(result, dict):
        return any(m in str(result.get("error", "")).lower() for m in _OOM_MARKERS)
    return False


class VerifyPool:
    """N reusable Lean workers with recycle-on-OOM. `spawn_fn() -> worker`,
    `check_fn(worker, task) -> result`; a worker with `.close()` is torn down at recycle
    and at `close()`."""

    def __init__(self, n_workers: int, *, spawn_fn, check_fn, is_oom=is_oom,
                 max_recycle: int = 2, log=None):
        self.spawn_fn = spawn_fn
        self.check_fn = check_fn
        self.is_oom = is_oom
        self.max_recycle = max_recycle
        self.log = log or (lambda _m: None)
        self.n_workers = max(1, n_workers)
        self.recycled = 0
        self._idle: "queue.Queue" = queue.Queue()
        for _ in range(self.n_workers):
            self._idle.put(self.spawn_fn())   # prewarm: workers hold a loaded env

    def _close_worker(self, w) -> None:
        if hasattr(w, "close"):
            try:
                w.close()
            except Exception:   # teardown is best-effort
                pass

    def _run_one(self, task) -> dict:
        attempts = recycled_here = 0
        while True:
            worker = self._idle.get()
            attempts += 1
            try:
                result = self.check_fn(worker, task)
            except Exception as e:   # a raised check is an infra crash → recycle
                result = e
            if self.is_oom(result) and recycled_here < self.max_recycle:
                self._close_worker(worker)          # discard the dead worker
                self.recycled += 1
                recycled_here += 1
                self._idle.put(self.spawn_fn())     # respawn a fresh one
                self.log(f"recycled a worker after OOM (task retry {recycled_here})")
                continue
            self._idle.put(worker)                  # env-cache reuse: return it warm
            ok = not self.is_oom(result)
            payload = {"error": str(result)} if isinstance(result, BaseException) else result
            return {"task": task, "ok": ok, "result": payload,
                    "attempts": attempts, "recycled": recycled_here}

    def run(self, tasks) -> list[dict]:
        """Verify every task across the pool, results in task order."""
        with ThreadPoolExecutor(max_workers=self.n_workers) as ex:
            return list(ex.map(self._run_one, tasks))

    def close(self) -> None:
        while not self._idle.empty():
            self._close_worker(self._idle.get())


# --- real Lean worker: `lake env lean` per check (CI wiring) ------------------

def lake_env_check(main_repo: str, code: str, *, timeout: int = 600) -> dict:
    """Elaborate `code` via `lake env lean` on a temp module in `main_repo` (reusing the
    shared on-disk olean cache — the env-cache). Returns `{ok, errors, sorry_count,
    exit_code}`; an exit 137/143 (OOM/kill) becomes an `error` so the pool recycles."""
    fd, path = tempfile.mkstemp(suffix=".lean", dir=os.path.join(main_repo, "MathFin"))
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        try:
            p = subprocess.run(["lake", "env", "lean", path], cwd=main_repo,
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"error": "lake env lean timed out (possible OOM/spin)"}
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode in (137, 143):
            return {"error": f"lake env lean killed (exit {p.returncode}, likely OOM)"}
        errors = [ln for ln in out.splitlines() if ": error:" in ln]
        return {"ok": p.returncode == 0 and not errors, "errors": errors,
                "sorry_count": out.count("declaration uses 'sorry'"),
                "exit_code": p.returncode}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _cmd(args) -> int:
    """Pool a list of `{id, code}` tasks through `lake env lean` on N workers. Tasks come
    from --tasks (a json list) or a manifest's leaf/prove stubs via --manifest."""
    if args.manifest:
        man = json.load(open(args.manifest))
        root = os.path.dirname(os.path.abspath(args.manifest))
        tasks = [{"id": t["id"], "code": open(os.path.join(root, t["file"]), encoding="utf-8").read()}
                 for t in man["targets"]]
    else:
        tasks = json.load(open(args.tasks))
    pool = VerifyPool(args.workers, spawn_fn=lambda: object(),
                      check_fn=lambda _w, t: lake_env_check(args.main_repo, t["code"],
                                                            timeout=args.timeout),
                      log=lambda m: print(f"[verify-pool] {m}", file=sys.stderr, flush=True))
    try:
        results = pool.run(tasks)
    finally:
        pool.close()
    rows = [{"id": t["id"], "ok": r["ok"], "attempts": r["attempts"],
             "recycled": r["recycled"], "result": r["result"]}
            for t, r in zip(tasks, results)]
    print(json.dumps({"total": len(rows), "passed": sum(1 for r in rows if r["ok"]),
                      "rows": rows}, indent=2))
    return 0 if all(r["ok"] for r in rows) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="parallel Lean verification pool (CI/big-box only)")
    ap.add_argument("--main-repo", required=True)
    ap.add_argument("--tasks", help="json list of {id, code} tasks")
    ap.add_argument("--manifest", help="a prove/leaf manifest.json (checks each stub file)")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("VERIFY_POOL_WORKERS", "2")))
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()
    if not args.tasks and not args.manifest:
        print("need --tasks or --manifest", file=sys.stderr)
        return 2
    return _cmd(args)


if __name__ == "__main__":
    sys.exit(main())
