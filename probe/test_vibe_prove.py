"""Pure tests for vibe_prove — injected run_fn, no vibe/docker/Lean."""
from __future__ import annotations

import os

import vibe_prove


import domain_pack

PACK = domain_pack.load("mathfin")


def test_sanitize_stem_makes_a_safe_module_name():
    assert vibe_prove.sanitize_stem("cal-bk-67") == "_Autoform_cal_bk_67"
    assert vibe_prove.sanitize_stem("mf/thm.9.1") == "_Autoform_mf_thm_9_1"


def test_scratch_paths_host_and_container_align():
    host, rel = vibe_prove.scratch_paths(PACK, "/home/x/main", "cal-bk-67")
    assert host == "/home/x/main/MathFin/_Autoform_cal_bk_67.lean"
    assert rel == "MathFin/_Autoform_cal_bk_67.lean"
    # the relative path is what vibe uses (CWD=main) AND what the MCP sees under /app
    assert host.endswith(rel)


def test_build_vibe_task_names_the_file_and_the_theorem():
    t = vibe_prove.build_vibe_task("MathFin/_Autoform_x.lean", "myThm", "")
    assert "MathFin/_Autoform_x.lean" in t
    assert "theorem myThm" in t
    assert "Do NOT change the theorem statement" in t
    assert "EXISTING RESULTS" not in t  # no pointer pack → no consume block


def test_build_vibe_task_includes_context_pack_when_present():
    t = vibe_prove.build_vibe_task("f.lean", "t", "vasicekBondPrice_affine : …")
    assert "EXISTING RESULTS TO CONSUME" in t
    assert "vasicekBondPrice_affine" in t


def test_run_vibe_target_captures_the_edited_file_and_cleans_up(tmp_path):
    (tmp_path / "MathFin").mkdir()
    target = {"id": "cal-x-1", "sorry_name": "foo",
              "statement": "import Mathlib\ntheorem foo : True := by sorry\n"}
    host, _ = vibe_prove.scratch_paths(PACK, str(tmp_path), target["id"])

    def fake_vibe(argv, cwd=None, check=None):
        # vibe runs with CWD = main repo, and edits the in-place host file
        assert cwd == str(tmp_path)
        assert argv[0] == "/x/leanstral-vibe.sh" and "-p" in argv
        assert os.path.exists(host)  # the stub was materialized before vibe ran
        with open(host, "w", encoding="utf-8") as f:
            f.write("import Mathlib\ntheorem foo : True := trivial\n")
        return 0

    cand = vibe_prove.run_vibe_target(PACK, 
        target, main_repo=str(tmp_path), context_pack="", max_turns=10,
        vibe_script="/x/leanstral-vibe.sh", run_fn=fake_vibe)
    assert cand is not None and "trivial" in cand and "sorry" not in cand
    assert not os.path.exists(host)  # scratch always cleaned up


def test_run_vibe_target_returns_stub_on_a_no_op_and_still_cleans_up(tmp_path):
    (tmp_path / "MathFin").mkdir()
    target = {"id": "cal-x-2", "sorry_name": "bar",
              "statement": "import Mathlib\ntheorem bar : True := by sorry\n"}
    host, _ = vibe_prove.scratch_paths(PACK, str(tmp_path), target["id"])

    cand = vibe_prove.run_vibe_target(PACK, 
        target, main_repo=str(tmp_path), context_pack="", max_turns=10,
        vibe_script="/x/leanstral-vibe.sh", run_fn=lambda *a, **k: 0)
    assert cand is not None and "sorry" in cand  # unchanged stub captured
    assert not os.path.exists(host)


# --- did the prover RUN? (the check that was missing for five weeks) ------------

import json  # noqa: E402

_STUB = "import Mathlib\ntheorem foo : True := by sorry\n"


