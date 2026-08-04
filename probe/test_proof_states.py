"""Pure tests for proof-state extraction + normalization. No daemon, no API.

The daemon is injected as `check_fn`, exactly as `gate`/`strengthen` do it, so the
prefix-replay logic is exercised with a fake that returns canned sorry goals.
"""
from __future__ import annotations

import proof_states as ps


# --- normalization: the LeanTree lesson ------------------------------------
# A raw sha256 of pretty-printed goal text almost never hits: the SAME state prints
# with different metavariable numbers and different inaccessible-name daggers
# depending on how it was reached. Normalize those away, and nothing else.

def test_inaccessible_names_are_canonicalized():
    a = "x✝ : ℝ\nh✝¹ : 0 < x✝\n⊢ 0 ≤ x✝"
    b = "x✝ : ℝ\nh✝² : 0 < x✝\n⊢ 0 ≤ x✝"
    assert ps.state_key(a) == ps.state_key(b)


def test_metavariable_numbering_is_canonicalized():
    a = "⊢ HAdd.hAdd ?m.1234 ?m.1235 = 0"
    b = "⊢ HAdd.hAdd ?m.9999 ?m.10000 = 0"
    assert ps.state_key(a) == ps.state_key(b)
    assert ps.state_key("⊢ Sort ?u.55") == ps.state_key("⊢ Sort ?u.7")


def test_whitespace_and_blank_lines_are_canonicalized():
    a = "h : P\n\n\n⊢   Q"
    b = "h : P\n⊢ Q"
    assert ps.state_key(a) == ps.state_key(b)


def test_accessible_names_are_NOT_merged():
    """Deliberately conservative: renaming `h` to `hp` would need alpha-renaming with
    substitution into the target, and getting it wrong merges DIFFERENT states into one
    key, which would serve a wrong tactic. Prefer a missed hit over a false hit."""
    assert ps.state_key("h : P\n⊢ P") != ps.state_key("hp : P\n⊢ P")


def test_hypothesis_order_is_significant():
    """Order is semantic under dependent types; sorting would merge distinct states."""
    assert ps.state_key("a : ℕ\nb : Fin a\n⊢ True") != ps.state_key("b : Fin a\na : ℕ\n⊢ True")


def test_distinct_states_have_distinct_keys_and_keys_are_stable():
    assert ps.state_key("⊢ P") != ps.state_key("⊢ Q")
    assert ps.state_key("⊢ P") == ps.state_key("⊢ P")
    assert len(ps.state_key("⊢ P")) == 64          # sha256 hex


def test_empty_or_whitespace_state_has_no_key():
    assert ps.state_key("") is None
    assert ps.state_key("   \n\n ") is None


# --- splitting a tactic proof into top-level steps -------------------------

def test_split_steps_at_the_block_indentation():
    proof = "  intro x\n  simp\n  exact h"
    assert ps.split_tactic_steps(proof) == ["intro x", "simp", "exact h"]


def test_split_keeps_a_nested_block_with_its_head():
    proof = "  have h : P := by\n    simp\n    ring\n  exact h"
    assert ps.split_tactic_steps(proof) == ["have h : P := by\n    simp\n    ring", "exact h"]


def test_split_treats_a_focus_dot_as_its_own_step():
    proof = "  constructor\n  · simp\n  · ring"
    assert ps.split_tactic_steps(proof) == ["constructor", "· simp", "· ring"]


def test_split_ignores_comment_and_blank_lines():
    proof = "  -- a comment\n  intro x\n\n  simp"
    assert ps.split_tactic_steps(proof) == ["intro x", "simp"]


def test_split_of_a_single_line_proof():
    assert ps.split_tactic_steps("  simpa using h") == ["simpa using h"]
    assert ps.split_tactic_steps("") == []


# --- prefix replay ---------------------------------------------------------

def _fake_check(goals_by_prefix):
    """A daemon stub: maps the number of tactic lines before the `sorry` to a goal.

    It also enforces what real Lean enforces and an earlier version of this stub did
    not: every line of the replayed tactic block must stay INDENTED. `split_tactic_steps`
    strips the base indent off each step, so a probe that re-emits steps verbatim puts
    them at column 0 — outside the block — and real Lean rejects it. That bug passed the
    whole unit suite and was caught only against the daemon; this stub now fails it.
    """
    def check(code: str) -> dict:
        body = code.split(":= by", 1)[1] if ":= by" in code else code
        lines = [ln for ln in body.splitlines() if ln.strip()]
        if any(len(ln) - len(ln.lstrip()) == 0 for ln in lines):
            return {"success": False, "errors": ["unexpected token; tactic block ended"],
                    "sorries": []}
        steps = [ln for ln in lines
                 if ln.strip() != "sorry" and not ln.strip().startswith("--")]
        goal = goals_by_prefix.get(len(steps))
        if goal is None:
            return {"success": False, "errors": ["no goal here"], "sorries": []}
        return {"success": False, "errors": [], "sorry_count": 1,
                "sorries": [{"goal": goal}]}
    return check


def test_replayed_steps_keep_the_tactic_block_indentation():
    """The regression the daemon found: a prefix probe must re-indent the steps it
    replays, or every step after the first lands at column 0 and elaborates as garbage."""
    cand = "theorem t : P := by\n  intro x\n  simp\n  exact h"
    probes: list[str] = []

    def spy(code):
        probes.append(code)
        return {"success": False, "sorry_count": 1, "sorries": [{"goal": "⊢ G"}]}

    ps.extract_states(cand, check_fn=spy)
    for probe in probes:
        block = probe.split(":= by", 1)[1]
        for line in block.splitlines():
            if line.strip():
                assert line.startswith(" "), f"step emitted at column 0:\n{probe}"


def test_extract_pairs_each_state_with_the_tactic_that_follows_it():
    cand = "theorem t : P := by\n  intro x\n  simp\n  exact h"
    got = ps.extract_states(cand, check_fn=_fake_check({0: "⊢ P", 1: "x : X\n⊢ Q", 2: "⊢ R"}))
    assert [(p["state"], p["tactic"]) for p in got] == [
        ("⊢ P", "intro x"), ("x : X\n⊢ Q", "simp"), ("⊢ R", "exact h")]
    assert all(p["key"] == ps.state_key(p["state"]) for p in got)


def test_extract_skips_prefixes_the_daemon_cannot_elaborate():
    """A prefix that does not elaborate (mid-`have`, a `<;>` combinator) yields no goal;
    it is skipped rather than aborting the whole extraction."""
    cand = "theorem t : P := by\n  intro x\n  simp\n  exact h"
    got = ps.extract_states(cand, check_fn=_fake_check({0: "⊢ P", 2: "⊢ R"}))
    assert [(p["state"], p["tactic"]) for p in got] == [("⊢ P", "intro x"), ("⊢ R", "exact h")]


def test_extract_returns_nothing_for_a_proof_with_no_tactic_block():
    assert ps.extract_states("theorem t : P := foo bar", check_fn=_fake_check({})) == []


def test_extract_is_bounded_by_max_steps():
    """A 200-tactic proof would cost 200 elaborations; the cap keeps a pathological
    candidate from eating the gate phase's Lean slot."""
    cand = "theorem t : P := by\n" + "\n".join(f"  tac{i}" for i in range(50))
    goals = {i: f"⊢ G{i}" for i in range(50)}
    got = ps.extract_states(cand, check_fn=_fake_check(goals), max_steps=5)
    assert len(got) == 5
