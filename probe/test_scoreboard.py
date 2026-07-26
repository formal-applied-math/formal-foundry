"""Tests for the A/B decomposition scoreboard (Task 2.6). Pure — no Lean, no API."""

from __future__ import annotations

import json

import pytest

from scoreboard import (ab_row, append_ab_row, render_scoreboard,
                        update_scoreboard_md)


def test_ab_row_validates_arm_and_shape():
    r = ab_row(target="cal-bk-99", arm="decompose", outcome="pass",
               ts="2026-07-18T00:00:00", leaves_total=3, leaves_closed=3, tokens=120000)
    assert r["arm"] == "decompose" and r["leaves_closed"] == 3
    assert r["refinery_minutes"] is None            # hand-filled at merge, not by the machine
    with pytest.raises(ValueError):                 # no centaur/claude arm — Mistral-only
        ab_row(target="x", arm="claude", outcome="pass", ts="t")


def test_append_and_render_scoreboard(tmp_path):
    append_ab_row(str(tmp_path), ab_row(target="a", arm="cron", outcome="fail", ts="t1"))
    append_ab_row(str(tmp_path), ab_row(target="a", arm="decompose", outcome="pass",
                                        ts="t2", leaves_total=3, leaves_closed=3, tokens=9))
    rows = [json.loads(line) for line in open(tmp_path / "ab-decomposer.jsonl")]
    md = render_scoreboard(rows)
    assert "| cron |" in md and "| decompose |" in md
    assert "3/3" in md and "—" in md               # leaves shown only for the decompose arm


def test_update_scoreboard_md_replaces_between_markers(tmp_path):
    md = tmp_path / "ab-decomposer.md"
    md.write_text("intro\n<!-- SCOREBOARD:START -->\nold\n<!-- SCOREBOARD:END -->\nfooter\n",
                  encoding="utf-8")
    append_ab_row(str(tmp_path), ab_row(target="a", arm="cron", outcome="pass", ts="t1"))
    update_scoreboard_md(str(md), str(tmp_path))
    out = md.read_text(encoding="utf-8")
    assert "old" not in out and "footer" in out and "| cron |" in out


def test_update_scoreboard_md_tolerates_a_junk_log_line(tmp_path):
    # reading the log now goes through provenance.read_jsonl (tolerant) — a truncated /
    # junk line no longer crashes the per-tick scoreboard refresh.
    md = tmp_path / "ab-decomposer.md"
    md.write_text("<!-- SCOREBOARD:START -->\n<!-- SCOREBOARD:END -->\n", encoding="utf-8")
    (tmp_path / "ab-decomposer.jsonl").write_text(
        '{"target": "a", "arm": "cron", "outcome": "pass", "ts": "t1"}\nhalf-written{\n',
        encoding="utf-8")
    update_scoreboard_md(str(md), str(tmp_path))          # must not raise
    assert "| cron |" in md.read_text(encoding="utf-8")
