"""A `$DOMAIN_*` used inside a container must be passed into that container.

I made this mistake twice in one sitting. `docker run … -c '<script>'` runs the
script in the CONTAINER's shell, so every `$VAR` in it is expanded there, not on the
host. A host variable that is not forwarded with `-e` is simply unset inside — and
the two failure modes are both bad:

- under `set -euo pipefail` it dies `unbound variable`, which is how the econometrics
  index build failed (run 31992546925) after paying for a full lean_scout build;
- under plain `set -e` it expands to EMPTY and carries on, which is what
  `open-pr.sh` was about to do — `lake build ""` on every PR, for both domains.

The second is the reason this is a test rather than a habit. Hand-auditing found the
first only because the shell shouted; nothing would have shouted about the second
until a PR came out wrong.

Static and hermetic: reads the scripts, runs no docker.
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")

# Variables the CONTAINER provides itself, or that are set inside the script body.
CONTAINER_PROVIDED = {"PATH", "HOME", "PWD", "SHELL", "HOSTNAME", "TERM", "LANG", "LC_ALL"}


def _script_files() -> list[str]:
    if not os.path.isdir(SCRIPTS):
        return []
    return [os.path.join(SCRIPTS, f) for f in sorted(os.listdir(SCRIPTS))
            if f.endswith(".sh")]


def _strip_comments(body: str) -> str:
    """Drop whole-line `#` comments. A `$VAR` inside a comment is prose, not a
    reference — and the comment explaining THIS trap contains one, which is how the
    first version of this gate failed on itself. Only full-line comments are
    stripped: `${#VAR}` must survive, and an inline `# $VAR` erring on the side of
    a loud false positive is the right way for a gate to be wrong."""
    return "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))


def _docker_run_blocks(src: str) -> list[tuple[str, str]]:
    """(the `docker run …` invocation, the in-container script) for each call.

    Two shapes are used in this repo: an inline `-c '…'`/`-lc '…'` heredoc-ish
    block, and a `-lc "$VARNAME"` referencing a single-quoted variable defined
    earlier. Both put the script in the container's shell, so both are checked.
    """
    out: list[tuple[str, str]] = []

    # shape 1: the script is inline after -c / -lc
    for m in re.finditer(r"docker run\b(?P<inv>.*?)-l?c\s+'(?P<body>.*?)'\s*(?:\\\n|\n|$)",
                         src, re.S):
        out.append((m.group("inv"), m.group("body")))

    # shape 2: -lc "$NAME", with NAME='…' assigned earlier
    for m in re.finditer(r"docker run\b(?P<inv>.*?)-l?c\s+\"\$(?P<name>[A-Z_][A-Z0-9_]*)\"",
                         src, re.S):
        assign = re.search(rf"(?m)^{m.group('name')}='(.*?)'\s*$", src, re.S)
        if assign:
            out.append((m.group("inv"), assign.group(1)))
    return out


def test_every_domain_var_used_in_a_container_is_passed_into_it():
    problems: list[str] = []
    checked = 0
    for path in _script_files():
        src = open(path, encoding="utf-8").read()
        for inv, body in _docker_run_blocks(src):
            checked += 1
            code = _strip_comments(body)
            used = set(re.findall(r"\$\{?([A-Z][A-Z0-9_]*)\}?", code))
            passed = set(re.findall(r"-e\s+([A-Z][A-Z0-9_]*)", inv))
            # a var the in-container script assigns itself is fine
            assigned = set(re.findall(r"(?m)^\s*([A-Z][A-Z0-9_]*)=", code))
            missing = sorted(used - passed - assigned - CONTAINER_PROVIDED)
            if missing:
                problems.append(f"{os.path.basename(path)}: {missing} used in a "
                                f"container but not passed with -e")
    assert checked, "found no `docker run … -c` blocks — the scan matched nothing"
    assert not problems, (
        "a host variable is unset inside the container (empty under `set -e`, fatal "
        "under `set -u`) — forward it with `-e VAR=\"$VAR\"`:\n  "
        + "\n  ".join(problems))


def test_the_scan_finds_the_blocks_it_claims_to():
    """A regex that silently matches nothing would make the gate above vacuous."""
    found = {os.path.basename(p): len(_docker_run_blocks(open(p, encoding="utf-8").read()))
             for p in _script_files()}
    total = sum(found.values())
    assert total >= 2, f"expected at least the build-index and open-pr blocks, got {found}"


def test_the_gate_would_catch_a_stranded_variable():
    """A gate nobody has seen fail is a gate nobody knows works."""
    bad = ("docker run --rm -e REV=\"$REV\" --entrypoint bash img -c '\n"
           "  lake build \"$DOMAIN_NAMESPACE\"\n'\n")
    inv, body = _docker_run_blocks(bad)[0]
    used = set(re.findall(r"\$\{?([A-Z][A-Z0-9_]*)\}?", body))
    passed = set(re.findall(r"-e\s+([A-Z][A-Z0-9_]*)", inv))
    assert "DOMAIN_NAMESPACE" in used - passed

    good = bad.replace('-e REV="$REV"', '-e REV="$REV" -e DOMAIN_NAMESPACE="$DOMAIN_NAMESPACE"')
    inv, body = _docker_run_blocks(good)[0]
    used = set(re.findall(r"\$\{?([A-Z][A-Z0-9_]*)\}?", body))
    passed = set(re.findall(r"-e\s+([A-Z][A-Z0-9_]*)", inv))
    assert not (used - passed - CONTAINER_PROVIDED)
