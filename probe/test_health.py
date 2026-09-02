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