def _transcript(*, mcp="connected", tools=("mcp__lean-lsp__lean_goal", "Edit"),
                result=True, is_error=False, subtype="success"):
    lines = [{"type": "system", "subtype": "init",
              "mcp_servers": [{"name": "lean-lsp", "status": mcp}]}]
    for name in tools:
        lines.append({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "checking the goal"},
            {"type": "tool_use", "name": name, "input": {}}]}})
    if result:
        lines.append({"type": "result", "subtype": subtype, "is_error": is_error,
                      "num_turns": len(tools) + 1, "total_cost_usd": 0.42,
                      "usage": {"input_tokens": 1200, "output_tokens": 800,
                                "cache_read_input_tokens": 90000}})
    return "not json\n" + "\n".join(json.dumps(x) for x in lines) + "\n"


def test_the_2026_08_19_failure_is_an_error_not_a_failed_proof(tmp_path):
    """The launcher raised a TypeError and exited 1 before the prover started. The
    untouched stub came back, it still had its `sorry`, and it was scored max_rounds."""
    (tmp_path / "MathFin").mkdir()
    target = {"id": "cal-bk-83", "sorry_name": "foo", "statement": _STUB}

    def crashed_launcher(argv, stdout=None, stderr=None, **kw):
        stderr.write("TypeError: build_system_prompt() missing 1 required positional "
                     "argument: 'pack'\n")
        return 1

    sess = vibe_prove.run_prover_session(
        PACK, target, main_repo=str(tmp_path), context_pack="", max_turns=60,
        launcher="/x/claude-prove.sh", run_fn=crashed_launcher,
        log_prefix=str(tmp_path / "t"))
    assert sess["content"] == _STUB and sess["rc"] == 1
    assert "TypeError" in open(sess["launcher_log"]).read()   # the evidence survives
    rec = vibe_prove.session_record(target, sess, engine="claude", model="m")
    ran, why = vibe_prove.classify_session(rec)
    assert ran is False and "rc=1" in why


def test_a_real_session_that_did_not_close_the_goal_still_counts_as_run(tmp_path):
    (tmp_path / "MathFin").mkdir()
    target = {"id": "cal-bk-9", "sorry_name": "foo", "statement": _STUB}

    def honest_failure(argv, stdout=None, stderr=None, **kw):
        stdout.write(_transcript(subtype="error_max_turns", is_error=True))
        return 0

    sess = vibe_prove.run_prover_session(
        PACK, target, main_repo=str(tmp_path), context_pack="", max_turns=60,
        launcher="/x/claude-prove.sh", run_fn=honest_failure, log_prefix=str(tmp_path / "t"))
    rec = vibe_prove.session_record(target, sess, engine="claude", model="m")
    assert vibe_prove.classify_session(rec) == (True, "")   # → max_rounds, a real verdict
    assert rec["tokens"] == 2000 and rec["cost_usd"] == 0.42 and rec["turns"] == 3


def test_classify_session_names_each_way_a_session_can_fail_to_run():
    base = {"engine": "claude", "rc": 0, "result": {"subtype": "success", "is_error": False},
            "mcp_servers": {"lean-lsp": "connected"}, "tool_calls": 4, "lean_tool_calls": 3}
    C = vibe_prove.classify_session
    assert C(base) == (True, "")
    assert C(None)[0] is False
    assert C({**base, "result": None})[0] is False                      # never completed
    assert C({**base, "mcp_servers": {"lean-lsp": "failed"}, "lean_tool_calls": 0})[0] is False
    assert C({**base, "result": {"subtype": "error_during_execution", "is_error": True}})[0] is False
    assert C({**base, "tool_calls": 0, "lean_tool_calls": 0})[0] is False
    # a pending status at init is fine once Lean tools were actually called
    assert C({**base, "mcp_servers": {"lean-lsp": "pending"}})[0] is True
    vibe = {"engine": "leanstral", "rc": 0}
    assert C({**vibe, "identical_to_stub": True, "duration_s": 0.4})[0] is False
    assert C({**vibe, "identical_to_stub": True, "duration_s": 900})[0] is True


