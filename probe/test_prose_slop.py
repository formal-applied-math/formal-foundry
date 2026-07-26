"""Pure tests for the prose-slop screen (item SP4). No daemon, no API."""
from __future__ import annotations

import prose_slop as ps


def test_clean_terse_docstring_is_slop_free():
    good = "The gain-to-pain ratio: total return over the sum of absolute losses."
    assert ps.is_slop_free(good)
    assert ps.prose_slop_report(good) == {"flags": [], "count": 0}


def test_flags_marketing_filler():
    r = ps.prose_slop_report("A powerful, cutting-edge framework to delve into pricing.")
    assert set(r["flags"]) >= {"powerful", "cutting-edge", "delve"}
    assert r["count"] >= 3


def test_flags_signposts_and_grand_closers():
    r = ps.prose_slop_report(
        "Moreover, this plays a crucial role. It is worth noting the result.")
    assert "moreover" in r["flags"]
    assert "plays a crucial role" in r["flags"]
    assert "it is worth noting" in r["flags"]


def test_does_not_flag_finance_or_lean_vocabulary():
    # high precision: real domain words must not trip the screen
    for legit in ("the leverage ratio of the portfolio",
                  "rename to avoid an underscore in the definition name",
                  "a robust estimator of realized variance",
                  "the rich structure of the filtration",
                  "if additionally |x - r| <= d, every iterate stays in the interval",
                  "it additionally asks f_t to be continuous",
                  "these compose formally, not only in prose"):
        assert ps.is_slop_free(legit), legit


def test_flags_are_unique_and_sorted():
    r = ps.prose_slop_report("powerful powerful POWERFUL and delve and delve")
    assert r["flags"] == sorted(set(r["flags"]))
    assert r["flags"].count("powerful") == 1


def test_empty_text_is_clean():
    assert ps.prose_slop_report("") == {"flags": [], "count": 0}
    assert ps.prose_slop_report(None) == {"flags": [], "count": 0}
