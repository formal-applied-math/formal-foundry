"""Tests for the token-pacing pipeline logic (pipeline_lib)."""

import os
import tempfile

import pipeline_lib as P
from pipeline_lib import PipelineConfig

DAY = P.SECONDS_PER_DAY


def test_defaults():
    c = PipelineConfig()
    assert c.interval_days == 3
    assert c.tokens_per_issue_cap == 500_000
    assert c.escalate_hard_cap == 2_000_000
    assert c.max_issues_per_tick == 1


def test_load_config_from_toml():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "pipeline.toml")
        with open(p, "w") as f:
            f.write("[pipeline]\ninterval_days = 7\ntokens_per_issue_cap = 250000\n")
        c = PipelineConfig.load(p)
        assert c.interval_days == 7
        assert c.tokens_per_issue_cap == 250_000
        assert c.monthly_token_allowance == 8_000_000  # default preserved


def test_load_config_missing_returns_defaults():
    assert PipelineConfig.load(None).interval_days == 3
    assert PipelineConfig.load("/no/such/file.toml").interval_days == 3


def test_roll_month_resets_counter_on_boundary():
    st = P.new_state("2026-07")
    st["tokens_spent_this_month"] = 400_000
    same = P.roll_month(st, "2026-07")
    assert same["tokens_spent_this_month"] == 400_000
    rolled = P.roll_month(st, "2026-08")
    assert rolled["tokens_spent_this_month"] == 0
    assert rolled["month"] == "2026-08"


def test_budget_and_affordability():
    cfg = PipelineConfig(monthly_token_allowance=1_000_000, tokens_per_issue_cap=500_000,
                         escalate_hard_cap=2_000_000)
    st = P.new_state("2026-07")
    assert P.budget_remaining(st, cfg) == 1_000_000
    assert P.can_afford(st, cfg)                       # 1M left, 500k needed
    assert not P.can_afford(st, cfg, difficulty="difficulty:hard")  # needs 2M
    st["tokens_spent_this_month"] = 600_000
    assert not P.can_afford(st, cfg)                   # only 400k left < 500k cap
    assert P.budget_remaining(st, cfg) == 400_000


def test_issue_budget_escalates_for_hard():
    cfg = PipelineConfig()
    assert P.issue_budget(cfg) == 500_000
    assert P.issue_budget(cfg, "difficulty:small") == 500_000
    assert P.issue_budget(cfg, "difficulty:hard") == 2_000_000


def test_due_respects_interval_and_fresh_state():
    cfg = PipelineConfig(interval_days=3)
    fresh = P.new_state("2026-07")
    assert P.due(fresh, cfg, now_epoch=1_000_000)      # last_tick 0 → always due
    recent = dict(fresh, last_tick_epoch=1_000_000)
    assert not P.due(recent, cfg, now_epoch=1_000_000 + 2 * DAY)
    assert P.due(recent, cfg, now_epoch=1_000_000 + 3 * DAY)


def test_next_target_skips_attempted():
    st = P.new_state("2026-07")
    st["attempted_issues"] = ["issue-53"]
    cands = [{"id": "issue-53"}, {"id": "issue-88"}, {"id": "issue-108"}]
    assert P.next_target(cands, st)["id"] == "issue-88"
    st["attempted_issues"] = ["issue-53", "issue-88", "issue-108"]
    assert P.next_target(cands, st) is None


def test_record_attempt_charges_and_stamps():
    cfg = PipelineConfig()
    st = P.new_state("2026-07")
    st2 = P.record_attempt(st, "issue-53", tokens=123_000, outcome="pass",
                           now_epoch=1_700_000_000, now_ym="2026-07")
    assert st2["attempted_issues"] == ["issue-53"]
    assert st2["tokens_spent_this_month"] == 123_000
    assert st2["last_tick_epoch"] == 1_700_000_000
    assert st2["history"][-1]["outcome"] == "pass"
    # original state not mutated
    assert st["attempted_issues"] == []


def test_record_attempt_rolls_month_before_charging():
    st = P.new_state("2026-07")
    st["tokens_spent_this_month"] = 900_000
    st2 = P.record_attempt(st, "x", tokens=100_000, outcome="pass",
                           now_epoch=1, now_ym="2026-08")
    assert st2["month"] == "2026-08"
    assert st2["tokens_spent_this_month"] == 100_000  # reset then charged, not 1_000_000


def test_state_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "state.json")
        st = P.record_attempt(P.new_state("2026-07"), "issue-1", 50_000, "pass", 10, "2026-07")
        P.save_state(p, st)
        loaded = P.load_state(p)
        assert loaded["attempted_issues"] == ["issue-1"]
        assert loaded["tokens_spent_this_month"] == 50_000


def test_load_state_missing_returns_fresh():
    assert P.load_state("/no/such/state.json")["attempted_issues"] == []


def test_config_has_fanout_and_repair_defaults():
    cfg = PipelineConfig()
    assert cfg.fanout == 8
    assert cfg.repair_rounds == 2
    assert cfg.tokens_per_attempt == 60000
