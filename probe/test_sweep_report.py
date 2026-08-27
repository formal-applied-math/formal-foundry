import sweep_report as sr


def _rec(**kw):
    base = {"arm": "mathfin", "entry_id": "e", "domain": "d", "thm": "t",
            "status": "full", "provenance": "human", "binder": "h",
            "verdict": "not_shown_unnecessary", "sweep_proves_original": True,
            "closing_tactic": None, "elapsed_s": 0.0}
    base.update(kw)
    return base


def test_rate_counts_only_binders_whose_entry_passed_the_power_control():
    recs = [
        _rec(entry_id="a", verdict="power_control", binder=None),
        _rec(entry_id="a", verdict="certified_unnecessary"),
        _rec(entry_id="b", verdict="power_control", binder=None,
             sweep_proves_original=False),
        _rec(entry_id="b", verdict="not_shown_unnecessary",
             sweep_proves_original=False),
    ]
    r = sr.rates(recs)[("mathfin", "d", "full")]
    assert r["probed"] == 1 and r["certified"] == 1 and r["rate"] == 1.0
    assert r["blind_entries"] == 1 and r["reachable_entries"] == 1


def test_daemon_errors_are_in_no_denominator():
    recs = [
        _rec(entry_id="a", verdict="power_control", binder=None),
        _rec(entry_id="a", verdict="daemon_error"),
        _rec(entry_id="a", binder="h2", verdict="certified_unnecessary"),
    ]
    r = sr.rates(recs)[("mathfin", "d", "full")]
    assert r["probed"] == 1 and r["certified"] == 1


def test_rate_is_none_rather_than_zero_when_nothing_was_probed():
    recs = [_rec(entry_id="a", verdict="power_control", binder=None,
                 sweep_proves_original=False)]
    r = sr.rates(recs)[("mathfin", "d", "full")]
    assert r["probed"] == 0 and r["rate"] is None


def test_refined_defects_are_pulled_from_provenance(tmp_path):
    import json
    (tmp_path / "d.json").write_text(json.dumps({"description": "d", "theorems": [
        {"id": "x", "code": {"lean": "theorem t : True := trivial"},
         "metadata": {"provenance": {"source": "leanstral-autoform", "issue": 161,
                                     "refined": "spurious guard dropped"}}},
        {"id": "y", "code": {"lean": "theorem u : True := trivial"}, "metadata": {}},
    ]}), encoding="utf-8")
    got = sr.refined_defects(str(tmp_path / "*.json"))
    assert got == [{"entry_id": "x", "issue": 161, "refined": "spurious guard dropped"}]
