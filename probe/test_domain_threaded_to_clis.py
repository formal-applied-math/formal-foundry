"""A script that knows its domain must tell the CLIs it calls.

Three bugs in one script tonight, all the same shape: the library was parameterised
and one wiring point was missed. The worst of them was silent —
`index_filter.py "$INDEX"` with no `--domain` fell back to the DEFAULT pack, sliced
an econometrics extraction against the flagship's namespaces, kept 0 of 766,629
records, and reported a successful build of an empty index (run 31996412973). Nothing
failed. The index was simply useless, and the tick that consumed it would have fallen
back to loogle without comment.

A default that is right for one domain and silently wrong for another is worse than
a required argument, but the CLIs need their defaults for interactive use. So the
rule is enforced at the call site instead: if a script sources the pack, every
probe/ CLI it invokes that ACCEPTS `--domain` must be PASSED one.

Static: reads argparse declarations and shell call sites, runs nothing.
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(ROOT, "scripts")

# index_filter also accepts `--own`, which names the namespaces directly and so is
# an equally explicit answer to "which domain".
ALTERNATIVES = {"index_filter": ("--domain", "--own")}


def _accepts_domain(module: str) -> bool:
    path = os.path.join(PROBE, f"{module}.py")
    if not os.path.isfile(path):
        return False
    return '"--domain"' in open(path, encoding="utf-8").read()


def _pack_aware_scripts() -> list[str]:
    if not os.path.isdir(SCRIPTS):
        return []
    out = []
    for f in sorted(os.listdir(SCRIPTS)):
        if not f.endswith(".sh"):
            continue
        p = os.path.join(SCRIPTS, f)
        if "--export-env" in open(p, encoding="utf-8").read():
            out.append(p)
    return out


def _probe_invocations(src: str) -> list[tuple[str, str]]:
    """(module, the rest of that command line) for each `probe/<mod>.py` CALL.

    Comment lines are dropped first: several scripts point at a module in prose
    ("see probe/index_filter.py for the reasoning"), and a doc reference is not an
    invocation. The first version of this gate flagged one and would have taught
    people to ignore it."""
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    out = []
    for m in re.finditer(r"probe/(\w+)\.py\"?((?:[^\n\\]|\\\n)*)", code):
        out.append((m.group(1), m.group(2)))
    return out


def test_every_domain_aware_cli_is_told_its_domain():
    problems = []
    checked = 0
    for path in _pack_aware_scripts():
        src = open(path, encoding="utf-8").read()
        for mod, rest in _probe_invocations(src):
            if mod == "domain_pack":          # the shim itself takes the name positionally
                continue
            if not _accepts_domain(mod):
                continue
            checked += 1
            flags = ALTERNATIVES.get(mod, ("--domain",))
            if not any(f in rest for f in flags):
                problems.append(
                    f"{os.path.basename(path)}: {mod}.py accepts {'/'.join(flags)} but is "
                    "called without it — it will silently use the DEFAULT pack")
    assert checked, "scan found no domain-aware probe CLI calls in pack-aware scripts"
    assert not problems, (
        "a domain-aware CLI is being called without its domain, so it falls back to "
        "the default pack and quietly does the wrong library:\n  " + "\n  ".join(problems))


def test_the_scan_sees_the_calls_it_claims_to():
    """A regex matching nothing would make the gate above vacuous."""
    found = {os.path.basename(p): [m for m, _ in _probe_invocations(
        open(p, encoding="utf-8").read())] for p in _pack_aware_scripts()}
    assert any(v for v in found.values()), f"no probe/ invocations found at all: {found}"
    assert _accepts_domain("index_filter"), "index_filter should declare --domain"


def test_the_gate_would_catch_the_bug_it_was_written_for():
    """The exact live shape: index_filter called with only the index dir."""
    bad = 'eval "$(python3 "$F/probe/domain_pack.py" --export-env)"\n' \
          'python3 "$F/probe/index_filter.py" "$INDEX"\n'
    mod, rest = [c for c in _probe_invocations(bad) if c[0] == "index_filter"][0]
    assert not any(f in rest for f in ALTERNATIVES["index_filter"])

    good = bad.replace('"$INDEX"', '"$INDEX" --domain "$DOMAIN_NAME"')
    mod, rest = [c for c in _probe_invocations(good) if c[0] == "index_filter"][0]
    assert any(f in rest for f in ALTERNATIVES["index_filter"])
