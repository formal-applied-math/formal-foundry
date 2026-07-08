from probe import run_target

TARGET = {
    "id": "cal-x",
    "stream": "backlog",
    "kind": "prove",
    "sorry_name": "foo",
    "statement": "import Mathlib\n\ntheorem foo : 2 + 2 = 4 := by sorry",
}


def make_chat(script):
    """script: list of (content, tokens); pops one per call."""
    calls = []

    def chat_fn(messages, **kw):
        calls.append(messages)
        return script.pop(0)

    chat_fn.calls = calls
    return chat_fn


def make_check(script):
    def check_fn(code, **kw):
        return script.pop(0)

    return check_fn


def test_pass_on_second_round_with_axiom_guard():
    chat = make_chat([
        ("```lean\nimport Mathlib\n\ntheorem foo : 2 + 2 = 4 := by exact rfl\n```", 900),
        ("```lean\nimport Mathlib\n\ntheorem foo : 2 + 2 = 4 := by norm_num\n```", 900),
    ])
    check = make_check([
        {"success": False, "errors": ["line 3:40: type mismatch"], "warnings": [], "sorry_count": 0},
        {"success": True, "errors": [], "warnings": [], "sorry_count": 0},
        {"success": True, "errors": [], "warnings": [], "sorry_count": 0},  # axiom guard
    ])
    logs = []
    out = run_target(TARGET, budget=50000, max_rounds=5,
                     chat_fn=chat, check_fn=check, log_fn=logs.append)
    assert out["outcome"] == "pass"
    assert out["rounds"] == 2
    assert out["tokens"] == 1800
    assert out["axioms_clean"] is True
    assert len(logs) == 2
    # repair round got the compiler error text
    assert "type mismatch" in chat.calls[1][-1]["content"]


def test_budget_exhaustion():
    chat = make_chat([("no code at all", 30000), ("still none", 30000)])
    check = make_check([])
    out = run_target(TARGET, budget=50000, max_rounds=10,
                     chat_fn=chat, check_fn=check, log_fn=lambda r: None)
    assert out["outcome"] == "budget_exhausted"
    assert out["tokens"] == 60000


def test_forbidden_tactic_rejected_not_passed():
    chat = make_chat([
        ("```lean\nimport Mathlib\n\ntheorem foo : 2 + 2 = 4 := by native_decide\n```", 500),
    ])
    check = make_check([
        {"success": True, "errors": [], "warnings": [], "sorry_count": 0},
    ])
    out = run_target(TARGET, budget=50000, max_rounds=1,
                     chat_fn=chat, check_fn=check, log_fn=lambda r: None)
    assert out["outcome"] == "max_rounds"
    assert out["slop"]["forbidden"] == ["native_decide"]
