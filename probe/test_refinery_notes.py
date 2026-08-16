"""Tests for the first-pass refinery punch list (Task 2.7). Pure — fake chat_fn."""

from __future__ import annotations

from refinery_notes import refinery_messages, refinery_notes

import domain_pack

PACK = domain_pack.load("mathfin")


_CAND = "theorem foo : True := by trivial"


def test_refinery_notes_returns_the_model_checklist():
    checklist = "### zero slop\n- [ ] unused `have h` on line 3"
    r = refinery_notes(PACK, _CAND, chat_fn=lambda msgs: (checklist, 42))
    assert r == checklist
    # the review prompt carries the candidate + the mechanical lenses, not the taste half
    msgs = refinery_messages(PACK, _CAND)
    assert _CAND in msgs[-1]["content"]
    sys = msgs[0]["content"].lower()
    assert "slop" in sys and "wrapper" in sys and "golf" in sys


def test_refinery_notes_is_soft_and_never_raises():
    # a chat failure must NOT block the PR — soft fallback, never an exception
    def boom(_msgs):
        raise RuntimeError("api down")
    r = refinery_notes(PACK, _CAND, chat_fn=boom)
    assert "unavailable" in r and "api down" in r
    # empty reply → a soft note, still a string
    assert refinery_notes(PACK, _CAND, chat_fn=lambda m: ("", 0)).strip()
    # tolerates a bare-string chat_fn (not just the (content, tokens) tuple)
    assert refinery_notes(PACK, _CAND, chat_fn=lambda m: "- [ ] fine") == "- [ ] fine"
