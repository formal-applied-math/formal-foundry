"""Pure tests for the cross-tick experience memory (item K). No daemon, no network."""
from __future__ import annotations

import json

import experience as xp
import vibe_prove as vp


# --- the roll ----------------------------------------------------------------

def test_mechanical_roll_accumulates_numbered_attempts():
    a = xp.summarize("", {"outcome": "fail_gate", "reason": "compile_or_sorry"}, index=1)
    b = xp.summarize(a, {"outcome": "max_rounds"}, index=2)
    assert "attempt 1" in b and "attempt 2" in b        # attempt 1 survives the roll
    assert "compile_or_sorry" in b


def test_summarizer_output_replaces_rather_than_appends():
    """The point of a rolling notebook: the summariser's text IS the new notebook, so the
    prior text is not also carried. Appending is what makes a memory grow without bound."""
    prior = "attempt 1: outcome: fail_gate; reason: axiom_dirty"
    out = xp.summarize(prior, {"outcome": "max_rounds"}, index=2,
                       summarize_fn=lambda msgs: ("condensed notebook", 12))
    assert out == "condensed notebook"
    assert "axiom_dirty" not in out


def test_summarizer_sees_both_prior_and_new_attempt():
    seen = {}

    def fake(msgs):
        seen["user"] = msgs[-1]["content"]
        return "ok", 1

    xp.summarize("PRIOR NOTES", {"outcome": "fail_gate", "reason": "lint:['x']"},
                 index=3, summarize_fn=fake)
    assert "PRIOR NOTES" in seen["user"]                # or it cannot preserve history
    assert "lint:['x']" in seen["user"]


def test_raising_summarizer_falls_back_to_mechanical():
    """Memory is attached to the failure path; it must never become a second failure."""
    def boom(_msgs):
        raise RuntimeError("model down")

    out = xp.summarize("", {"outcome": "fail_gate", "reason": "axiom_dirty"},
                       index=1, summarize_fn=boom)
    assert "axiom_dirty" in out


def test_empty_summarizer_reply_falls_back_to_mechanical():
    out = xp.summarize("", {"outcome": "max_rounds"}, index=1,
                       summarize_fn=lambda _m: ("   ", 0))
    assert "max_rounds" in out


def test_roll_is_bounded_and_marks_the_elision():
    notebook = ""
    for i in range(1, 400):
        notebook = xp.summarize(notebook, {"outcome": "fail_gate", "notes": "x" * 80},
                                index=i)
    assert len(notebook) <= xp.MAX_EXPERIENCE_CHARS + len("[...earlier attempts elided...]\n")
    assert "elided" in notebook
    assert "attempt 399" in notebook                    # the newest lesson is the kept one


def test_llm_summary_is_truncated_to_the_cap():
    out = xp.summarize("", {"outcome": "fail_gate"}, index=1,
                       summarize_fn=lambda _m: ("y" * 99_999, 5))
    assert len(out) == xp.MAX_EXPERIENCE_CHARS


def test_digest_keeps_only_the_informative_error_prefix():
    d = xp._attempt_digest({"outcome": "fail_gate",
                            "errors": ["first", "second", "third"]})
    assert "first" in d and "second" in d and "third" not in d


# --- the store ---------------------------------------------------------------

def test_record_persists_across_instances(tmp_path):
    p = str(tmp_path / "experience.json")
    xp.ExperienceStore(p).record("cal-bk-1", {"outcome": "fail_gate", "reason": "r"})
    reread = xp.ExperienceStore(p)
    assert reread.attempts("cal-bk-1") == 1
    assert "fail_gate" in reread.get("cal-bk-1")


def test_generation_bump_drops_the_store(tmp_path):
    """Lessons name Mathlib lemmas, so a pin bump makes them stale — drop, don't serve."""
    p = str(tmp_path / "e.json")
    xp.ExperienceStore(p).record("t", {"outcome": "fail_gate"})
    assert xp.ExperienceStore(p, generation="v-next").get("t") == ""


