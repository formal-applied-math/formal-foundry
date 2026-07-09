"""Tests for build_manifest helpers (pure; the daemon-gated build is not tested here)."""

from build_manifest import parse_pointers


def test_parse_pointers_reads_comment_line():
    code = (
        "import MathFin.BlackScholes.Call\n"
        "-- pointers: MathFin/BlackScholes/Call.lean, MathFin/BlackScholes/Forward.lean\n"
        "theorem t : True := by sorry\n"
    )
    assert parse_pointers(code) == [
        "MathFin/BlackScholes/Call.lean",
        "MathFin/BlackScholes/Forward.lean",
    ]


def test_parse_pointers_empty_when_absent():
    assert parse_pointers("theorem t : True := by sorry\n") == []


def test_parse_pointers_trims_and_drops_blanks():
    assert parse_pointers("--  pointers:  A.lean ,, B.lean ,\n") == ["A.lean", "B.lean"]
