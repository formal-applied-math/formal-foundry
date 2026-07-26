"""Prose-slop screen for the drafter's generated prose (item SP4).

`probe_lib.slop_report` screens forbidden Lean TACTICS; this screens AI-slop PROSE — the
markers a Mathlib/quant reader strikes from a docstring or an outbound PR body: marketing
filler, signpost openers, the "not only …" tic, vacuous "plays a role" grand-closers. A
terse mathematical docstring states WHAT is defined or proved; it does not sell it.

Deterministic (a machine screen, not a prompt nudge — soft prompts don't stick). The set
is HIGH-PRECISION on purpose: only markers that are slop even in one terse sentence, and
NOT finance/Lean vocabulary (no "leverage" = a real ratio, no "underscore" = a real
identifier class, no "robust" = a real estimator). Em-dashes are not flagged — the house
docstring style uses them; the outbound-only rules (lowercase, "i", no em-dash) belong to
a PR-body check, not here.
"""
from __future__ import annotations

import re

# single words that read as marketing/AI-filler in a math docstring (word-boundary match)
_FILLER = {
    "powerful", "seamless", "seamlessly", "cutting-edge", "delve", "delves", "delving",
    "tapestry", "showcase", "showcases", "boasts", "game-changer", "supercharge",
    "revolutionize", "revolutionizes", "unparalleled", "unleash", "unleashes",
}
# signpost openers / vacuous connectors (substring, case-insensitive). NOTE: "additionally"
# is deliberately absent — it has legit mathematical uses ("if additionally …", "additionally
# asks …"), confirmed against the corpus.
_SIGNPOSTS = (
    "moreover", "furthermore", "it is worth noting", "it's worth noting",
    "needless to say", "in conclusion", "state-of-the-art", "a testament to",
)
# vacuous grand-closers (substring). "not only" is deliberately absent — "not only in prose",
# "not only X but also Y" are legit constructions (corpus-confirmed).
_PHRASES = (
    "plays a key role", "plays a crucial role", "plays a vital role",
    "plays an important role", "plays a pivotal role", "plays a significant role",
    "is a cornerstone", "at its core", "in today's", "when it comes to",
)

_WORD_RE = {w: re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE) for w in _FILLER}


def prose_slop_report(text: str) -> dict:
    """`{flags, count}` — the AI-slop markers in `text` (unique, sorted), empty when clean.
    `count` is the number of distinct markers, the screen's severity signal."""
    if not text:
        return {"flags": [], "count": 0}
    low = text.lower()
    flags: set[str] = set()
    for w, rx in _WORD_RE.items():
        if rx.search(text):
            flags.add(w)
    for s in _SIGNPOSTS:
        if s in low:
            flags.add(s)
    for p in _PHRASES:
        if p in low:
            flags.add(p)
    ordered = sorted(flags)
    return {"flags": ordered, "count": len(ordered)}


def is_slop_free(text: str) -> bool:
    return prose_slop_report(text)["count"] == 0