def test_tolerates_corrupt_file(tmp_path):
    p = tmp_path / "e.json"
    p.write_text("not json{", encoding="utf-8")
    store = xp.ExperienceStore(str(p))
    assert store.get("t") == ""
    store.record("t", {"outcome": "max_rounds"})
    assert xp.ExperienceStore(str(p)).attempts("t") == 1


def test_save_is_atomic_json(tmp_path):
    p = tmp_path / "e.json"
    xp.ExperienceStore(str(p)).record("t", {"outcome": "fail_gate"})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["generation"] == xp.EXPERIENCE_GENERATION
    assert data["entries"]["t"]["attempts"] == 1


# --- rendering ---------------------------------------------------------------

def test_render_is_silent_on_a_cold_store(tmp_path):
    """A target's FIRST attempt must get the pre-memory prompt, byte for byte."""
    assert xp.ExperienceStore(str(tmp_path / "e.json")).render("never-seen") == ""


def test_render_carries_the_notebook_and_a_diversity_nudge(tmp_path):
    store = xp.ExperienceStore(str(tmp_path / "e.json"))
    store.record("t", {"outcome": "fail_gate", "reason": "compile_or_sorry"})
    block = store.render("t")
    assert "compile_or_sorry" in block
    assert xp.DIVERSITY_INSTRUCTIONS[0] in block        # first retry reads instruction 0
    assert "the theorem statement above still governs" in block


def test_diversity_instruction_rotates_across_attempts(tmp_path):
    store = xp.ExperienceStore(str(tmp_path / "e.json"))
    seen = []
    for _ in range(len(xp.DIVERSITY_INSTRUCTIONS)):
        store.record("t", {"outcome": "max_rounds"})
        seen.append(next(d for d in xp.DIVERSITY_INSTRUCTIONS if d in store.render("t")))
    assert seen == list(xp.DIVERSITY_INSTRUCTIONS)      # rotation, not a stuck index


def test_report_flags_a_write_only_memory(tmp_path):
    store = xp.ExperienceStore(str(tmp_path / "e.json"))
    store.record("a", {"outcome": "fail_gate"})
    store.record("b", {"outcome": "fail_gate"})
    assert store.report()["retried_targets"] == 0       # nothing has READ a notebook yet
    store.record("a", {"outcome": "fail_gate"})
    r = store.report()
    assert r["retried_targets"] == 1 and r["max_attempts"] == 2 and r["targets"] == 2


# --- prompt wiring -----------------------------------------------------------

def test_task_is_unchanged_when_there_is_no_experience():
    before = vp.build_vibe_task("MathFin/X.lean", "thm", "PACK", "HINTS")
    after = vp.build_vibe_task("MathFin/X.lean", "thm", "PACK", "HINTS", experience="")
    assert before == after


def test_experience_goes_last_after_the_premises():
    """Weakest-authority block last: the statement and the context pack must not be read
    through a record of past failures."""
    task = vp.build_vibe_task("MathFin/X.lean", "thm", "PACK", "HINTS",
                              experience="NOTEBOOK")
    assert task.index("PACK") < task.index("NOTEBOOK")
    assert task.index("HINTS") < task.index("NOTEBOOK")


def test_experience_reaches_the_vibe_invocation(tmp_path, monkeypatch):
    """End to end through run_vibe_target: the rendered block must land in the `-p` task."""
    captured = {}

    def fake_run(argv, **_kw):
        captured["task"] = argv[argv.index("-p") + 1]
        return None

    main_repo = tmp_path / "main"
    (main_repo / "MathFin").mkdir(parents=True)
    vp.run_vibe_target({"id": "t", "sorry_name": "thm", "statement": "theorem thm : True := by sorry"},
                       main_repo=str(main_repo), context_pack="", max_turns=1,
                       vibe_script="/bin/true", run_fn=fake_run, experience="NOTEBOOK-HERE")
    assert "NOTEBOOK-HERE" in captured["task"]


