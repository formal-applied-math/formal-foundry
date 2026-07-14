"""Tests for the draft-elaboration eval's pure accounting (no models / daemon)."""

from __future__ import annotations

import eval_draft as ev


_ROWS = [
    {"issue": 67, "elaborated": True, "depth_pass": True, "tokens": 50000, "wall_s": 120.0},
    {"issue": 61, "elaborated": True, "depth_pass": False, "tokens": 30000, "wall_s": 90.0},
    {"issue": 53, "elaborated": False, "depth_pass": None, "tokens": 20000, "wall_s": 60.0},
]


def test_summarize_computes_rates():
    s = ev.summarize(_ROWS)
    assert s["n"] == 3
    assert s["elaborated"] == 2 and s["elaboration_rate"] == round(2 / 3, 3)
    assert s["deep"] == 1 and s["depth_rate"] == round(1 / 3, 3)
    assert s["tokens"] == 100000


def test_summarize_empty_is_safe():
    s = ev.summarize([])
    assert s["n"] == 0 and s["elaboration_rate"] == 0.0 and s["depth_rate"] == 0.0


def test_format_table_has_header_and_a_row_per_issue():
    table = ev.format_table(_ROWS)
    lines = table.splitlines()
    assert "issue" in lines[0] and "elab" in lines[0] and "depth" in lines[0]
    assert len(lines) == 1 + len(_ROWS)
    assert "67" in table and "61" in table and "53" in table
