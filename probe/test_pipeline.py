"""Tests for the pipeline tick brain (pipeline.py plan/record)."""

import argparse
import json
import os
import tempfile

import pipeline as PL
import pipeline_lib as P

DAY = P.SECONDS_PER_DAY


def _plan(queue_targets, state, cfg_toml=None, now_epoch=1_700_000_000, force=False):
    with tempfile.TemporaryDirectory() as d:
        cfg = os.path.join(d, "pipeline.toml")
        if cfg_toml:
            open(cfg, "w").write(cfg_toml)
        statep = os.path.join(d, "state.json")
        P.save_state(statep, state)
        queuep = os.path.join(d, "queue.json")
        json.dump({"targets": queue_targets}, open(queuep, "w"))
        import io
        import contextlib
        ns = argparse.Namespace(config=cfg if cfg_toml else "/none", state=statep,
                                queue=queuep, force=force, now_epoch=now_epoch, now_ym=None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            PL.cmd_plan(ns)
        return json.loads(buf.getvalue())


def test_plan_runs_first_unattempted():
    st = P.new_state("2026-07")
    dec = _plan([{"id": "cal-bk-1"}, {"id": "cal-bk-2"}], st)
    assert dec["action"] == "run"
    assert dec["target"]["id"] == "cal-bk-1"
    assert dec["budget"] == 500_000


def test_plan_skips_attempted_and_picks_next():
    st = P.new_state("2026-07")
    st["attempted_issues"] = ["cal-bk-1"]
    dec = _plan([{"id": "cal-bk-1"}, {"id": "cal-bk-2"}], st)
    assert dec["target"]["id"] == "cal-bk-2"


def test_plan_skips_when_not_due():
    st = dict(P.new_state("2026-07"), last_tick_epoch=1_700_000_000)
    dec = _plan([{"id": "x"}], st, now_epoch=1_700_000_000 + DAY)  # 1 day < 3
    assert dec["action"] == "skip" and dec["reason"] == "not_due"


def test_plan_force_ignores_due():
    st = dict(P.new_state("2026-07"), last_tick_epoch=1_700_000_000)
    dec = _plan([{"id": "x"}], st, now_epoch=1_700_000_000 + DAY, force=True)
    assert dec["action"] == "run"


def test_plan_skips_when_no_targets():
    dec = _plan([], P.new_state("2026-07"))
    assert dec["action"] == "skip" and dec["reason"] == "no_unattempted_targets"


def test_plan_skips_when_budget_exhausted():
    # month must match the plan clock (epoch 1.7e9 = 2023-11 UTC) or roll_month
    # would reset the spent counter for a new month.
    st = P.new_state("2023-11")
    st["tokens_spent_this_month"] = 7_800_000  # <500k left of 8M default
    dec = _plan([{"id": "x"}], st)
    assert dec["action"] == "skip" and dec["reason"] == "budget_exhausted"


def test_plan_hard_difficulty_escalates_budget():
    st = P.new_state("2026-07")
    dec = _plan([{"id": "x", "difficulty": "difficulty:hard"}], st)
    assert dec["budget"] == 2_000_000


def test_record_charges_cap_and_persists():
    with tempfile.TemporaryDirectory() as d:
        statep = os.path.join(d, "state.json")
        P.save_state(statep, P.new_state("2026-07"))
        ns = argparse.Namespace(config="/none", state=statep, id="cal-bk-1",
                                difficulty="difficulty:small", tokens=None,
                                outcome="pass", now_epoch=1_700_000_000, now_ym=None)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            PL.cmd_record(ns)
        out = json.loads(buf.getvalue())
        assert out["charged"] == 500_000
        st = P.load_state(statep)
        assert st["attempted_issues"] == ["cal-bk-1"]
        assert st["history"][-1]["outcome"] == "pass"
