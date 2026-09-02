"""The prompts must treat over-assuming as the failure it is (post-2026-07-31 review).

Every hypothesis rule in this file used to point one way — do not weaken, do not drop,
never omit — while the failure the pipeline actually commits is the opposite: adding a
guard the issue did not ask for and the conclusion does not need. All four autoform
drafts of formal-mathfin#161/#162 did it, and the judge passed all four, because it had
no criterion to fail them on. These tests pin the symmetry so it cannot quietly regress
the next time the prompts are edited."""
from __future__ import annotations

import re

import af_prompts as P


import domain_pack

PACK = domain_pack.load("mathfin")


def _stages():
    """(name, prompt text) for every stage that constrains the statement's hypotheses."""
    return [
        ("judge", PACK.prompt("judge-system")),
        ("intent", PACK.prompt("intent-system")),
        ("formalize", PACK.prompt("formalize-system")),
    ]


def test_every_statement_stage_forbids_adding_a_hypothesis():
    for name, text in _stages():
        low = text.lower()
        assert re.search(r"not add a hypothesis|never add a hypothesis|assumes more", low), (
            f"{name} prompt constrains hypotheses in only one direction — it must also "
            "forbid ADDING one the issue does not state")


def test_every_statement_stage_still_forbids_dropping_one():
    # the symmetry is an addition, not a swap: both directions stay live
    for name, text in _stages():
        low = text.lower()
        assert "weaken" in low or "drop" in low, f"{name} lost its do-not-drop rule"


def test_the_drafter_is_told_not_to_guard_a_division_by_reflex():
    low = PACK.prompt("formalize-system").lower()
    assert "x / 0 = 0" in low
    assert "div_nonneg" in low and "mul_div_assoc" in low


def test_the_judge_knows_a_used_hypothesis_can_still_be_unnecessary():
    # the trap that made this invisible: the proof genuinely consumes the guard
    # (`h.le`, `field_simp [h]`), so "the proof uses it" reads as justification
    low = PACK.prompt("judge-system").lower()
    assert "proof using the hypothesis" in low or "proof can" in low
    assert "conclusion" in low


def test_the_guidance_names_a_hypothesis_that_must_be_kept():
    # a one-directional "drop your guards" rule would be the same bug mirrored, so the
    # prompts must show where a side condition is genuinely load-bearing
    assert "1 ≤ a / b" in PACK.prompt("formalize-system") or "comparable to 1" in PACK.prompt("formalize-system")
    assert "still required" in PACK.prompt("judge-system")
