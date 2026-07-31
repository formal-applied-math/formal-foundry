"""The backlog must not state a proposed mechanism at the confidence of a finding.

Item R was recorded as an established plan while being untraced and, as it turned out,
wrong — the pipeline already had the pass it described, and on the artifacts cited that
pass does not fire. The convention (see the doc's "Item convention" section) is that a
proposed gate carries a Reproduction line, or is tagged [UNTRACED]."""
from __future__ import annotations

import os
import re

_DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "docs", "upgrade-backlog.md")

# a heading tag meaning the work exists — shipped code is its own trace
_DONE = re.compile(r"\[(?:BUILT|LANDED|APPLIED|DONE|SHIPPED)\b", re.I)
_UNTRACED = re.compile(r"\[UNTRACED\]", re.I)
_REPRO = re.compile(r"(?mi)^\s*\*\*Reproduction:?\*\*")
# language that makes an item a proposed MECHANISM rather than a note
_PROPOSES_GATE = re.compile(
    r"(?i)\b(gate|prober|probe|check|guard|filter)\b.*\b(catch|fire|reject|detect|flag)\b"
    r"|\bwould have caught\b")
# a reproduction must point at something concrete
_CONCRETE = re.compile(r"(?:#\d+|`[\w./]+\.(?:py|lean|json|sh|ya?ml)`|cal-bk-\d+)")


def _items() -> list[tuple[str, str]]:
    """(heading, body) for every `###` item in the backlog."""
    text = open(_DOC, encoding="utf-8").read()
    parts = re.split(r"(?m)^### ", text)[1:]
    return [(p.split("\n", 1)[0].strip(), p) for p in parts]


def test_the_convention_is_documented():
    text = open(_DOC, encoding="utf-8").read()
    assert "Item convention: observed" in text
    assert "Reproduction:" in text


def test_a_proposed_gate_is_traced_or_labelled_untraced():
    offenders = []
    for heading, body in _items():
        if _DONE.search(heading) or _UNTRACED.search(heading):
            continue
        if _PROPOSES_GATE.search(body) and not _REPRO.search(body):
            offenders.append(heading)
    assert not offenders, (
        "these items propose a mechanism without a **Reproduction:** line and without "
        "an [UNTRACED] tag — an untraced fix is a hypothesis, not a plan: "
        + "; ".join(offenders))


def test_every_reproduction_line_names_something_concrete():
    vague = []
    for heading, body in _items():
        for m in _REPRO.finditer(body):
            para = body[m.end():].split("\n\n", 1)[0]
            if not _CONCRETE.search(para):
                vague.append(heading)
    assert not vague, (
        "a Reproduction must name the artifact it fires on (a #PR/issue, a file, or a "
        "target id), not just assert that it would: " + "; ".join(vague))
