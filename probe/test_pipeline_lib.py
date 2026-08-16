"""Tests for the token-pacing pipeline logic (pipeline_lib)."""

import os
import tempfile

import pipeline_lib as P
from pipeline_lib import PipelineConfig

import domain_pack

PACK = domain_pack.load("mathfin")


DAY = P.SECONDS_PER_DAY


def test_defaults():
    c = PipelineConfig()
    assert c.interval_days == 2
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
    assert PipelineConfig.load(None).interval_days == 2
    assert PipelineConfig.load("/no/such/file.toml").interval_days == 2


def test_decompose_config_defaults_off_and_loads():
    from pipeline_lib import DecomposeConfig
    d = DecomposeConfig()
    assert d.enabled is False and d.max_leaves == 3 and d.leaf_max_turns == 40
    assert DecomposeConfig.load(None).enabled is False   # off by default (tag-only path)
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "pipeline.toml")
        with open(p, "w") as f:
            f.write("[decompose]\nenabled = true\nmax_leaves = 5\n")
        c = DecomposeConfig.load(p)
        assert c.enabled is True and c.max_leaves == 5 and c.leaf_max_turns == 40


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


def test_due_tolerates_the_run_duration_of_a_fixed_time_cron():
    """A fixed-minute cron + a stamp taken at RECORD time (fire + run duration)
    puts the next firing just UNDER the interval — without slack the cron skips
    it and the cadence silently halves. Live durations are 45-85 min; the job
    ceiling is 120. Holds at any interval, so it survives a cadence change."""
    fire = 1_000_000                                   # the previous cron minute
    for interval in (1, 2, 3):
        cfg = PipelineConfig(interval_days=interval)
        for run_minutes in (46, 85, 120):              # stamp = fire + duration
            stamped = dict(P.new_state("2026-08"), last_tick_epoch=fire + run_minutes * 60)
            assert P.due(stamped, cfg, now_epoch=fire + interval * DAY), \
                f"interval={interval}d skipped after a {run_minutes}min run"
        # an off-cadence firing is still guarded (that is what --force is for)
        justran = dict(P.new_state("2026-08"), last_tick_epoch=fire)
        assert not P.due(justran, cfg, now_epoch=fire + interval * DAY - 5 * 3600)


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


def test_config_has_max_turns_default():
    # the vibe ⇄ lean-lsp-mcp harness's depth lever (replaced the retired
    # text-loop's fanout/repair_rounds/tokens_per_attempt knobs).
    assert PipelineConfig().max_turns == 40


# --- ground-truth duplicate guard (backlog T) ---------------------------------

def test_next_target_still_skips_attempted_without_a_claim_fn():
    st = {"attempted_issues": ["cal-bk-161"]}
    got = P.next_target([{"id": "cal-bk-161"}, {"id": "cal-bk-162"}], st)
    assert got["id"] == "cal-bk-162"


def test_next_target_skips_a_target_an_open_pr_already_claims():
    # the #161/#162 failure: state lost the attempt, but a PR was already open
    st = {"attempted_issues": []}
    got = P.next_target([{"id": "cal-bk-161"}, {"id": "cal-bk-162"}], st,
                        claimed_fn=lambda c: c["id"] == "cal-bk-161")
    assert got["id"] == "cal-bk-162"


def test_a_broken_ground_truth_lookup_never_stalls_the_tick():
    def boom(c):
        raise RuntimeError("gh: network unreachable")

    got = P.next_target([{"id": "cal-bk-161"}], {"attempted_issues": []}, claimed_fn=boom)
    assert got["id"] == "cal-bk-161"


def test_queue_claimed_sees_a_drafted_entry(tmp_path):
    (tmp_path / "cal-bk-161.entry.json").write_text("{}")
    assert P.queue_claimed({"id": "cal-bk-161"}, str(tmp_path)) is True
    assert P.queue_claimed({"id": "cal-bk-999"}, str(tmp_path)) is False


class _Res:
    def __init__(self, stdout):
        self.stdout = stdout


def test_pr_claimed_matches_only_a_closing_reference():
    rows = '[{"number": 163, "title": "autoform: GainToPain", "body": "closes #161"}]'
    assert P.pr_claimed({"id": "cal-bk-161"}, repo=PACK.slug, run_fn=lambda *a, **k: _Res(rows)) is True
    assert P.pr_claimed({"id": "cal-bk-162"}, repo=PACK.slug, run_fn=lambda *a, **k: _Res(rows)) is False


def test_pr_claimed_ignores_a_bare_mention():
    rows = '[{"number": 9, "title": "x", "body": "related to #161 but not closing it"}]'
    assert P.pr_claimed({"id": "cal-bk-161"}, repo=PACK.slug, run_fn=lambda *a, **k: _Res(rows)) is False


def test_pr_claimed_is_false_when_gh_is_unavailable():
    def boom(*a, **k):
        raise FileNotFoundError("gh")
    assert P.pr_claimed({"id": "cal-bk-161"}, repo=PACK.slug, run_fn=boom) is False
    assert P.pr_claimed({"id": "cal-bk-161"}, repo=PACK.slug,
                        run_fn=lambda *a, **k: _Res("not json")) is False


def test_pr_claimed_reads_the_issue_number_off_the_target_id():
    seen = {}

    def fake(cmd, **k):
        seen["cmd"] = cmd
        return _Res('[{"number": 1, "title": "t", "body": "Closes #162"}]')

    assert P.pr_claimed({"id": "cal-bk-162"}, repo=PACK.slug, run_fn=fake) is True
    assert "--state" in seen["cmd"] and "open" in seen["cmd"]
