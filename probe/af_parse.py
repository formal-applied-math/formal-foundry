"""af_parse — extracted from autoformalize.py; see it for the pipeline overview."""
from __future__ import annotations

import re

__all__ = ['_DECL_RE', '_OPEN', '_CLOSE', '_locate', 'split_statement', 'vacuity_goal', 'disproof_goal']

_DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:theorem|lemma)\s+([A-Za-z0-9_'.]+)",
    re.MULTILINE,
)


_OPEN, _CLOSE = "([{", ")]}"




def _locate(text: str) -> tuple[str, int, int, int]:
    """Locate the theorem/lemma header: return `(name, bstart, sep, end)` — the
    decl name, the index where binders start (just after the name), the index of
    the type-separator `:`, and the index of the proof `:=`. The separator is the
    first `:` at bracket-depth 0 that is not part of `:=` (so a `∀ x : ℝ, …` colon
    in the conclusion, which comes later, and a `(x : T)` binder colon at depth > 0
    are both skipped). Raises on a malformed header."""
    m = _DECL_RE.search(text)
    if not m:
        raise ValueError("no theorem/lemma declaration found")
    name, n = m.group(1), len(text)

    depth, sep = 0, -1
    j = m.end()
    while j < n:
        c = text[j]
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == ":" and depth == 0 and not (j + 1 < n and text[j + 1] == "="):
            sep = j
            break
        j += 1
    if sep == -1:
        raise ValueError("no type separator ':' found")

    depth, end = 0, n
    j = sep + 1
    while j < n:
        c = text[j]
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == ":" and depth == 0 and j + 1 < n and text[j + 1] == "=":
            end = j
            break
        j += 1
    return name, m.end(), sep, end




def split_statement(stub: str) -> tuple[str, str, str]:
    """Split a Lean theorem stub into `(name, binders, concl)`. Robust to a full
    module scaffold around the theorem (finds the line-anchored decl)."""
    name, bstart, sep, end = _locate(stub)
    return name, stub[bstart:sep], stub[sep + 1:end]




def vacuity_goal(lean_text: str) -> str:
    """The hypothesis-rejection probe: the stub with its conclusion replaced by
    `False`, keeping imports + binders. A clean proof means the hypotheses are
    contradictory (the theorem is vacuously true) — retire the target."""
    _n, _b, sep, end = _locate(lean_text)
    return lean_text[:sep] + ": False " + lean_text[end:]




def disproof_goal(lean_text: str) -> str:
    """The disproof probe: the stub with its conclusion `C` replaced by `¬ (C)`,
    keeping imports + binders. A clean proof means the statement is false as
    written — retire the target."""
    _n, _b, sep, end = _locate(lean_text)
    concl = lean_text[sep + 1:end].strip()
    return lean_text[:sep] + ": ¬ (" + concl + ") " + lean_text[end:]
