"""`probe/` must not name a domain. Runbook 02's grep-gate, as a test.

The pack refactor is only worth something if it STAYS done: the next person to add
a prompt, a gate snippet or an emitter will reach for the flagship's namespace
because that is what every example shows. This gate makes that a red test instead
of a silent re-coupling that only surfaces when a second library ticks.

Mirrors formal-mathfin's forbidden-text pattern. Scope is `probe/*.py`, excluding
tests — per the runbook, test fixtures are legitimately domain-flavoured; they ARE
the mathfin pack's test data. `scripts/` and `.github/workflows/` are runbook 06's
territory and are deliberately NOT covered here; see the note at the bottom.
"""
from __future__ import annotations

import os
import re

FORBIDDEN = re.compile(r"MathFin|mathfin", re.IGNORECASE)

PROBE_DIR = os.path.dirname(os.path.abspath(__file__))

# Every allowed occurrence, with the reason it is not coupling. A new entry here is
# a deliberate act that shows up in review — which is the point.
ALLOWED = {
    # The loader names the default pack and documents the contract with worked
    # examples. It is the ONE file whose job is to know a pack name exists.
    "domain_pack.py": re.compile(
        r'domain_pack\.load\("mathfin"\)'
        r'|DEFAULT_NAME = "mathfin"'
        r'|name: str = "mathfin"'
        r"|mathfin and econometrics"
        r"|MathFin"                     # docstring examples of a namespace
        r"|formal-mathfin/docs/plans"   # the design of record's path
    ),
    # Historical provenance: these cite the real issues a guard was built from.
    # Renaming them would destroy the audit trail the honesty register depends on.
    "pipeline_lib.py": re.compile(r"formal-mathfin#\d+"),
    "strengthen.py": re.compile(r"formal-mathfin#\d+"),
}


def _sources() -> list[str]:
    return sorted(f for f in os.listdir(PROBE_DIR)
                  if f.endswith(".py") and not f.startswith("test_"))


def test_no_domain_leakage_in_probe_sources():
    leaks: list[str] = []
    for fname in _sources():
        allowed = ALLOWED.get(fname)
        with open(os.path.join(PROBE_DIR, fname), encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if not FORBIDDEN.search(line):
                    continue
                if allowed and allowed.search(line):
                    continue
                leaks.append(f"{fname}:{lineno}: {line.rstrip()}")
    assert not leaks, (
        "probe/ names a domain — move it into domains/<name>/ and reach it through "
        "the pack, or add an explicit ALLOWED entry saying why it is not coupling:\n  "
        + "\n  ".join(leaks))


def test_the_allowlist_has_no_dead_entries():
    """An ALLOWED entry that no longer matches anything is stale permission — it
    would silently re-admit the pattern it was written to excuse."""
    dead = []
    for fname, pattern in ALLOWED.items():
        path = os.path.join(PROBE_DIR, fname)
        if not os.path.isfile(path):
            dead.append(f"{fname} (no such file)")
            continue
        with open(path, encoding="utf-8") as f:
            if not pattern.search(f.read()):
                dead.append(f"{fname} (pattern matches nothing)")
    assert not dead, "stale ALLOWED entries — delete them:\n  " + "\n  ".join(dead)


def test_the_gate_would_actually_catch_a_leak():
    """A gate nobody has seen fail is a gate nobody knows works."""
    assert FORBIDDEN.search('namespace = "MathFin"')
    assert FORBIDDEN.search("ghcr.io/formal-applied-math/mathfin-verify:latest")
    assert not FORBIDDEN.search('namespace = "Econometrics"')


# NOT COVERED HERE, and deliberately: `scripts/*.sh` and `.github/workflows/*.yml`
# still name the flagship in ~77 places — the container name, the GHCR image, the
# `lake build MathFin` line, the `repository:` checkouts and the cache keys. Those
# are the TARGET PLANE, and lifting them is runbook 06's whole job. A domain-free
# `probe/` is necessary and not sufficient: until 06 lands, the foundry is portable
# in principle and immovable in practice, and this test must not be read as saying
# otherwise.