def test_parse_claude_transcript_tolerates_junk_and_counts_lean_tools(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_transcript(tools=("mcp__lean-lsp__lean_goal", "mcp__lean-lsp__lean_diagnostic_messages", "Edit")) + '{"trunc')
    parsed = vibe_prove.parse_claude_transcript(str(p))
    assert parsed["tool_calls"] == 3 and parsed["lean_tool_calls"] == 2
    assert parsed["mcp_servers"] == {"lean-lsp": "connected"}
    assert parsed["result"]["num_turns"] == 4
    assert vibe_prove.parse_claude_transcript(str(tmp_path / "missing"))["result"] is None


def test_the_launcher_gets_the_configured_env(tmp_path):
    (tmp_path / "MathFin").mkdir()
    seen = {}

    def fake(argv, env=None, **kw):
        seen.update(env or {})
        return 0

    vibe_prove.run_prover_session(PACK, {"id": "x", "sorry_name": "foo", "statement": _STUB},
                                  main_repo=str(tmp_path), context_pack="", max_turns=5,
                                  launcher="/x/l.sh", run_fn=fake,
                                  env={"CLAUDE_PROVER_MODEL": "claude-sonnet-5"})
    assert seen["CLAUDE_PROVER_MODEL"] == "claude-sonnet-5"


def test_the_task_tells_an_agent_to_edit_the_file_not_print_it():
    t = vibe_prove.build_vibe_task("MathFin/_Autoform_x.lean", "myThm", "")
    assert "the file on disk is the only output" in t


# --- the canary ----------------------------------------------------------------

def _canary_run(tmp_path, *, proves: bool, rc: int = 0):
    (tmp_path / "MathFin").mkdir(exist_ok=True)
    host, _ = vibe_prove.scratch_paths(PACK, str(tmp_path), vibe_prove.CANARY_ID)

    def fake(argv, stdout=None, stderr=None, **kw):
        if proves:
            text = open(host, encoding="utf-8").read().replace("sorry", "omega")
            open(host, "w", encoding="utf-8").write(text)
        stdout.write(_transcript())
        return rc

    return vibe_prove.run_canary(PACK, main_repo=str(tmp_path), launcher="/x/l.sh",
                                 engine="claude", model="m", run_dir=str(tmp_path),
                                 tag="t", max_turns=12, env=None, run_fn=fake)


def test_the_canary_passes_when_the_path_proves_a_trivial_theorem(tmp_path):
    ok, why = _canary_run(tmp_path, proves=True)
    assert ok, why
    rec = json.load(open(tmp_path / "t-foundry-canary.json"))
    assert rec["canary_ok"] is True


def test_the_canary_fails_on_a_crashed_launcher_or_an_unproved_theorem(tmp_path):
    assert _canary_run(tmp_path, proves=True, rc=1)[0] is False
    ok, why = _canary_run(tmp_path, proves=False)
    assert ok is False and "unproved" in why


def test_the_canary_is_shaped_like_a_real_stub():
    t = vibe_prove.canary_target(PACK)
    assert t["statement"].startswith("module\n\npublic import Mathlib")
    assert f"namespace {PACK.namespace}" in t["statement"]
    assert t["statement"].count("sorry") == 1


def test_a_launcher_that_cannot_start_is_a_failed_session_not_a_crash(tmp_path):
    """`claude-prove.sh` was committed without its executable bit."""
    (tmp_path / "MathFin").mkdir()

    def not_executable(argv, **kw):
        raise PermissionError(13, "Permission denied", argv[0])

    sess = vibe_prove.run_prover_session(
        PACK, {"id": "x", "sorry_name": "foo", "statement": _STUB}, main_repo=str(tmp_path),
        context_pack="", max_turns=5, launcher="/x/claude-prove.sh", run_fn=not_executable,
        log_prefix=str(tmp_path / "t"))
    assert sess["rc"] == 127 and "Permission denied" in open(sess["launcher_log"]).read()


