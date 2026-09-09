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


def test_the_committed_scoreboard_matches_the_log():
    """The doc is generated from `runs/ab-decomposer.jsonl` by every decompose tick, and
    it is the A/B evidence the 2026-09-30 decision gate reads. It sat at "(no rows yet —
    first decompose attempt pending)" from 2026-07-18 while seven decompose failures
    accumulated in the log, because the tick regenerated it and the CI persist step
    staged `pipeline_state.json runs targets/queue` — never `docs/`. The evidence
    surface existed and was silently empty, which is how the five-week stall stayed
    invisible.

    Asserting the committed doc against the log catches that whatever the cause."""
    import os
    import provenance
    from scoreboard import _END, _START, render_scoreboard

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    md = os.path.join(root, "docs", "research", "ab-decomposer.md")
    rows = provenance.read_jsonl(os.path.join(root, "runs", "ab-decomposer.jsonl"))
    text = open(md, encoding="utf-8").read()
    assert _START in text and _END in text, "scoreboard markers missing — the refresh no-ops"
    committed = text.split(_START, 1)[1].split(_END, 1)[0].strip()
    assert committed == render_scoreboard(rows).strip(), (
        "docs/research/ab-decomposer.md is stale against runs/ab-decomposer.jsonl "
        f"({len(rows)} rows) — regenerate with scoreboard.update_scoreboard_md")


def test_the_note_is_rendered_beside_the_outcome():
    """`ab_row` has carried a `note` since it was written and `render_scoreboard` never
    showed it — dead data. It is where the failure reason lands (and, by the file's own
    hand-annotation convention, where a correction lands), so a verdict without it is a
    bare outcome a reader cannot check."""
    from scoreboard import ab_row, render_scoreboard
    md = render_scoreboard([ab_row(target="t", arm="decompose", outcome="max_rounds",
                                   ts="2026-09-09T00:00:00", note="unknown namespace")])
    assert "note" in md.split("\n")[0]
    assert "unknown namespace" in md


def test_a_long_note_is_truncated_so_the_table_stays_readable():
    from scoreboard import ab_row, render_scoreboard
    md = render_scoreboard([ab_row(target="t", arm="decompose", outcome="pass",
                                   ts="x", note="y" * 400)])
    assert "…" in md
    assert max(len(line) for line in md.split("\n")) < 260


def test_a_row_without_a_note_renders_an_empty_cell():
    from scoreboard import ab_row, render_scoreboard
    md = render_scoreboard([ab_row(target="t", arm="cron", outcome="pass", ts="x")])
    assert md.rstrip().endswith("|")


def test_the_engine_is_recorded_beside_the_arm():
    """`arm` says WHICH PATH ran (direct vs decomposed); it was also carrying WHICH
    MODEL proved, implicitly, because there was only ever one. A frontier prover makes
    those two different questions, and a scoreboard that cannot express "same path,
    different engine" cannot record the comparison at all."""
    from scoreboard import ab_row
    r = ab_row(target="t", arm="cron", engine="claude", outcome="pass", ts="x")
    assert r["arm"] == "cron" and r["engine"] == "claude"


def test_the_engine_defaults_to_the_incumbent_so_old_rows_read_unchanged():
    from scoreboard import ab_row
    assert ab_row(target="t", arm="cron", outcome="pass", ts="x")["engine"] == "leanstral"


def test_an_unknown_engine_is_rejected():
    """Same discipline the arm guard had: a typo must not silently become a new column
    in the A/B."""
    import pytest
    from scoreboard import ab_row
    with pytest.raises(ValueError):
        ab_row(target="t", arm="cron", engine="gpt-9", outcome="pass", ts="x")


def test_an_unknown_arm_is_still_rejected():
    import pytest
    from scoreboard import ab_row
    with pytest.raises(ValueError):
        ab_row(target="t", arm="nonsense", outcome="pass", ts="x")


def test_a_legacy_row_without_an_engine_renders_as_the_incumbent():
    """Every row on disk predates this field; none of them may render blank, or the
    table would imply the engine was unknown when it is the only one that ever ran."""
    from scoreboard import render_scoreboard
    md = render_scoreboard([{"target": "t", "arm": "decompose", "outcome": "max_rounds",
                             "ts": "2026-08-31T14:03:08", "tokens": 0}])
    assert "leanstral" in md
