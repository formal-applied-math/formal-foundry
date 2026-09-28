"""Block until the lean-repl daemon is actually serving.

`docker logs | grep READY:` is unreliable after a `stop`+`start`: the old READY
line persists in the logs, so the grep matches immediately and the caller proceeds
against a daemon that is still cold-loading Mathlib (this exact bug failed the first
W1 gate). Instead PROBE the port with a trivial check and retry until it succeeds —
no dependence on log freshness, and identical behaviour locally and on CI.
"""

from __future__ import annotations

import sys
import time

PROBE = "example : True := by trivial"


def wait_ready(*, tries: int = 120, sleep: float = 5.0, check_fn=None, sleep_fn=time.sleep) -> bool:
    """Return True once the daemon answers the trivial probe with success, else
    False after `tries` attempts. `check_fn`/`sleep_fn` are injected for testing."""
    if check_fn is None:
        from probe import daemon_check
        check_fn = daemon_check
    for i in range(1, tries + 1):
        try:
            if check_fn(PROBE).get("success"):
                print(f"[wait-daemon] ready after {i} probe(s)", flush=True)
                return True
        except Exception:  # noqa: BLE001 — a refused/half-open socket is just "not ready yet"
            pass
        sleep_fn(sleep)
    print(f"[wait-daemon] NOT ready after {tries} probes", file=sys.stderr)
    return False


def environment_ready(*, check_fn=None, pack=None, tries: int = 3, sleep: float = 10.0,
                      sleep_fn=time.sleep) -> bool:
    """Liveness is not readiness. `PROBE` needs no environment, so a REPL that has lost
    (or never loaded) Mathlib answers it and is declared ready, and every gate after it
    reads `unknown namespace MeasureTheory` as a verdict on the code. This elaborates the
    decompose path's environment probe — the pack's real imports and house opens — which
    has run on the production daemon before every skeleton rejection since 2026-09-08."""
    from decompose import environment_canary
    if check_fn is None:
        from probe import daemon_check
        check_fn = daemon_check
    if pack is None:
        import os

        import domain_pack
        cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "pipeline.toml")
        pack = domain_pack.load(os.environ.get("DOMAIN") or domain_pack.name_from_config(cfg))
    for i in range(1, tries + 1):
        if environment_canary(pack, check_fn):
            print(f"[wait-daemon] environment loads (probe {i})", flush=True)
            return True
        sleep_fn(sleep)
    print(f"[wait-daemon] the daemon answers but its environment does NOT load after {tries} "
          "probes — nothing it says about a proof is a verdict", file=sys.stderr)
    return False


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not wait_ready():
        return 1
    if "--liveness-only" in argv:
        return 0
    return 0 if environment_ready() else 1


if __name__ == "__main__":
    sys.exit(main())
