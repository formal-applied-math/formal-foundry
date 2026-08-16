"""The foundry must not name a domain. Runbooks 02 + 06's grep-gate, as a test.

The pack refactor is only worth something if it STAYS done: the next person to add
a prompt, a gate snippet, an emitter or a workflow step will reach for the
flagship's namespace because that is what every example shows. This gate makes that
a red test instead of a silent re-coupling that only surfaces when a second library
ticks.

Mirrors formal-mathfin's forbidden-text pattern. Three scopes:

- `probe/*.py` excluding tests (runbook 02). Test fixtures are legitimately
  domain-flavoured; per the runbook they ARE the mathfin pack's test data.
- `scripts/*.sh` (runbook 06) — the shell reads DOMAIN_* from the pack shim.
- `.github/workflows/*.yml` + `docker/*.yml` (runbook 06) — `repository:`, the
  image pulls and the cache keys come from the resolved pack.

`domains/` is never scanned: naming its own library is a pack's entire job.
"""
from __future__ import annotations

import os
import re

FORBIDDEN = re.compile(r"MathFin|mathfin", re.IGNORECASE)

PROBE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PROBE_DIR)

# Every allowed occurrence, with the reason it is not coupling. A new entry here is
# a deliberate act that shows up in review — which is the point.
ALLOWED = {
    # The loader names the default pack and documents the contract with worked
    # examples. It is the ONE file whose job is to know a pack name exists.
    "domain_pack.py": re.compile(
        r'domain_pack\.load\("mathfin"\)'
        r"|--export-env mathfin"         # the shim's own usage example
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


# --- runbook 06: the target plane ---------------------------------------------
#
# `probe/` being domain-free is necessary and not sufficient — the pipeline does not
# read `probe/` to decide which repo it operates on. It reads shell scripts, three
# workflows and a compose file. All four are covered below.

def _listdir(*parts: str, suffixes: tuple[str, ...]) -> list[str]:
    d = os.path.join(ROOT, *parts)
    if not os.path.isdir(d):
        return []
    return [f for f in sorted(os.listdir(d)) if f.endswith(suffixes)]


PLANE = {
    "scripts": _listdir("scripts", suffixes=(".sh",)),
    ".github/workflows": _listdir(".github", "workflows", suffixes=(".yml", ".yaml")),
    "docker": _listdir("docker", suffixes=(".yml", ".yaml")),
}

# A pack NAME is not a namespace. The workflows offer `mathfin` in a choice list and
# default to it; the compose file falls back to the flagship's values so a bare
# `docker compose` outside the scripts behaves as it always did. Those are the
# domain being SELECTED, which is the opposite of the domain being welded in.
PLANE_ALLOWED = re.compile(
    r"options: \[mathfin, econometrics\]"
    r"|default: mathfin"
    r"|inputs\.domain \|\| 'mathfin'"
    r"|:-mathfin\}"                                              # compose: DOMAIN_NAME
    r"|:-ghcr\.io/formal-applied-math/mathfin-verify:latest\}"   # compose: image
    r"|:-mathfin-lean-lsp\}"                                     # compose: container
    r"|:-MathFin\}"                                              # compose: lake root
)
# NOTE: there is deliberately NO blanket "comments may say anything" escape. It was
# tried and dropped: it made the gate unable to see a commented-out welded line, and
# the three explanatory comments that needed it were reworded in seconds.


def _plane_files() -> list[tuple[str, str]]:
    return [(f"{d}/{n}", os.path.join(ROOT, *d.split("/"), n))
            for d, names in PLANE.items() for n in names]


def test_the_target_plane_names_no_domain_either():
    """Runbook 06's acceptance criterion. Before it, these files named the flagship
    in ~77 places: the container, the GHCR image, `lake build MathFin`, the
    `repository:` checkouts and the two cache keys."""
    leaks: list[str] = []
    for rel, path in _plane_files():
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if FORBIDDEN.search(line) and not PLANE_ALLOWED.search(line):
                    leaks.append(f"{rel}:{lineno}: {line.rstrip()[:120]}")
    assert not leaks, (
        "the target plane names a domain — read it from the pack instead "
        '(`eval "$(python3 probe/domain_pack.py --export-env)"`):\n  '
        + "\n  ".join(leaks))


def test_the_plane_scan_covers_the_files_it_claims_to():
    """A glob that silently matched nothing would make the gate above vacuous."""
    assert len(PLANE["scripts"]) >= 8, PLANE
    assert len(PLANE[".github/workflows"]) >= 3, PLANE
    assert len(PLANE["docker"]) >= 1, PLANE