def test_store_is_off_unless_the_config_enables_it(tmp_path):
    cfg = tmp_path / "pipeline.toml"
    cfg.write_text("[autoformalize]\nexperience = false\n", encoding="utf-8")
    assert vp._experience_store(str(tmp_path), str(cfg)) is None
    assert vp._experience_store(str(tmp_path), None) is None        # default off
    cfg.write_text("[autoformalize]\nexperience = true\n", encoding="utf-8")
    assert vp._experience_store(str(tmp_path), str(cfg)) is not None


def test_summarizer_is_none_without_a_key_or_when_disabled(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("EXPERIENCE_LLM", raising=False)
    assert vp._summarizer() is None
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-test")
    assert vp._summarizer() is not None
    monkeypatch.setenv("EXPERIENCE_LLM", "0")
    assert vp._summarizer() is None


# --- draft-side wiring (item K's other half; 19 of 22 obstructions live here) -------

import autoformalize as af


def _rec(issue, family, gate="depth", detail="type consumes no pointer def"):
    return {"issue": issue, "outcome": gate, "family": family,
            "history": [{"attempt": 1, "gate": gate, "detail": detail}]}


def test_refill_failure_is_folded_into_the_issue_notebook(tmp_path):
    store = xp.ExperienceStore(str(tmp_path / "e.json"))
    af._record_refill_experience(store, _rec(53, "depth_exhausted"))
    assert store.attempts("issue-53") == 1
    assert "depth" in store.get("issue-53")
    assert "depth_exhausted" in store.get("issue-53")


def test_non_verdict_families_teach_nothing_and_are_skipped(tmp_path):
    """Same rule load_prior_lessons uses: a win, a budget cutoff or retryable infra has
    no lesson in it, and recording one would burn a diversity rotation on noise."""
    store = xp.ExperienceStore(str(tmp_path / "e.json"))
    for family in ("budget", "infra", "infra_indeterminate"):
        af._record_refill_experience(store, _rec(60, family))
    assert store.attempts("issue-60") == 0


def test_a_seeded_issue_retires_its_notebook(tmp_path):
    """Once the issue is off the queue its notes are stale; a later regression should
    start clean rather than inherit approaches that eventually worked."""
    store = xp.ExperienceStore(str(tmp_path / "e.json"))
    af._record_refill_experience(store, _rec(72, "depth_exhausted"))
    assert store.attempts("issue-72") == 1
    af._record_refill_experience(store, _rec(72, "seeded"))
    assert store.get("issue-72") == ""
    assert xp.ExperienceStore(str(tmp_path / "e.json")).get("issue-72") == ""


def test_recording_never_raises_on_a_broken_store(tmp_path):
    class Exploding:
        def record(self, *_a, **_k):
            raise RuntimeError("disk gone")

        def forget(self, *_a, **_k):
            raise RuntimeError("disk gone")

    af._record_refill_experience(Exploding(), _rec(88, "depth_exhausted"))   # must not raise
    af._record_refill_experience(None, _rec(88, "depth_exhausted"))          # off ⇒ no-op


def test_draft_render_omits_the_nudge_so_rotations_do_not_collide(tmp_path):
    """`af_routing.render_prior_lessons` has owned the diversity rotation on the draft
    path since before this store existed; two rotations in one prompt would contradict."""
    store = xp.ExperienceStore(str(tmp_path / "e.json"))
    store.record("issue-53", {"outcome": "depth"})
    drafted = store.render("issue-53", nudge=False)
    assert "THIS ATTEMPT:" not in drafted
    assert all(d not in drafted for d in xp.DIVERSITY_INSTRUCTIONS)
    assert "THIS ATTEMPT:" in store.render("issue-53")      # prove path still nudges


def test_draft_and_prove_key_spaces_are_disjoint(tmp_path):
    """One file, two writers: `issue-<n>` from refill, target ids from the prove gate."""
    store = xp.ExperienceStore(str(tmp_path / "e.json"))
    af._record_refill_experience(store, _rec(53, "depth_exhausted"))
    store.record("cal-bk-144", {"outcome": "max_rounds"})
    assert store.report()["targets"] == 2
    assert store.attempts("issue-53") == 1 and store.attempts("cal-bk-144") == 1
