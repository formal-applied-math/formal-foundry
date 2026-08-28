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


def test_wilson_interval_brackets_the_point_estimate():
    lo, hi = sr.wilson(5, 20)
    assert lo < 0.25 < hi and 0.0 < lo and hi < 1.0


def test_wilson_interval_stays_inside_the_unit_interval_at_zero_and_one():
    assert sr.wilson(0, 20)[0] == 0.0
    assert sr.wilson(20, 20)[1] == 1.0


def test_wilson_interval_is_none_when_nothing_was_probed():
    assert sr.wilson(0, 0) is None


def test_rates_carry_an_interval_because_the_arm_is_a_sample():
    recs = [
        _rec(entry_id="a", verdict="power_control", binder=None),
        _rec(entry_id="a", verdict="certified_unnecessary"),
        _rec(entry_id="a", binder="h2", verdict="not_shown_unnecessary"),
    ]
    r = sr.rates(recs)[("mathfin", "d", "full")]
    lo, hi = r["ci95"]
    assert lo < r["rate"] < hi


def test_a_sampled_rate_reports_how_many_entries_the_draw_touched():
    recs = [
        _rec(entry_id="a", verdict="power_control", binder=None),
        _rec(entry_id="a", verdict="certified_unnecessary"),
        _rec(entry_id="b", verdict="power_control", binder=None),
        _rec(entry_id="b", verdict="not_shown_unnecessary"),
    ]
    r = sr.rates(recs)[("mathfin", "d", "full")]
    assert r["probed_entries"] == 2
