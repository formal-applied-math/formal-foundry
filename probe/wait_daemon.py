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


def main() -> int:
    return 0 if wait_ready() else 1


if __name__ == "__main__":
    sys.exit(main())
