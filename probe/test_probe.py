import socket

import probe
from probe import _parse_daemon_response, daemon_check, run_target


def test_parse_daemon_response_valid():
    r = _parse_daemon_response(b'{"success": true, "errors": [], "sorry_count": 1}')
    assert r["success"] is True and r["sorry_count"] == 1


class _FakeSock:
    """Minimal socket stand-in whose recv raises, exercising daemon_check's
    failure handling without a real daemon."""

    def __init__(self, exc):
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def settimeout(self, t):
        pass

    def sendall(self, b):
        pass

    def shutdown(self, how):
        pass

    def recv(self, n):
        raise self._exc


def test_daemon_check_socket_timeout_returns_error_dict(monkeypatch):
    # a wedged / mid-respawn daemon can blow the socket deadline; daemon_check must
    # surface it as a FAILED check (like _parse_daemon_response does for a bad
    # payload), never let an uncaught TimeoutError skip the whole issue.
    monkeypatch.setattr(probe.socket, "create_connection",
                        lambda *a, **k: _FakeSock(socket.timeout("timed out")))
    r = daemon_check("import Mathlib")
    assert r["success"] is False
    assert r["errors"]
    assert r["sorry_count"] == 0


def test_daemon_check_connection_refused_returns_error_dict(monkeypatch):
    # daemon down (respawning) → create_connection raises ConnectionRefusedError;
    # a failed check, not a crash.
    def refuse(*a, **k):
        raise ConnectionRefusedError("connection refused")
    monkeypatch.setattr(probe.socket, "create_connection", refuse)
    r = daemon_check("import Mathlib")
    assert r["success"] is False
    assert r["errors"]


def test_parse_daemon_response_empty_is_error_not_raise():
    # a degraded daemon can return an empty/truncated payload — surface it as an
    # error dict (so run_target / formalize_with_repair retries), never raise.
    r = _parse_daemon_response(b"")
    assert r["success"] is False
    assert r["errors"]
    assert r["sorry_count"] == 0


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


def test_run_target_prepends_system_prompt():
    chat = make_chat([
        ("```lean\nimport Mathlib\n\ntheorem foo : 2 + 2 = 4 := by norm_num\n```", 500),
    ])
    check = make_check([
        {"success": True, "errors": [], "warnings": [], "sorry_count": 0},
        {"success": True, "errors": [], "warnings": [], "sorry_count": 0},  # axiom guard
    ])
    out = run_target(TARGET, budget=50000, max_rounds=1,
                     chat_fn=chat, check_fn=check, log_fn=lambda r: None,
                     system_prompt="HOUSE DOCTRINE")
    assert out["outcome"] == "pass"
    assert chat.calls[0][0] == {"role": "system", "content": "HOUSE DOCTRINE"}


def test_run_target_injects_context_pack():
    chat = make_chat([
        ("```lean\nimport Mathlib\n\ntheorem foo : 2 + 2 = 4 := by norm_num\n```", 500),
    ])
    check = make_check([
        {"success": True, "errors": [], "warnings": [], "sorry_count": 0},
        {"success": True, "errors": [], "warnings": [], "sorry_count": 0},  # axiom guard
    ])
    pack = "── EXISTING DECLARATIONS ──\nvasicekBondPrice : ℝ → ℝ → ℝ\n"
    out = run_target(TARGET, budget=50000, max_rounds=1, chat_fn=chat, check_fn=check,
                     log_fn=lambda r: None, context_pack=pack)
    assert out["outcome"] == "pass"
    first_user = next(m for m in chat.calls[0] if m["role"] == "user")
    assert "vasicekBondPrice" in first_user["content"]   # pack injected
    assert "theorem foo" in first_user["content"]         # statement still present


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


def test_fanout_passes_when_one_of_k_succeeds_same_round():
    # k=2: first candidate fails to compile, second passes — both in ROUND 1.
    chat = make_chat([
        ("```lean\nimport Mathlib\n\ntheorem foo : 2 + 2 = 4 := by omega\n```", 400),
        ("```lean\nimport Mathlib\n\ntheorem foo : 2 + 2 = 4 := by norm_num\n```", 400),
    ])
    check = make_check([
        {"success": False, "errors": ["e"], "warnings": [], "sorry_count": 0},  # cand 1
        {"success": True, "errors": [], "warnings": [], "sorry_count": 0},       # cand 2
        {"success": True, "errors": [], "warnings": [], "sorry_count": 0},       # axiom guard
    ])
    out = run_target(TARGET, budget=50000, max_rounds=3, fanout=2, repair_rounds=2,
                     chat_fn=chat, check_fn=check, log_fn=lambda r: None)
    assert out["outcome"] == "pass"
    assert out["rounds"] == 1
    assert out["tokens"] == 800  # both candidates charged


def test_fanout_repairs_best_failure_across_rounds():
    # k=2 round 1: both fail (2 errors vs 1); repair should build on the 1-error one.
    chat = make_chat([
        ("```lean\nimport Mathlib\n\ntheorem foo : 2 + 2 = 4 := by ring_nf\n```", 300),   # 2 errors
        ("```lean\nimport Mathlib\n\ntheorem foo : 2 + 2 = 4 := by simp only []\n```", 300),# 1 error "ALPHA"
        ("```lean\nimport Mathlib\n\ntheorem foo : 2 + 2 = 4 := by norm_num\n```", 300),   # round 2 pass
    ])
    check = make_check([
        {"success": False, "errors": ["x", "y"], "warnings": [], "sorry_count": 0},  # cand 1
        {"success": False, "errors": ["ALPHA"], "warnings": [], "sorry_count": 0},    # cand 2 (fewer)
        {"success": True, "errors": [], "warnings": [], "sorry_count": 0},            # round 2
        {"success": True, "errors": [], "warnings": [], "sorry_count": 0},            # axiom guard
    ])
    out = run_target(TARGET, budget=50000, max_rounds=3, fanout=2, repair_rounds=2,
                     chat_fn=chat, check_fn=check, log_fn=lambda r: None)
    assert out["outcome"] == "pass"
    assert out["rounds"] == 2
    # the repair round's prompt was built from the FEWEST-error candidate ("ALPHA")
    assert "ALPHA" in chat.calls[2][-1]["content"]