def test_prover_of_reads_the_session_then_the_leaves_then_the_config(tmp_path):
    runs = str(tmp_path)
    assert vibe_prove.prover_of(runs, "t", "cal-bk-1")["engine"] == "claude"   # config default
    (tmp_path / "t-cal-bk-1__leaf_a.session.json").write_text(
        json.dumps({"engine": "leanstral", "model": "labs-leanstral-1-5"}))
    assert vibe_prove.prover_of(runs, "t", "cal-bk-1") == {
        "engine": "leanstral", "model": "labs-leanstral-1-5"}          # a decompose candidate
    (tmp_path / "t-cal-bk-1.session.json").write_text(
        json.dumps({"engine": "claude", "model": "claude-sonnet-5"}))
    assert vibe_prove.prover_of(runs, "t", "cal-bk-1") == {
        "engine": "claude", "model": "claude-sonnet-5"}               # its own session wins


# --- a session that surfaced a credential is a security event --------------------

def test_a_leaked_credential_is_redacted_on_disk_and_the_session_is_an_error(tmp_path, monkeypatch):
    (tmp_path / "MathFin").mkdir()
    secret = "sk-ant-oat01-" + "q" * 40
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", secret)
    target = {"id": "cal-bk-7", "sorry_name": "foo", "statement": _STUB}
    host, _ = vibe_prove.scratch_paths(PACK, str(tmp_path), target["id"])

    def env_dumping_agent(argv, stdout=None, stderr=None, **kw):
        stdout.write(_transcript() + json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "content": f"CLAUDE_CODE_OAUTH_TOKEN={secret}"}]}}) + "\n")
        with open(host, "w", encoding="utf-8") as f:
            f.write(f"-- {secret}\ntheorem foo : True := trivial\n")
        return 0

    sess = vibe_prove.run_prover_session(
        PACK, target, main_repo=str(tmp_path), context_pack="", max_turns=5,
        launcher="/x/claude-prove.sh", run_fn=env_dumping_agent, log_prefix=str(tmp_path / "t"))
    assert secret not in open(sess["transcript"]).read()          # redacted before persist
    assert secret not in sess["content"]                          # the candidate too
    rec = vibe_prove.session_record(target, sess, engine="claude", model="m")
    ran, why = vibe_prove.classify_session(rec)
    assert ran is False and "credential" in why


def test_the_prover_environment_holds_only_its_own_credential(monkeypatch):
    monkeypatch.setenv("MAIN_PR_TOKEN", "ghp_" + "x" * 36)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "y" * 30)
    engine, _launcher, _model, env, _cfg = vibe_prove._prover(
        type("A", (), {"config": None, "engine": "claude"})())
    assert "MAIN_PR_TOKEN" not in env and env["CLAUDE_CODE_OAUTH_TOKEN"].startswith("sk-ant-")


# --- reuse a proof that already passed instead of paying for it again --------------

def test_adopt_reuses_the_newest_verified_proof_and_charges_nothing(tmp_path):
    runs = tmp_path
    for tag, outcome in (("pipeline-20260928-001810", "pass"), ("pipeline-20260928-011101", "pass"),
                         ("pipeline-20260929-000000", "max_rounds")):
        (runs / f"{tag}-summary.jsonl").write_text(json.dumps(
            {"target": "cal-bk-129", "harness": "vibe", "outcome": outcome}) + "\n")
        (runs / f"{tag}-cal-bk-129.candidate").write_text(
            "theorem x : True := trivial\n" if outcome == "pass" else "theorem x : True := by sorry\n")
        (runs / f"{tag}-cal-bk-129.session.json").write_text(json.dumps(
            {"engine": "claude", "model": "claude-sonnet-5", "rc": 0, "tokens": 72456}))
    src = vibe_prove.adopt_verified(str(runs), "cal-bk-129", "pipeline-20261001-061700")
    assert src == "pipeline-20260928-011101"
    rec = json.load(open(runs / "pipeline-20261001-061700-cal-bk-129.session.json"))
    assert rec["reused_from"] == src and rec["tokens"] == 0 and rec["original_tokens"] == 72456
    assert "trivial" in (runs / "pipeline-20261001-061700-cal-bk-129.candidate").read_text()
    assert vibe_prove.adopt_verified(str(runs), "cal-bk-999", "t") is None
