"""The check that would have caught five weeks of nothing."""
import health


def _state(*outcomes, month="2026-09"):
    return {"month": month,
            "history": [{"epoch": 1785000000 + 86400 * i, "id": f"t{i}",
                         "outcome": o, "tokens": 0} for i, o in enumerate(outcomes)]}


def test_a_barren_streak_is_counted_from_the_most_recent_tick():
    h = health.assess(_state("pass", "max_rounds", "max_rounds", "max_rounds"))
    assert h["barren_streak"] == 3
    assert h["last_pass_id"] == "t0"


def test_a_pass_resets_the_streak():
    assert health.assess(_state("max_rounds", "max_rounds", "pass"))["barren_streak"] == 0


def test_a_foundry_that_never_passed_is_all_barren():
    assert health.assess(_state("error", "max_rounds"))["barren_streak"] == 2
    assert health.assess(_state("error", "max_rounds"))["last_pass_id"] is None


def test_no_history_is_not_an_alarm():
    h = health.assess({"history": []})
    assert h["barren_streak"] == 0 and h["alarm"] is False


def test_the_streak_raises_an_alarm_at_the_threshold():
    assert health.assess(_state("pass", "max_rounds", "max_rounds"), threshold=3)["alarm"] is False
    assert health.assess(_state("pass", *["max_rounds"] * 3), threshold=3)["alarm"] is True


def test_the_real_streak_this_check_was_built_for():
    """The production history on 2026-09-01: four passes in July, then five failures."""
    h = health.assess(_state("pass", "pass", "pass", "pass",
                             "max_rounds", "max_rounds", "max_rounds",
                             "max_rounds", "max_rounds"))
    assert h["barren_streak"] == 5 and h["alarm"] is True
    assert h["passes"] == 4 and h["ticks"] == 9


def test_render_names_the_streak_and_the_last_success():
    text = health.render(health.assess(_state("pass", "max_rounds", "max_rounds",
                                              "max_rounds")))
    assert "3" in text and "t0" in text


# --- every tick, not just the recorded ones --------------------------------------

def _tick(action, reason=None, outcome=None, infra=False):
    return {"action": action, "reason": reason, "outcome": outcome, "infra_failure": infra}


def test_the_ticks_that_recorded_nothing_are_counted():
    """2026-07-29..08-18 and 09-25..27: a blocked manifest skipped every tick, and the
    history check could not see a single one of them."""
    rows = [_tick("run", outcome="pass")] + [_tick("skip", "no_unattempted_targets")] * 2
    h = health.assess_ticks(rows)
    assert h["idle_streak"] == 2 and h["idle_alarm"] is True


def test_a_crashed_prover_is_idle_even_when_it_wrote_an_outcome():
    rows = [_tick("run", outcome="error", infra=True), _tick("run", outcome="error", infra=True)]
    assert health.assess_ticks(rows)["idle_alarm"] is True


def test_a_real_verdict_resets_the_idle_streak_and_not_due_is_neutral():
    rows = [_tick("skip", "no_unattempted_targets"), _tick("run", outcome="max_rounds"),
            _tick("skip", "not_due")]
    assert health.assess_ticks(rows)["idle_streak"] == 0


def test_record_tick_round_trips_and_never_raises_on_shell_input(tmp_path):
    path = str(tmp_path / "ticks.jsonl")
    health.record_tick(path, tag="t1", action="run", target="cal-bk-83", outcome="",
                       infra_failure="1", infra_reason="prover canary failed", exit_code="1")
    health.record_tick(path, exit_code="not-a-number")
    rows = health.load_ticks(path)
    assert rows[0]["infra_failure"] is True and rows[0]["outcome"] is None
    assert rows[1]["action"] == "aborted" and rows[1]["exit_code"] == -1


def test_the_cli_fails_the_job_on_an_idle_alarm(tmp_path):
    import json
    state = tmp_path / "state.json"
    state.write_text(json.dumps(_state("pass")))
    ticks = tmp_path / "ticks.jsonl"
    for _ in range(2):
        health.main(["record-tick", "--ticks", str(ticks), "--action", "skip",
                     "--reason", "no_unattempted_targets"])
    assert health.main(["--state", str(state), "--ticks", str(ticks), "--fail-on-alarm"]) == 1
    assert health.main(["--state", str(state), "--fail-on-alarm"]) == 0   # history alone: blind
