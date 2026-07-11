import json
import os
import tempfile

from probe_lib import (
    TokenLedger,
    append_jsonl,
    axiom_guard_block,
    best_failure,
    build_initial_prompt,
    build_repair_prompt,
    extract_lean_code,
    normalize_content,
    sha256_hex,
    slop_report,
    window_messages,
)


def test_normalize_content_reasoning_blocks():
    assert normalize_content("plain string") == "plain string"
    blocks = [
        {"type": "thinking", "thinking": [{"type": "text", "text": "secret reasoning"}]},
        {"type": "text", "text": "```lean\nby norm_num\n```"},
    ]
    out = normalize_content(blocks)
    assert out == "```lean\nby norm_num\n```"
    assert "secret reasoning" not in out


def test_extract_lean_code_fenced():
    text = "Here is the proof:\n```lean\ntheorem t : 1 = 1 := rfl\n```\nDone."
    assert extract_lean_code(text) == "theorem t : 1 = 1 := rfl"


def test_extract_lean_code_prefers_last_lean_block():
    text = "```lean\nold\n```\nrevised:\n```lean\nnew\n```"
    assert extract_lean_code(text) == "new"


def test_extract_lean_code_plain_fence_fallback():
    text = "```\ntheorem t : 1 = 1 := rfl\n```"
    assert extract_lean_code(text) == "theorem t : 1 = 1 := rfl"


def test_extract_lean_code_none():
    assert extract_lean_code("no code here") is None


def test_token_ledger():
    led = TokenLedger(budget=100)
    assert not led.exhausted
    led.add(60)
    led.add(39)
    assert led.spent == 99 and not led.exhausted
    led.add(1)
    assert led.exhausted


def test_prompts_mention_rules_and_content():
    p = build_initial_prompt("theorem foo : 2 = 2 := by sorry")
    assert "COMPLETE file" in p and "sorry" in p and "theorem foo" in p
    r = build_repair_prompt(["line 3:2: unknown identifier 'bar'"])
    assert "unknown identifier" in r and "COMPLETE" in r


def test_window_messages_keeps_first_and_tail():
    msgs = [{"role": "user", "content": "initial"}]
    for i in range(10):
        msgs.append({"role": "assistant", "content": f"a{i}"})
        msgs.append({"role": "user", "content": f"u{i}"})
    out = window_messages(msgs, keep_last=2)
    assert out[0]["content"] == "initial"
    assert [m["content"] for m in out[1:]] == ["a8", "u8", "a9", "u9"]


def test_window_messages_preserves_system():
    msgs = [{"role": "system", "content": "SYS"},
            {"role": "user", "content": "initial"}]
    for i in range(10):
        msgs.append({"role": "assistant", "content": f"a{i}"})
        msgs.append({"role": "user", "content": f"u{i}"})
    out = window_messages(msgs, keep_last=2)
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1]["content"] == "initial"
    assert [m["content"] for m in out[2:]] == ["a8", "u8", "a9", "u9"]


def test_axiom_guard_block():
    out = axiom_guard_block("theorem foo : 1 = 1 := rfl", "foo")
    assert out.startswith("theorem foo")
    assert "Lean.collectAxioms `foo" in out
    assert "DISALLOWED_AXIOM" in out
    assert "`propext, `Classical.choice, `Quot.sound" in out


def test_slop_report_flags_forbidden():
    rep = slop_report("theorem t : 1 = 1 := by native_decide")
    assert "native_decide" in rep["forbidden"]
    clean = slop_report("theorem t : 1 = 1 := by\n  nlinarith [sq_nonneg a, sq_nonneg b]")
    assert clean["forbidden"] == []
    assert clean["line_count"] == 2
    assert clean["max_bracket_args"] == 2


def test_sha256_and_jsonl():
    assert len(sha256_hex("abc")) == 64
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "log.jsonl")
        append_jsonl(path, {"a": 1})
        append_jsonl(path, {"b": 2})
        lines = [json.loads(x) for x in open(path)]
        assert lines == [{"a": 1}, {"b": 2}]


def test_best_failure_picks_fewest_errors():
    results = [{"errors": ["a", "b"]}, {"errors": ["c"]}, {"errors": ["d", "e", "f"]}]
    assert best_failure(results) == 1


def test_best_failure_ties_resolve_earliest():
    assert best_failure([{"errors": ["a"]}, {"errors": ["b"]}]) == 0


def test_best_failure_tolerates_missing_errors_key():
    assert best_failure([{"errors": ["a"]}, {}]) == 1
