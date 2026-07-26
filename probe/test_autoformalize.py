"""Tests for the issue->stub autoformalizer sub-probe. Pure logic only —
injected chat_fn/check_fn, temp trees; no Lean, no API, no network."""

from __future__ import annotations

import os

import build_manifest as bm
import pipeline_lib as pl

import autoformalize as af


# --- AutoformalizeConfig -----------------------------------------------------

def test_autoformalize_config_defaults():
    cfg = pl.AutoformalizeConfig.load(None)
    assert cfg.enabled is True
    assert cfg.budget > 0
    assert cfg.prover_model == "labs-leanstral-1-5"


# --- [drafter] engine switch (item I) ----------------------------------------

def test_drafter_config_defaults_are_todays_behaviour():
    d = pl.DrafterConfig.load(None)
    assert d.engine == "mistral"       # default = magistral-intent + leanstral-formalize
    assert d.mode == "completion"
    assert d.on_cap == "fallback"
    assert d.claude_model.startswith("claude-")   # a real model, not the weak CLI default


# --- claude -p draft adapter (item I) ----------------------------------------

class _FakeRun:
    def __init__(self, stdout="", stderr=""):
        self.stdout, self.stderr = stdout, stderr


def test_claude_draft_args_shape():
    argv, stdin = af._claude_draft_args(
        [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USR"}],
        model="claude-sonnet-5")
    assert argv[:2] == ["claude", "-p"]
    assert "--output-format" in argv and "json" in argv
    assert "--allowedTools" in argv                              # tools disabled → pure completion
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5"
    assert argv[argv.index("--append-system-prompt") + 1] == "SYS"
    assert stdin == "USR"                                        # user content on stdin


def test_claude_draft_fn_parses_result_and_tokens():
    j = ('{"is_error": false, "subtype": "success", "result": "INTENT", '
         '"usage": {"input_tokens": 5, "output_tokens": 7}}')
    text, tokens = af.claude_draft_fn([{"role": "user", "content": "x"}],
                                      run_fn=lambda argv, stdin: _FakeRun(stdout=j))
    assert text == "INTENT" and tokens == 12


def test_claude_draft_fn_raises_cap_error_on_usage_limit():
    j = '{"is_error": true, "subtype": "error", "result": "5-hour usage limit reached"}'
    try:
        af.claude_draft_fn([{"role": "user", "content": "x"}],
                           run_fn=lambda argv, stdin: _FakeRun(stdout=j))
        raised = False
    except af.ClaudeCapError:
        raised = True
    assert raised is True


def test_claude_draft_fn_other_error_is_runtime_not_cap():
    j = '{"is_error": true, "subtype": "error", "result": "some other failure"}'
    try:
        af.claude_draft_fn([{"role": "user", "content": "x"}],
                           run_fn=lambda argv, stdin: _FakeRun(stdout=j))
        kind = None
    except af.ClaudeCapError:
        kind = "cap"
    except RuntimeError:
        kind = "runtime"
    assert kind == "runtime"


def test_refill_uses_intent_fn_for_draft(monkeypatch, tmp_path):
    # the engine switch routes the DRAFT to intent_fn while the judge keeps reason_fn
    captured = {}

    def fake_draft(issue, ctx, *, chat_fn, **kw):
        captured["chat_fn"] = chat_fn
        return {"ok": False, "reason": "stop after intent", "tokens": 1}
    monkeypatch.setattr(af, "draft_intent", fake_draft)
    intent_sentinel = lambda m: ("i", 0)
    reason_sentinel = lambda m: ("r", 0)
    af.refill([_issue(9)], reason_fn=reason_sentinel, intent_fn=intent_sentinel,
              prove_fn=_NOOP, check_fn=_ELAB_OK, context_fn=lambda i: "",
              queue_dir=str(tmp_path), budget=100000)
    assert captured["chat_fn"] is intent_sentinel


def test_refill_intent_fn_defaults_to_reason_fn(monkeypatch, tmp_path):
    captured = {}

    def fake_draft(issue, ctx, *, chat_fn, **kw):
        captured["chat_fn"] = chat_fn
        return {"ok": False, "reason": "stop", "tokens": 1}
    monkeypatch.setattr(af, "draft_intent", fake_draft)
    reason_sentinel = lambda m: ("r", 0)
    af.refill([_issue(9)], reason_fn=reason_sentinel, prove_fn=_NOOP, check_fn=_ELAB_OK,
              context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert captured["chat_fn"] is reason_sentinel   # back-compat: no intent_fn → reason_fn


def test_lean_lsp_mcp_config_targets_the_container():
    # phase 2: the agentic drafter gets the SAME lean-lsp MCP the vibe harness gives Leanstral
    c = af._lean_lsp_mcp_config()
    s = c["mcpServers"]["lean-lsp"]
    assert s["command"] == "docker"
    assert "mathfin-lean-lsp" in s["args"] and "lean-lsp-mcp" in s["args"]
    assert "--lean-project-path" in s["args"] and "/app" in s["args"]


def test_agentic_formalize_args_wires_lean_lsp_strict_and_tools():
    argv = af._agentic_formalize_args("/tmp/mcp.json", model="claude-opus-4-8")
    assert argv[:2] == ["claude", "-p"]
    assert "--mcp-config" in argv and "/tmp/mcp.json" in argv
    assert "--strict-mcp-config" in argv                          # ONLY the lean-lsp MCP
    assert argv[argv.index("--model") + 1] == "claude-opus-4-8"
    tools = argv[argv.index("--allowedTools") + 1]
    assert "lean-lsp" in tools and "Write" in tools               # draft a file + self-validate via lean tools


_AGENTIC_INTENT = {"statement": "upCapture is scale-invariant in portfolio returns",
                   "module_name": "UpCapAgentic", "benchmark_id": "mf-agentic", "docstring": "d",
                   "definitions": [{"name": "upCapture", "signature": "Finset S → (S→ℝ) → (S→ℝ) → ℝ",
                                    "meaning": "ratio of sums"}]}


def test_agentic_formalize_prompt_carries_intent_scaffold_and_selfcheck():
    p = af._agentic_formalize_prompt(_AGENTIC_INTENT, "SCAFFOLD", "MathFin/X.lean")
    assert "upCapture" in p and "MathFin/X.lean" in p and "SCAFFOLD" in p
    assert "lean_diagnostic" in p and "sorry" in p                # self-validate; keep the sorry


def test_extract_core_stub_pulls_body_between_scaffold():
    module = ("junk\nopen scoped NNReal ENNReal\n\n"
              "noncomputable def foo : ℝ := 1\n\ntheorem t : foo = 1 := by sorry\n\nend MathFin\n")
    core = af._extract_core_stub(module)
    assert "def foo" in core and "theorem t" in core
    assert "end MathFin" not in core and "junk" not in core


def _agentic_scaffolded(core):
    lt, _e, _p = af.emit_target_files(
        _ISSUE, core, {"module_name": "UpCapAgentic", "benchmark_id": "mf-agentic",
                       "docstring": "d", "definitions": ["upCapture"]})
    return lt


def test_agentic_formalize_ok_on_elaborating_module(tmp_path):
    main = str(tmp_path); os.makedirs(os.path.join(main, "MathFin"))

    def fake_run(argv, stdin, cwd):
        lt = _agentic_scaffolded("noncomputable def upCapture : ℝ := 1\n\n"
                                 "theorem t : upCapture = 1 := by sorry")
        with open(os.path.join(cwd, af._AGENTIC_SCRATCH_REL), "w") as f:
            f.write(lt)
        return _FakeRun(stdout='{"is_error":false,"subtype":"success","result":"DONE",'
                               '"usage":{"input_tokens":2,"output_tokens":3}}')
    r = af.agentic_formalize(_AGENTIC_INTENT, issue=_ISSUE, main_repo=main,
                             check_fn=lambda code: {"errors": [], "sorry_count": 1},
                             run_fn=fake_run, mcp_config_path="/tmp/x.json")
    assert r["ok"] is True and "upCapture" in r["lean_text"]
    assert r["entry"] is not None and r["tokens"] == 5


def test_agentic_formalize_fails_when_not_elaborating(tmp_path):
    main = str(tmp_path); os.makedirs(os.path.join(main, "MathFin"))

    def fake_run(argv, stdin, cwd):
        with open(os.path.join(cwd, af._AGENTIC_SCRATCH_REL), "w") as f:
            f.write("broken lean")
        return _FakeRun(stdout='{"is_error":false,"subtype":"success","result":"DONE"}')
    r = af.agentic_formalize(_AGENTIC_INTENT, issue=_ISSUE, main_repo=main,
                             check_fn=lambda code: {"errors": ["boom"], "sorry_count": 0},
                             run_fn=fake_run, mcp_config_path="/tmp/x.json")
    assert r["ok"] is False and r["reason"]


def test_agentic_formalize_fails_when_no_file_written(tmp_path):
    main = str(tmp_path); os.makedirs(os.path.join(main, "MathFin"))
    r = af.agentic_formalize(_AGENTIC_INTENT, issue=_ISSUE, main_repo=main,
                             check_fn=lambda code: {"errors": [], "sorry_count": 1},
                             run_fn=lambda a, s, c: _FakeRun(stdout='{}'),
                             mcp_config_path="/tmp/x.json")
    assert r["ok"] is False and "no file" in r["reason"]


def test_agentic_formalize_skips_verify_when_check_fn_none(tmp_path):
    # live-pipe path: check_fn=None trusts claude's lean-lsp self-validation (gates re-check)
    main = str(tmp_path); os.makedirs(os.path.join(main, "MathFin"))

    def fake_run(argv, stdin, cwd):
        lt = _agentic_scaffolded("noncomputable def upCapture : ℝ := 1\n\n"
                                 "theorem t : upCapture = 1 := by sorry")
        with open(os.path.join(cwd, af._AGENTIC_SCRATCH_REL), "w") as f:
            f.write(lt)
        return _FakeRun(stdout='{"result":"DONE"}')
    r = af.agentic_formalize(_AGENTIC_INTENT, issue=_ISSUE, main_repo=main,
                             run_fn=fake_run, mcp_config_path="/tmp/x.json")   # check_fn defaults None
    assert r["ok"] is True and r["entry"] is not None


def test_refill_routes_to_agentic_formalize_fn(monkeypatch, tmp_path):
    # engine=claude mode=agentic: refill routes the FORMALIZE stage to the agentic fn
    calls = {"agentic": 0, "completion": 0}
    monkeypatch.setattr(af, "draft_intent", lambda issue, ctx, *, chat_fn, **k:
                        {"ok": True, "tokens": 1, "unknowns": [],
                         "intent": {"statement": "s", "module_name": "M", "benchmark_id": "mf-x",
                                    "docstring": "d"}})
    monkeypatch.setattr(af, "formalize_with_repair",
                        lambda *a, **k: calls.__setitem__("completion", calls["completion"] + 1)
                        or {"ok": False, "tokens": 0})

    def agentic_fn(intent, ctx, issue):
        calls["agentic"] += 1
        return {"ok": False, "tokens": 0}          # stop after formalize
    af.refill([_issue(9)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
              context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000,
              agentic_formalize_fn=agentic_fn)
    assert calls["agentic"] >= 1 and calls["completion"] == 0


def test_refill_flips_slot_around_agentic_formalize(monkeypatch, tmp_path):
    # autonomous tick: lean-lsp for the agentic formalize, then flip to the daemon for the gates
    events = []
    monkeypatch.setattr(af, "draft_intent", lambda issue, ctx, *, chat_fn, **k:
                        {"ok": True, "tokens": 1, "unknowns": [],
                         "intent": {"statement": "s", "module_name": "M", "benchmark_id": "mf-x",
                                    "docstring": "d"}})

    def agentic_fn(intent, ctx, issue):
        events.append("formalize")
        return {"ok": False, "tokens": 0}          # stop after formalize (records a gate fail)
    af.refill([_issue(9)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
              context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000, semantic_rounds=1,
              agentic_formalize_fn=agentic_fn, slot_switch_fn=lambda s: events.append("slot:" + s))
    assert events[:3] == ["slot:lean-lsp", "formalize", "slot:daemon"]


def test_select_draft_fns_mistral_keeps_today():
    mi, mf = (lambda m: ("i", 0)), (lambda m: ("f", 0))
    i, f = af.select_draft_fns(pl.DrafterConfig(),
                               mistral_intent_fn=mi, mistral_formalize_fn=mf)
    assert i is mi and f is mf


def test_select_draft_fns_claude_routes_both_to_adapter(monkeypatch):
    calls = []
    monkeypatch.setattr(af, "claude_draft_fn",
                        lambda msgs, model="": calls.append(model) or ("x", 3))
    d = pl.DrafterConfig(engine="claude", claude_model="claude-sonnet-5")
    i, f = af.select_draft_fns(d, mistral_intent_fn=lambda m: ("i", 0),
                               mistral_formalize_fn=lambda m: ("f", 0))
    assert i([{"role": "user", "content": "z"}]) == ("x", 3)
    assert f([{"role": "user", "content": "z"}]) == ("x", 3)
    assert calls == ["claude-sonnet-5", "claude-sonnet-5"]   # both draft sub-stages route to claude


def test_select_draft_fns_claude_falls_back_to_mistral_on_cap(monkeypatch):
    def capping(msgs, model=""):
        raise af.ClaudeCapError("5-hour usage limit reached")
    monkeypatch.setattr(af, "claude_draft_fn", capping)
    d = pl.DrafterConfig(engine="claude", on_cap="fallback")
    i, f = af.select_draft_fns(d, mistral_intent_fn=lambda m: ("m-intent", 5),
                               mistral_formalize_fn=lambda m: ("m-formalize", 5))
    assert i([{"role": "user", "content": "x"}]) == ("m-intent", 5)       # intent → mistral
    assert f([{"role": "user", "content": "x"}]) == ("m-formalize", 5)    # formalize → mistral


def test_select_draft_fns_claude_defer_reraises_cap(monkeypatch):
    def capping(msgs, model=""):
        raise af.ClaudeCapError("usage limit reached")
    monkeypatch.setattr(af, "claude_draft_fn", capping)
    d = pl.DrafterConfig(engine="claude", on_cap="defer")
    i, _f = af.select_draft_fns(d, mistral_intent_fn=lambda m: ("m", 0),
                                mistral_formalize_fn=lambda m: ("m", 0))
    try:
        i([{"role": "user", "content": "x"}])
        raised = False
    except af.ClaudeCapError:
        raised = True
    assert raised is True                                                 # defer → propagate to refill


def test_refill_records_deferred_on_claude_cap(monkeypatch, tmp_path):
    def capping_draft(issue, ctx, *, chat_fn, **kw):
        raise af.ClaudeCapError("5-hour usage limit reached")
    monkeypatch.setattr(af, "draft_intent", capping_draft)
    res = af.refill([_issue(9)], reason_fn=_NOOP, intent_fn=_NOOP, prove_fn=_NOOP,
                    check_fn=_ELAB_OK, context_fn=lambda i: "", queue_dir=str(tmp_path),
                    budget=100000)
    assert res["seeded"] == []
    assert [a["outcome"] for a in res["attempted"]] == ["deferred"]       # not "error"


def test_drafter_config_loads_claude_block(tmp_path):
    p = tmp_path / "pipeline.toml"
    p.write_text('[drafter]\nengine = "claude"\nmode = "agentic"\non_cap = "defer"\n')
    d = pl.DrafterConfig.load(str(p))
    assert d.engine == "claude" and d.mode == "agentic" and d.on_cap == "defer"


def test_drafter_config_ignores_unknown_keys(tmp_path):
    p = tmp_path / "pipeline.toml"
    p.write_text('[drafter]\nengine = "claude"\nbogus = 1\n')
    d = pl.DrafterConfig.load(str(p))
    assert d.engine == "claude" and d.mode == "completion"   # unknown dropped, rest defaulted


def test_autoformalize_config_reads_toml(tmp_path):
    toml = tmp_path / "pipeline.toml"
    toml.write_text("[autoformalize]\nenabled = false\nbudget = 123456\nmax_issues = 2\n")
    cfg = pl.AutoformalizeConfig.load(str(toml))
    assert cfg.enabled is False
    assert cfg.budget == 123456
    assert cfg.max_issues == 2
    assert cfg.gate_budget == 20_000        # unspecified keys keep defaults


# --- Drafter authority (H1): pins + statement-design reach the drafter --------

def test_formalize_messages_inject_drafter_authority(tmp_path):
    from test_house_context import _fake_main_repo
    repo = _fake_main_repo(tmp_path)
    af.set_drafter_prompt(str(repo))
    try:
        sys_content = af.formalize_messages({"statement": "x", "objects": []}, "")[0]["content"]
        assert "leanprover/lean4:v4.31.0" in sys_content          # pins reached the drafter
        assert "autoformalization model" in sys_content.lower()   # base FORMALIZE_SYSTEM kept
        assert "Statement design" in sys_content                  # statement-design authority
        isys = af.intent_messages({"number": 1, "title": "t", "body": "b"}, "")[0]["content"]
        assert "leanprover/lean4:v4.31.0" in isys
    finally:
        af._DRAFTER_PROMPT = ""   # reset the module global (test isolation)


def test_drafter_prompt_unset_leaves_base_system_unchanged():
    # With no wiring, the drafter system prompts are exactly the base constants.
    af._DRAFTER_PROMPT = ""
    assert af.formalize_messages({"statement": "x", "objects": []}, "")[0]["content"] \
        == af.FORMALIZE_SYSTEM


# --- Task 1.3: deterministic repair transforms + emit pre-lint ---------------

def test_repair_hint_stuck_metavar_names_the_implicit():
    h = af._repair_hint(["typeclass instance problem is stuck\n  IsFilteredPreBrownian X ?m P"])
    assert "explicitly" in h and "(μ :=" in h


def test_repair_hint_nnreal_misparse():
    h = af._repair_hint(["failed to synthesize instance of type class\n  LE Type"])
    assert "open scoped NNReal" in h


def test_repair_hint_unknown_identifier_suggests_pinned_grep():
    h = af._repair_hint(["Unknown identifier `MathFin.zcb`"])
    assert ".lake/packages/mathlib" in h


def test_emit_prelint_reorders_omit_before_docstring():
    stub = "/-- d -/\nomit hB in\ntheorem foo (hB : True) : 1 = 1 := by sorry"
    lean_text, _e, _p = af.emit_target_files(_ISSUE, stub, _META)
    assert lean_text.index("omit hB in") < lean_text.index("/-- d -/")


def test_emit_prelint_rejects_sigma_identifier():
    stub = "theorem fooΣ : 1 = 1 := by sorry"
    try:
        af.emit_target_files(_ISSUE, stub, _META)
        assert False, "expected emit to reject the Σ identifier"
    except Exception as e:  # noqa: BLE001
        assert "Σ" in str(e) or "sigma" in str(e).lower()


def test_prelint_strips_model_imports():
    # Leanstral (RL-trained on complete files) emits its own `import` lines; the module
    # header already imports Mathlib + pointers, so a stub-level import lands mid-file and
    # the module system rejects it ("invalid 'import' command" — recurred live on #109/#60).
    stub = "import Mathlib\npublic import MathFin.FixedIncome.ZCB\n\ntheorem foo : True := by sorry"
    out = af._prelint_stub(stub)
    assert "import" not in out
    assert "theorem foo : True := by sorry" in out


def test_emit_strips_stub_imports_so_module_stays_valid():
    stub = "import Mathlib\n\ntheorem foo : 1 = 1 := by sorry"
    lean_text, _e, _p = af.emit_target_files(_ISSUE, stub, _META)
    # the model's import must NOT survive into the body (after the namespace) — that is the
    # mid-file position the elaborator rejects; the only imports are the header's.
    body = lean_text.split("namespace MathFin", 1)[1]
    assert "import" not in body and "theorem foo" in body


def test_prelint_rewrites_autobound_universe_to_star():
    # the model writes `{Ω : Type u}`; emit pins `autoImplicit false` (build-parity), so `u`
    # is unbound → "unknown universe level u" (recurred live on #109/#60). Rewrite the
    # autobound universe vars to the Mathlib idiom `Type*`/`Sort*`.
    out = af._prelint_stub("theorem foo {Ω : Type u} {β : Sort v} (κ : Type u_1) : True := by sorry")
    assert "Type*" in out and "Sort*" in out
    assert "Type u" not in out and "Sort v" not in out and "Type u_1" not in out
    # an explicit numeric level is valid — leave it alone
    assert "Type 0" in af._prelint_stub("theorem g (x : Type 0) : True := by sorry")


def test_prelint_marks_defs_noncomputable():
    # MathFin is proof-only — every real-valued def is effectively noncomputable (ℝ
    # division/order are). The drafter routinely omits the modifier and then burns
    # every repair round on "consider marking it noncomputable" (the #1 recurring
    # defs-route error). Prepend it deterministically; it never breaks a proof.
    out = af._prelint_stub("def omegaRatio (a b : ℝ) : ℝ := a / b\ntheorem t : True := by trivial")
    assert "noncomputable def omegaRatio" in out
    assert "noncomputable theorem" not in out          # theorems/lemmas untouched
    # idempotent: an already-noncomputable def is not double-marked
    out2 = af._prelint_stub("noncomputable def foo (a : ℝ) : ℝ := a\ntheorem t : True := by trivial")
    assert "noncomputable noncomputable" not in out2 and "noncomputable def foo" in out2
    # a private modifier is preserved
    out3 = af._prelint_stub("private def bar (a : ℝ) : ℝ := a\ntheorem t : True := by trivial")
    assert "private noncomputable def bar" in out3


def test_repair_hint_unknown_universe_suggests_type_star():
    h = af._repair_hint(["line 30:55: unknown universe level `u`"])
    assert "Type*" in h


# --- Task 1.4: gates go INDETERMINATE (not pass) on a wedged daemon ----------

def test_structural_gates_indeterminate_on_daemon_error():
    err = lambda *_a, **_kw: {"error": "daemon check did not complete: TimeoutError"}
    stub = "theorem foo (hB : True) : 1 = 1 := by sorry"
    lean = af.emit_target_files(_ISSUE, stub, _META)[0]
    d = af.depth_rejection(lean, "foo", _ISSUE["pointers"], check_fn=err)
    assert d.get("indeterminate") is True and not d.get("shallow")
    t = af.triviality_rejection(lean, check_fn=err)
    assert t.get("indeterminate") is True and not t.get("trivial")
    fr = af.defs_rejection(lean, "foo", ["fooDef"], check_fn=err)
    assert fr.get("indeterminate") is True and not fr.get("failed")
    # the derivable probe fails open to [] on a daemon error — never all-names
    assert af.derivable_hypotheses(lean, check_fn=err) == []


def test_daemon_check_infra_error_sets_sentinel():
    import probe as pr
    r = pr.daemon_check("theorem x : True := trivial", port=1)  # nothing listening
    assert r.get("error")             # singular infra sentinel present
    assert r.get("success") is False


# --- Task 1.5: strengthen-pass pure-parse guards (H8) ------------------------

def test_strengthen_keeps_sole_implicit_pin():
    # hBmeas is the ONLY use of the implicit {B}; dropping it orphans B → protect it,
    # even though the (faked) re-gate would pass.
    candidate = "theorem foo {B : ℝ → ℝ} (hBmeas : Measurable B) (x : ℝ) : x = x := by rfl"
    res = af.strengthen_candidate(candidate, None, "foo", ["unused variable `hBmeas`"],
                                  regate_fn=lambda c: {"passed": True, "warnings": []})
    assert "hBmeas" not in res["stripped"]
    assert "hBmeas" in res["candidate"]


def test_strengthen_whitelists_nonzero_binder_under_grind():
    # hA is flagged unused, but the proof is `by grind`, which pulls hypotheses from
    # context — the "unused variable" warning is unreliable, so keep the binder.
    candidate = "theorem foo (A : ℝ) (hA : A ≠ 0) : A = A := by grind"
    res = af.strengthen_candidate(candidate, None, "foo", ["unused variable `hA`"],
                                  regate_fn=lambda c: {"passed": True, "warnings": []})
    assert "hA" not in res["stripped"]
    assert "hA" in res["candidate"]


# --- Task 1.8: feasibility census at intent time (H12) -----------------------

def test_route_feasibility_blocks_on_missing_primitives():
    # names MathFin.omegaRatio (absent) + MathFin.zcb (present) + Real.exp (Mathlib,
    # not our concern) → blocked_on_infra with the missing list, no draft attempted.
    intent = {"objects": ["MathFin.zcb", "MathFin.omegaRatio", "Real.exp"], "statement": "x"}
    feas = af.route_feasibility(intent, ["MathFin/FixedIncome/ZCB.lean"],
                                lookup_fn=lambda name: name == "MathFin.zcb")
    assert feas["feasible"] is False
    assert feas["missing"] == ["MathFin.omegaRatio"]
    assert "MathFin.omegaRatio" in feas["note"]


def test_route_feasibility_ok_when_present_or_mathlib():
    intent = {"objects": ["MathFin.zcb", "Real.exp", "integral_add_compl"]}
    feas = af.route_feasibility(intent, [], lookup_fn=lambda n: n == "MathFin.zcb")
    assert feas["feasible"] is True and feas["missing"] == []


# --- Task 1.9: telemetry for silent channels (H11) ---------------------------

def test_retrieval_backend_is_labeled():
    r, _p = af.build_retrieve_fns(backend="loogle", main_repo="/x", index_dir="/no/index",
                                  k=4, embed_model="m", api_key=None)
    assert getattr(r, "backend", None) == "loogle"


def test_formalize_result_records_retrieval_backend_and_counters():
    intent = {"statement": "s", "objects": [], "module_name": "M", "benchmark_id": "b"}
    retrieve = lambda nm: "cand: MathFin.bar"   # noqa: E731
    retrieve.backend = "loogle"
    fr = af.formalize_with_repair(
        intent, "", issue=_ISSUE,
        chat_fn=lambda msgs: ("```lean\ntheorem t : MathFin.foo = 0 := by sorry\n```", 5),
        check_fn=lambda c: {"success": False, "sorry_count": 1,
                            "errors": ["Unknown identifier `MathFin.foo`"]},
        emit_fn=af.emit_target_files, rounds=1, retrieve_fn=retrieve)
    assert fr["ok"] is False
    assert fr["retrieval_backend"] == "loogle"        # which backend surfaced candidates
    assert fr["lint_repaired"] == 0 and fr["advised_bundle"] is False


def test_autoformalize_config_depth_gate_default_on():
    assert pl.AutoformalizeConfig.load(None).depth_gate is True


def test_autoformalize_config_depth_gate_reads_toml(tmp_path):
    toml = tmp_path / "pipeline.toml"
    toml.write_text("[autoformalize]\ndepth_gate = false\n")
    assert pl.AutoformalizeConfig.load(str(toml)).depth_gate is False


def test_autoformalize_config_two_stage_defaults():
    cfg = pl.AutoformalizeConfig.load(None)
    assert cfg.intent_model == "magistral-medium-latest"    # strongest Magistral reasoning tier
    assert cfg.formalize_model == "labs-leanstral-1-5"      # leanstral formalizes the Lean
    assert cfg.formalize_rounds == 3
    assert cfg.retrieval is True
    assert cfg.formalize_token_budget == 40_000            # early-abort a doomed draft


def test_autoformalize_config_retrieval_backend_defaults():
    cfg = pl.AutoformalizeConfig.load(None)
    assert cfg.retrieval_backend == "embedding"
    assert cfg.retrieval_k == 8
    assert cfg.embed_model == "mistral-embed"


def test_autoformalize_config_retrieval_backend_reads_toml(tmp_path):
    toml = tmp_path / "pipeline.toml"
    toml.write_text('[autoformalize]\nretrieval_backend = "loogle"\nretrieval_k = 4\n')
    cfg = pl.AutoformalizeConfig.load(str(toml))
    assert cfg.retrieval_backend == "loogle"
    assert cfg.retrieval_k == 4


def test_autoformalize_config_semantic_cascade_defaults():
    cfg = pl.AutoformalizeConfig.load(None)
    assert cfg.semantic_rounds == 2        # one fresh draft + one feedback re-draft
    assert cfg.triviality_gate is True


def test_autoformalize_config_semantic_cascade_reads_toml(tmp_path):
    toml = tmp_path / "pipeline.toml"
    toml.write_text("[autoformalize]\nsemantic_rounds = 3\ntriviality_gate = false\n")
    cfg = pl.AutoformalizeConfig.load(str(toml))
    assert cfg.semantic_rounds == 3
    assert cfg.triviality_gate is False


# --- split_statement ---------------------------------------------------------

def test_split_statement_simple():
    name, binders, concl = af.split_statement("theorem foo (x : ℝ) : x = x := by sorry")
    assert name == "foo"
    assert binders.strip() == "(x : ℝ)"
    assert concl.strip() == "x = x"


def test_split_statement_no_binders():
    name, binders, concl = af.split_statement("theorem foo : True := by sorry")
    assert name == "foo"
    assert binders.strip() == ""
    assert concl.strip() == "True"


def test_split_statement_forall_colon_stays_in_concl():
    # the FIRST depth-0 colon is the type separator; the ∀'s colon is later, so
    # it stays inside the conclusion.
    name, binders, concl = af.split_statement("theorem foo : ∀ x : ℝ, x = x := by sorry")
    assert name == "foo"
    assert binders.strip() == ""
    assert concl.strip() == "∀ x : ℝ, x = x"


def test_split_statement_fra_like_multigroup_multiline():
    stub = (
        "theorem fra {P₁ P₂ δ : ℝ} (hP₂ : 0 < P₂) (hδ : δ ≠ 0) :\n"
        "    P₁ = P₂ * (1 + δ) ∧ (δ = 0 ↔ P₁ = P₂) := by sorry"
    )
    name, binders, concl = af.split_statement(stub)
    assert name == "fra"
    assert "{P₁ P₂ δ : ℝ}" in binders
    assert "hP₂" in binders and "hδ" in binders
    assert "∧" in concl
    assert ":=" not in concl and "sorry" not in concl


def test_split_statement_ignores_leading_module_scaffold():
    stub = (
        "module\npublic import Mathlib\n-- pointers: A.lean\n"
        "/-! doc: a : b -/\nnamespace MathFin\n"
        "theorem bar (h : p) : q := by sorry\nend MathFin\n"
    )
    name, binders, concl = af.split_statement(stub)
    assert name == "bar"
    assert binders.strip() == "(h : p)"
    assert concl.strip() == "q"


# --- explicit_arg_names ------------------------------------------------------

def test_explicit_arg_names_only_parenthesized():
    # explicit () binders are passed to the re-export; {} implicit and [] instance
    # are inferred, so they are NOT passed.
    binders = "{P₁ P₂ δ : ℝ} (hP₂ : 0 < P₂) (hδ : δ ≠ 0)"
    assert af.explicit_arg_names(binders) == ["hP₂", "hδ"]


def test_explicit_arg_names_multiple_in_one_group():
    assert af.explicit_arg_names("(a b : ℝ) (h : a = b)") == ["a", "b", "h"]


def test_explicit_arg_names_none():
    assert af.explicit_arg_names("{x : ℝ}") == []


# --- section mapping ---------------------------------------------------------

def test_section_for_area_known():
    assert af.section_for_area("fixed-income") == "FixedIncome"
    assert af.section_for_area("actuarial") == "Actuarial"
    assert af.section_for_area("credit") == "FixedIncome"   # credit lives in FixedIncome


def test_section_for_area_new_and_unknown():
    assert af.section_for_area("fx") == "FX"                # new dir, umbrella absorbs
    assert af.section_for_area("stoch-vol") == "StochVol"   # unknown → CamelCase fallback


# --- emit_target_files -------------------------------------------------------

_ISSUE = {
    "number": 67,
    "area": "fixed-income",
    "title": "FRA value + fair rate",
    "difficulty": "good-first",
    "pointers": ["MathFin/FixedIncome/ForwardRate.lean", "MathFin/FixedIncome/ZCB.lean"],
}
_STUB = ("theorem fra_value {P₁ P₂ δ : ℝ} (hP₂ : 0 < P₂) (hδ : δ ≠ 0) :\n"
         "    P₁ = P₂ * (1 + δ) := by sorry")
_META = {"module_name": "FRA", "benchmark_id": "mf-fi-fra",
         "docstring": "FRA value and fair simple forward rate."}


def test_emit_stub_headers_and_scaffold():
    lean_text, _entry, _placement = af.emit_target_files(_ISSUE, _STUB, _META)
    assert "-- source-issue: 67" in lean_text
    assert "-- main-module: MathFin/FixedIncome/FRA.lean" in lean_text
    assert "-- benchmark: benchmarks/mathematical_finance.json" in lean_text
    assert "-- benchmark-id: mf-fi-fra" in lean_text
    assert ("-- pointers: MathFin/FixedIncome/ForwardRate.lean, "
            "MathFin/FixedIncome/ZCB.lean") in lean_text
    assert "@[expose] public section" in lean_text
    assert "namespace MathFin" in lean_text and "end MathFin" in lean_text
    assert "theorem fra_value" in lean_text
    assert "FRA value and fair simple forward rate." in lean_text
    assert lean_text.count("sorry") == 1        # exactly one — build_manifest requires it


def test_emit_stub_pins_autoImplicit_false_for_lake_parity():
    # run 4's PR-blocker verbatim: the daemon (autoImplicit on) auto-bound the
    # `u` in a drafted `{Ω : Type u}` and every gate passed; `lake build`
    # (lakefile: autoImplicit false) then failed with `unknown universe level`.
    # The emitted stub pins the option so DRAFT-time elaboration enforces what
    # the build enforces and the compile-repair loop fixes such drafts early.
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    assert "set_option autoImplicit false" in lean_text
    assert lean_text.index("set_option autoImplicit false") \
        < lean_text.index("namespace MathFin")


def test_emit_stub_opens_measuretheory_so_bare_names_resolve():
    # live root cause (run 29667784310, #60 + #109): the drafter emits IDIOMATIC
    # bare Mathlib names — `IsProbabilityMeasure`, `IntegrableOn`, `Measure` (all
    # under `MeasureTheory`) — exactly as the 155/262 MathFin modules that
    # `open MeasureTheory` do. The emitted stub module omitted the open, so every
    # measure-theory target died `unknown identifier` after a faithful re-draft.
    # Emit the house-idiom opens (Girsanov.lean:50-51) right after `namespace
    # MathFin`, so the model's correct idiomatic names resolve.
    stub = ("theorem prob_int {Ω : Type*} {m : MeasurableSpace Ω} (Q : Measure Ω)\n"
            "    [IsProbabilityMeasure Q] (f : Ω → ℝ) (hf : IntegrableOn f Set.univ Q) :\n"
            "    True := by sorry")
    lean_text, _e, _p = af.emit_target_files(_ISSUE, stub, _META)
    assert "open MeasureTheory ProbabilityTheory" in lean_text
    assert "open scoped NNReal ENNReal" in lean_text
    # in scope for the stub: opens sit between `namespace MathFin` and the theorem
    assert (lean_text.index("namespace MathFin")
            < lean_text.index("open MeasureTheory ProbabilityTheory")
            < lean_text.index("theorem prob_int"))


def test_emit_stub_imports_pointer_modules():
    # coherence-first: the stub imports its pointer modules so a drafted statement
    # can consume existing MathFin defs, not just Mathlib.
    lean_text, _entry, _placement = af.emit_target_files(_ISSUE, _STUB, _META)
    assert "public import Mathlib" in lean_text
    assert "public import MathFin.FixedIncome.ForwardRate" in lean_text
    assert "public import MathFin.FixedIncome.ZCB" in lean_text


def test_emit_stub_roundtrips_through_build_manifest():
    lean_text, _entry, placement = af.emit_target_files(_ISSUE, _STUB, _META)
    # the real consumer parses the placement headers + the decl the same way
    meta = bm.parse_meta(lean_text)
    assert meta["main_module"] == "MathFin/FixedIncome/FRA.lean"
    assert meta["benchmark"] == "benchmarks/mathematical_finance.json"
    assert meta["benchmark_id"] == "mf-fi-fra"
    assert meta["source_issue"] == 67
    assert bm.parse_pointers(lean_text) == _ISSUE["pointers"]


# --- new-object placement: the module is named for the object it introduces --

_ISSUE_162 = {
    "number": 162,
    "area": "performance",
    "title": "Upside-capture ratio",
    "pointers": ["MathFin/Performance/RatiosExtended.lean"],
}
_STUB_162 = ("theorem upCapture_scale_invariant {S : Type*} (up : Finset S)\n"
             "    (p b : S → ℝ) (c : ℝ) (h : (∑ s ∈ up, b s) ≠ 0) :\n"
             "    True := by sorry")


def test_emit_new_object_module_named_for_def_not_magistral_bucket():
    # #162 repro: magistral picked module_name "RatiosExtended" — an EXISTING module
    # the target also imports as a pointer (main-module == its own import). The old
    # code trusted that name and apply_contribution overwrote RatiosExtended.lean,
    # deleting its theorems -> AxiomAuditGen "unknown constant" -> PR blocked. A
    # new-object contribution creates its OWN module named for the object it
    # introduces: def `upCapture` -> module `UpCapture` (like #161 gainToPain -> GainToPain).
    meta = {"module_name": "RatiosExtended", "benchmark_id": "mf-performance-upside_capture",
            "docstring": "Upside-capture ratio.", "definitions": ["upCapture"]}
    lean_text, entry, placement = af.emit_target_files(_ISSUE_162, _STUB_162, meta)
    assert placement["main_module"] == "MathFin/Performance/UpCapture.lean"
    assert "-- main-module: MathFin/Performance/UpCapture.lean" in lean_text
    # the re-export imports the SAME fresh module, not the (would-be clobbered) pointer
    assert "import MathFin.Performance.UpCapture" in entry["code"]["lean"]
    # the pointer is still imported as a dependency (coherence-first)
    assert "public import MathFin.Performance.RatiosExtended" in lean_text


def test_emit_new_object_module_idempotent_when_magistral_already_canonical():
    # #161-style regression: magistral's bucket already equals the object's canonical
    # module (def gainToPain -> module GainToPain), so the collision guard never fires
    # and placement is unchanged.
    issue = {"number": 161, "area": "performance", "title": "Gain-to-pain ratio",
             "pointers": ["MathFin/Performance/RatiosExtended.lean"]}
    stub = ("theorem gainToPain_nonneg {α : Type*} (S : Finset α) (r : α → ℝ) :\n"
            "    True := by sorry")
    meta = {"module_name": "GainToPain", "benchmark_id": "mf-performance-gain_to_pain",
            "docstring": "Gain-to-pain ratio.", "definitions": ["gainToPain"]}
    _lt, _e, placement = af.emit_target_files(issue, stub, meta)
    assert placement["main_module"] == "MathFin/Performance/GainToPain.lean"


def test_emit_rejects_self_import_when_no_new_def_to_derive_from():
    # a theorem-route target whose module_name equals a pointer would make
    # apply_contribution overwrite that existing module, and there is no new def to
    # derive a fresh name from -> reject loudly rather than clobber.
    issue = {"number": 200, "area": "performance", "title": "x",
             "pointers": ["MathFin/Performance/RatiosExtended.lean"]}
    meta = {"module_name": "RatiosExtended", "benchmark_id": "mf-x", "docstring": "d"}
    try:
        af.emit_target_files(issue, "theorem foo : True := by sorry", meta)
        raised = False
    except ValueError:
        raised = True
    assert raised, "self-importing main-module with no new def must be rejected"


# --- honest subsetting: declared `deferred` remainder ------------------------

_DEFERRED_FACT = "term-structure monotonicity: T ↦ F(T) increasing iff r > δ"
_META_SUBSET = {**_META, "deferred": [_DEFERRED_FACT]}


def test_normalize_deferred_cleans_and_coerces():
    assert af.normalize_deferred(None) == []
    assert af.normalize_deferred([]) == []
    assert af.normalize_deferred(["a", " b ", "", "  "]) == ["a", "b"]
    assert af.normalize_deferred("solo fact") == ["solo fact"]   # bare string → one item
    assert af.normalize_deferred(["x", 3]) == ["x", "3"]         # non-str coerced


def test_two_stage_prompts_document_subset_and_stub_contract():
    # the honest-subsetting contract lives in the INTENT prompt (deferred facts are
    # declared there); the stub-format contract lives in the FORMALIZE prompt.
    intent_joined = " ".join(m["content"] for m in af.intent_messages(
        {"number": 88, "title": "t", "body": "b", "pointers": []}, ""))
    assert "deferred" in intent_joined     # the json field declared on a subset
    assert "SUBSET" in intent_joined       # subsetting is explicitly allowed
    formalize_joined = " ".join(m["content"] for m in af.formalize_messages(
        {"statement": "s", "objects": []}, ""))
    assert ":= by sorry" in formalize_joined   # the stub-format contract


def test_judge_system_does_not_fault_provable_hypotheses():
    # #67 was wrongly rejected for "missing positivity hypotheses" though its zcb (Real.exp) is
    # provably positive — the judge must not fault a hypothesis provable from the consumed defs.
    msgs = af.judge_messages({"number": 1, "title": "t", "body": "b"}, "theorem foo : True := by sorry")
    sys = " ".join(m["content"] for m in msgs)
    assert "PROVABLE" in sys and "automatically positive" in sys


def test_refill_logs_rejected_statement_on_unfaithful(monkeypatch, tmp_path):
    # instrument the reject path: log the ACTUAL statement so we stop inferring why it was rejected.
    _two_stage_ok(monkeypatch)
    monkeypatch.setattr(af, "depth_rejection", lambda lt, nm, ptr, **k: {"shallow": False, "tokens": 0})
    monkeypatch.setattr(af, "hypothesis_rejection", lambda *a, **k: {"vacuous": False, "tokens": 1})
    monkeypatch.setattr(af, "disproof", lambda *a, **k: {"false": False, "tokens": 1})
    monkeypatch.setattr(af, "judge_faithfulness",
                        lambda i, s, chat_fn, deferred=None: {"faithful": False, "verdict": "missing X", "tokens": 1})
    monkeypatch.setattr(af, "intent_fidelity_check", lambda intent, s, *, reason_fn: {"faithful": True, "tokens": 1})
    logs = []
    af.refill([_issue(7)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
              context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000, log=lambda m: logs.append(m))
    blob = "\n".join(logs)
    assert "unfaithful" in blob and "theorem t7" in blob   # verdict + the actual rejected statement


def test_judge_messages_includes_declared_deferred():
    msgs = af.judge_messages({"number": 88, "title": "t", "body": "b"},
                             "theorem foo : True := by sorry",
                             ["monotonicity in T", "basis → 0"])
    joined = " ".join(m["content"] for m in msgs)
    assert "DECLARED DEFERRED" in joined
    assert "monotonicity in T" in joined and "basis → 0" in joined


def test_judge_messages_no_deferred_section_when_full_issue():
    msgs = af.judge_messages({"number": 1, "title": "t", "body": "b"},
                             "theorem foo : True := by sorry")
    assert "DECLARED DEFERRED" not in " ".join(m["content"] for m in msgs)


def test_judge_faithfulness_threads_deferred_to_the_judge():
    seen = {}
    def chat(msgs):
        seen["msgs"] = msgs
        return ('{"faithful": true, "verdict": "subset ok", "issues": []}', 5)
    r = af.judge_faithfulness({"number": 88, "title": "t", "body": "b"},
                              "theorem foo : True := by sorry",
                              chat_fn=chat, deferred=[_DEFERRED_FACT])
    assert r["faithful"] is True and r["tokens"] == 5
    assert _DEFERRED_FACT in " ".join(m["content"] for m in seen["msgs"])


def test_emit_carries_declared_deferred():
    lean_text, entry, placement = af.emit_target_files(_ISSUE, _STUB, _META_SUBSET)
    assert f"-- deferred: {_DEFERRED_FACT}" in lean_text        # header build_manifest reads
    assert placement["deferred"] == [_DEFERRED_FACT]
    scope = entry["metadata"]["formalization_scope"]
    assert "SUBSET of issue #67" in scope and "term-structure monotonicity" in scope
    assert entry["metadata"]["provenance"]["deferred"] == [_DEFERRED_FACT]
    # and it round-trips through the real manifest parser (→ open-pr's follow-ups)
    assert bm.parse_meta(lean_text)["deferred"] == [_DEFERRED_FACT]
    assert lean_text.count("sorry") == 1                        # still a well-formed stub


def test_emit_full_issue_has_no_deferred_noise():
    lean_text, entry, placement = af.emit_target_files(_ISSUE, _STUB, _META)
    assert "-- deferred:" not in lean_text
    assert placement["deferred"] == []
    assert "SUBSET" not in entry["metadata"]["formalization_scope"]
    assert "deferred" not in entry["metadata"]["provenance"]
    name, _b, _c = af.split_statement(lean_text)
    assert name == "fra_value"


def test_emit_entry_reexport_and_provenance():
    _lean, entry, _placement = af.emit_target_files(_ISSUE, _STUB, _META)
    assert entry["id"] == "mf-fi-fra"
    assert entry["domain"] == "mathematical_finance"
    assert entry["metadata"]["formalization_status"] == "full"
    assert entry["metadata"]["provenance"] == {
        "statement_source": "magistral-autoform",
        "statement_model": "magistral-medium",
        "source": "leanstral-autoform",
        "model": "labs-leanstral-1-5",
        "issue": 67,
    }
    code = entry["code"]["lean"]
    assert "import MathFin.FixedIncome.FRA" in code
    assert "theorem mf_fi_fra" in code                  # dashes -> underscores
    assert "MathFin.fra_value hP₂ hδ" in code           # applies the module lemma, explicit args only
    assert "sorry" not in code                          # a re-export, not a proof


def test_emit_placement_dict():
    _lean, _entry, placement = af.emit_target_files(_ISSUE, _STUB, _META)
    assert placement == {
        "main_module": "MathFin/FixedIncome/FRA.lean",
        "benchmark": "benchmarks/mathematical_finance.json",
        "benchmark_id": "mf-fi-fra",
        "source_issue": 67,
        "deferred": [],        # full-issue proof carries an empty deferred list
    }


# --- kernel-gate goal builders -----------------------------------------------

def test_vacuity_goal_swaps_conclusion_to_false():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    goal = af.vacuity_goal(lean_text)
    assert "public import Mathlib" in goal           # imports preserved
    assert "(hP₂ : 0 < P₂)" in goal                  # hypotheses preserved
    assert ": False" in goal
    assert "P₁ = P₂ * (1 + δ)" not in goal           # original conclusion removed
    assert goal.count("sorry") == 1                  # still a provable stub


def test_disproof_goal_negates_conclusion():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    goal = af.disproof_goal(lean_text)
    assert "(hP₂ : 0 < P₂)" in goal                  # hypotheses preserved
    assert "¬ (" in goal
    assert "P₁ = P₂ * (1 + δ)" in goal               # original conclusion appears, negated
    assert goal.count("sorry") == 1


# --- magistral reply parsers -------------------------------------------------

def test_parse_verdict_json_block():
    reply = '```json\n{"faithful": false, "verdict": "missing X", "issues": ["X"]}\n```'
    v = af.parse_verdict(reply)
    assert v["faithful"] is False
    assert v["issues"] == ["X"]


def test_parse_verdict_fails_closed_when_unparseable():
    # no JSON → treat as NOT faithful (never ship an unverified statement)
    assert af.parse_verdict("looks fine to me")["faithful"] is False


# --- chat-mediated runners (injected chat_fn) --------------------------------

def _canned_chat(reply, tokens=100):
    return lambda msgs: (reply, tokens)


def _script_chat(replies, tokens=10):
    it = iter(replies)
    return lambda msgs: (next(it), tokens)


def test_judge_faithfulness_parses_verdict():
    chat = _canned_chat('```json\n{"faithful": true, "verdict": "ok", "issues": []}\n```', 42)
    r = af.judge_faithfulness({"number": 1, "title": "t", "body": "b"},
                              "theorem foo : True := by sorry", chat_fn=chat)
    assert r["faithful"] is True
    assert r["tokens"] == 42


# --- kernel-gate runners (drive run_target with injected chat_fn/check_fn) ----

_PROVES = lambda code: {"success": True, "errors": [], "sorry_count": 0}
_FAILS = lambda code: {"success": False, "errors": ["unsolved goals"], "sorry_count": 0}


def test_hypothesis_rejection_flags_provable_false():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    # an HONEST vacuity proof keeps the probed `: False` conclusion
    honest = "```lean\n" + af.vacuity_goal(lean_text).replace("sorry", "trivial") + "\n```"
    r = af.hypothesis_rejection(lean_text, "fra_value",
                                chat_fn=_canned_chat(honest, 50),
                                check_fn=_PROVES, budget=20000)
    assert r["vacuous"] is True
    assert r["tokens"] > 0


def test_hypothesis_rejection_passes_when_unprovable():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    r = af.hypothesis_rejection(lean_text, "fra_value",
                                chat_fn=_canned_chat("```lean\nattempt\n```", 50),
                                check_fn=_FAILS, budget=20000)
    assert r["vacuous"] is False


def test_hypothesis_rejection_rejects_reverted_conclusion():
    # The adversarial prover cannot prove the probed `: False`, so it REVERTS the
    # conclusion to the provable original and returns a clean file. run_target checks
    # whatever file it returns, so this "passes" — a false vacuous verdict that
    # deterministically kills easy TRUE targets (caught on cal-bk-161). The
    # statement-fidelity guard must count a pass only when the winning candidate
    # still asserts the probed conclusion.
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    # sorry-free (the prover reverts AND proves), original conclusion, not False
    reverted = "```lean\n" + lean_text.replace("sorry", "trivial") + "\n```"
    r = af.hypothesis_rejection(lean_text, "fra_value",
                                chat_fn=_canned_chat(reverted, 50),
                                check_fn=_PROVES, budget=20000)
    assert r["vacuous"] is False


def test_disproof_flags_provable_negation():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    # an HONEST disproof keeps the probed `¬ (C)` conclusion
    honest = "```lean\n" + af.disproof_goal(lean_text).replace("sorry", "trivial") + "\n```"
    r = af.disproof(lean_text, "fra_value",
                    chat_fn=_canned_chat(honest, 50),
                    check_fn=_PROVES, budget=20000)
    assert r["false"] is True


def test_disproof_passes_when_negation_unprovable():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    r = af.disproof(lean_text, "fra_value",
                    chat_fn=_canned_chat("```lean\nattempt\n```", 50),
                    check_fn=_FAILS, budget=20000)
    assert r["false"] is False


def test_gate_is_lightened_to_a_single_daemon_check_by_default():
    # each gate attempt is a daemon elaboration; the probe is a cheapest-first
    # SAFETY NET, not a proof to maximize. Default it to pass@1 / single round so
    # the two gates cost 2 checks per issue, not ~8 (fanout 2 x 2 rounds each) —
    # the load that let one spinning candidate wedge the daemon.
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    calls = []

    def counting_check(code):
        calls.append(code)
        return {"success": False, "errors": ["unsolved goals"], "sorry_count": 0}

    r = af.hypothesis_rejection(lean_text, "fra_value",
                                chat_fn=_canned_chat("```lean\nattempt\n```", 50),
                                check_fn=counting_check, budget=20000)
    assert r["vacuous"] is False
    assert len(calls) == 1


# --- pointers-scoped depth gate (option B) -----------------------------------

def test_depth_probe_pointers_scoped_targets_pointer_module_defs():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    probe = af.depth_probe(lean_text, "fra_value", _ISSUE["pointers"])
    assert lean_text.rstrip() in probe                         # elaborates the stub first
    assert "`MathFin.fra_value" in probe                       # looks the decl up by full name
    assert "`MathFin.FixedIncome.ForwardRate" in probe         # pointer modules, Lean-name form
    assert "`MathFin.FixedIncome.ZCB" in probe
    assert "getUsedConstants" in probe and "getModuleIdxFor?" in probe
    assert "depth-gate:" in probe                              # the reject marker


def test_depth_rejection_skips_when_no_pointers():
    # the gate is pointers-scoped; with no pointers it is INAPPLICABLE — skip (never
    # reject for a missing Pointers section, and never touch the daemon).
    lean_text, _e, _p = af.emit_target_files({**_ISSUE, "pointers": []}, _STUB, _META)
    called = {"n": 0}

    def check(code):
        called["n"] += 1
        return {"success": False, "errors": [_DEPTH_ERR], "sorry_count": 1}
    r = af.depth_rejection(lean_text, "fra_value", [], check_fn=check)
    assert r["shallow"] is False
    assert called["n"] == 0                                    # daemon not consulted


_DEPTH_ERR = ("line 21:0: depth-gate: statement type consumes no def from "
              "pointer modules [MathFin.FixedIncome.ForwardRate, MathFin.FixedIncome.ZCB]")


def test_depth_rejection_rejects_on_depth_error():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    check = lambda code: {"success": False, "errors": [_DEPTH_ERR],
                          "warnings": ["declaration uses `sorry`"], "sorry_count": 1}
    r = af.depth_rejection(lean_text, "fra_value", _ISSUE["pointers"], check_fn=check)
    assert r["shallow"] is True
    assert "depth-gate" in r["verdict"]
    assert r["tokens"] == 0                                    # daemon elaboration, no prover


def test_depth_rejection_passes_when_only_sorry_warning():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    check = lambda code: {"success": False, "errors": [],
                          "warnings": ["declaration uses `sorry`"], "sorry_count": 1}
    r = af.depth_rejection(lean_text, "fra_value", _ISSUE["pointers"], check_fn=check)
    assert r["shallow"] is False


def test_depth_rejection_fails_open_on_daemon_error():
    # a daemon-communication failure (Fix 1b's error dict) is NOT a depth verdict —
    # do not reject a good target on an infra hiccup (fail-open, like the prover gates).
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    check = lambda code: {"success": False,
                          "errors": ["daemon check did not complete: TimeoutError: timed out"],
                          "sorry_count": 0}
    r = af.depth_rejection(lean_text, "fra_value", _ISSUE["pointers"], check_fn=check)
    assert r["shallow"] is False


# --- triviality gate (the #67 class: rfl/simp-closable statements) ------------

def test_triviality_goal_splices_tactic_over_sorry():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    goal = af.triviality_goal(lean_text)
    assert goal is not None
    assert "sorry" not in goal
    assert ":= by first | rfl | simp" in goal
    assert "end MathFin" in goal                       # module scaffold intact


def test_triviality_goal_handles_bare_sorry():
    goal = af.triviality_goal("theorem t : True := sorry\n")
    assert goal == "theorem t : True := by first | rfl | simp\n"


def test_triviality_goal_none_without_sorry():
    assert af.triviality_goal("theorem t : True := trivial") is None


def test_triviality_rejection_flags_rfl_closable():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    check = lambda code: {"success": True, "errors": [], "sorry_count": 0}
    r = af.triviality_rejection(lean_text, check_fn=check)
    assert r["trivial"] is True
    assert r["tokens"] == 0                            # daemon elaboration, no prover
    assert "rfl" in r["verdict"]


def test_triviality_rejection_passes_substantive_statement():
    # rfl and simp both fail on real content → elaboration errors → NOT trivial
    # (the healthy case; also covers daemon-error fail-open, same dict shape).
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    check = lambda code: {"success": False,
                          "errors": ["The rfl tactic failed", "simp made no progress"],
                          "sorry_count": 0}
    r = af.triviality_rejection(lean_text, check_fn=check)
    assert r["trivial"] is False


def test_triviality_rejection_fails_open_without_sorry():
    called = {"n": 0}

    def check(code):
        called["n"] += 1
        return {"success": True, "errors": [], "sorry_count": 0}
    r = af.triviality_rejection("theorem t : True := trivial", check_fn=check)
    assert r["trivial"] is False
    assert called["n"] == 0                            # daemon not consulted


# --- gate feedback rendering (the repair cascade's re-draft signal) -----------

def test_render_gate_feedback_depth_carries_stub_and_instruction():
    fb = af.render_gate_feedback(
        "depth", "type consumes no def from [MathFin.FixedIncome.ZCB]", _STUB)
    assert "`depth` gate" in fb
    assert "consumes no def" in fb                     # the gate's own verdict
    assert "theorem fra_value" in fb                   # the rejected stub rides along
    assert "EXPRESSED THROUGH" in fb                   # the repair direction
    assert "inline" in fb


def test_render_gate_feedback_false_drops_genuinely_false_conjunct():
    # a "false" verdict still guards a genuinely-true claim from being weakened, but
    # a conjunct that is FALSE as the issue states it must be DROPPED, corrected in
    # `deferred`, and the true remainder proved (subset-drop — the #73 maxDD case).
    fb = af.render_gate_feedback("false", "", None)
    assert "NEGATION" in fb
    assert "weaken" in fb                              # a true claim is still protected
    assert "CORRECTION" in fb and "deferred" in fb     # false conjunct → drop + correct + defer
    assert "```lean" not in fb                         # no stub block when none given


def test_render_gate_feedback_unknown_gate_generic():
    fb = af.render_gate_feedback("mystery", "d", None)
    assert "mystery" in fb and "without weakening" in fb


def test_intent_messages_carry_feedback():
    msgs = af.intent_messages(_ISSUE, "SIGS", feedback="PREV-FB")
    user = msgs[1]["content"]
    assert "PREV-FB" in user
    assert "REVISED intent" in user
    assert user.index("SIGS") < user.index("PREV-FB")  # feedback lands after context


def test_intent_messages_no_feedback_block_by_default():
    user = af.intent_messages(_ISSUE, "SIGS")[1]["content"]
    assert "REVISED intent" not in user


def test_draft_intent_threads_feedback_to_chat():
    seen = {}

    def chat(msgs):
        seen["user"] = msgs[1]["content"]
        return ('{"statement": "s", "module_name": "M", "benchmark_id": "b"}', 7)
    r = af.draft_intent(_ISSUE, "", chat_fn=chat, feedback="FIX-THIS")
    assert r["ok"] is True
    assert "FIX-THIS" in seen["user"]


def test_intent_reject_reason_pinpoints_the_missing_piece():
    # "no parseable intent reply" was opaque (#109 r1; recurs on #66/#88/#73) — it
    # fired identically whether the reply carried no JSON at all or JSON missing any
    # one required field. Record WHICH, so the next run's telemetry pinpoints the
    # cause instead of us guessing which piece the model dropped.
    assert af.intent_reject_reason("prose, no json") == "no JSON object in reply"
    assert (af.intent_reject_reason('{"module_name":"M","benchmark_id":"b"}')
            == "JSON missing 'statement'")
    assert (af.intent_reject_reason('{"statement":"s","benchmark_id":"b"}')
            == "JSON missing 'module_name'")
    assert (af.intent_reject_reason('{"statement":"s","module_name":"M"}')
            == "JSON missing 'benchmark_id'")
    assert af.intent_reject_reason(
        '{"statement":"s","module_name":"M","benchmark_id":"b"}') is None


def test_draft_intent_surfaces_reject_reason():
    di = af.draft_intent(_ISSUE, "", chat_fn=lambda m: ("prose, no json", 4))
    assert di["ok"] is False
    assert di["reason"] == "no JSON object in reply"


def test_formalize_messages_carry_revision_note():
    intent = {"statement": "s", "objects": ["MathFin.zcb"]}
    user = af.formalize_messages(intent, "SIGS", revision_note="NOTE-X")[1]["content"]
    assert "NOTE-X" in user and "SIGS" in user


def test_formalize_with_repair_threads_revision_note():
    intent = {"statement": "s", "objects": [], "module_name": "M", "benchmark_id": "b"}
    seen = {}

    def chat(msgs):
        seen["user"] = msgs[1]["content"]
        return ("```lean\ntheorem t (h : p) : q := by sorry\n```", 5)
    fr = af.formalize_with_repair(intent, "", issue=_ISSUE, chat_fn=chat,
                                  check_fn=lambda c: {"success": True, "sorry_count": 1,
                                                      "errors": []},
                                  emit_fn=af.emit_target_files, rounds=1,
                                  revision_note="NOTE-Y")
    assert fr["ok"] is True
    assert "NOTE-Y" in seen["user"]


# --- primitives-aware routing (F3+F2): measure, classify, route ---------------

def test_count_pointer_defs_counts_consumable_exports(tmp_path):
    mod = tmp_path / "MathFin" / "X" / "A.lean"
    mod.parent.mkdir(parents=True)
    mod.write_text("noncomputable def a : ℝ := 0\n"
                   "abbrev b := ℝ\n"
                   "structure C where\n  x : ℝ\n"
                   "@[simp] def d : ℕ := 1\n"
                   "theorem t : True := trivial\n"    # theorems are not consumables
                   "-- def commented : not counted\n")
    assert af.count_pointer_defs(str(tmp_path), ["MathFin/X/A.lean"]) == 4


def test_count_pointer_defs_missing_file_counts_zero(tmp_path):
    assert af.count_pointer_defs(str(tmp_path), ["MathFin/Nope.lean", "notlean"]) == 0


def test_classify_refill_families():
    assert af.classify_refill({"outcome": "seeded"}) == "seeded"
    assert af.classify_refill({"outcome": "depth"}) == "needs_primitives"
    assert af.classify_refill({"outcome": "newdef_depth"}) == "defs_rejected"
    assert af.classify_refill({"outcome": "ungrounded"}) == "defs_rejected"
    assert af.classify_refill({"outcome": "trivial"}) == "trivial_restatement"
    assert af.classify_refill({"outcome": "unfaithful"}) == "fidelity"
    assert af.classify_refill({"outcome": "drift"}) == "fidelity"
    assert af.classify_refill({"outcome": "intent"}) == "undraftable"
    assert af.classify_refill({"outcome": "formalize"}) == "undraftable"
    assert af.classify_refill({"outcome": "vacuous"}) == "statement_wrong"
    assert af.classify_refill({"outcome": "false"}) == "statement_wrong"
    assert af.classify_refill({"outcome": "budget"}) == "budget"
    assert af.classify_refill({"outcome": "error"}) == "infra"


def test_classify_refill_depth_evidence_beats_trailing_noise():
    # the CI run's #66 verbatim: depth-rejected on attempt 1, then a flaky
    # intent parse on attempt 2 — the depth evidence must still classify it
    # needs_primitives, or the router never learns.
    rec = {"outcome": "intent", "history": [{"gate": "depth"}, {"gate": "intent"}]}
    assert af.classify_refill(rec) == "needs_primitives"
    rec2 = {"outcome": "formalize",
            "history": [{"gate": "ungrounded"}, {"gate": "formalize"}]}
    assert af.classify_refill(rec2) == "defs_rejected"
    # seeded always wins, whatever the earlier rows say
    assert af.classify_refill({"outcome": "seeded",
                               "history": [{"gate": "depth"}]}) == "seeded"


def test_order_by_route_prefers_def_rich_and_demotes_lemons():
    # the CI run attempted the same three lemons and never reached def-rich
    # #108 at position 6. Within a route group: fresh before demoted families,
    # easier difficulty first, then MORE pointer consumables, then number.
    xs = [
        {"number": 53, "route": "theorem", "difficulty": "small", "def_count": 1},
        {"number": 61, "route": "theorem", "difficulty": "small", "def_count": 5,
         "family": "undraftable"},                     # lemon: demoted despite riches
        {"number": 108, "route": "theorem", "difficulty": "small", "def_count": 4},
        {"number": 7, "route": "defs", "difficulty": "small", "def_count": 0},
    ]
    assert [i["number"] for i in af.order_by_route(xs)] == [108, 53, 7, 61]


def test_route_for_history_beats_measurement():
    # runtime evidence (needs_primitives) routes to defs even when the pointer
    # modules export defs — the #53 case (chooserPrice exists; the faithful
    # statement can't use it).
    assert af.route_for({}, def_count=3, family="needs_primitives") == "defs"
    assert af.route_for({}, def_count=0, family=None) == "defs"
    assert af.route_for({}, def_count=1, family=None) == "theorem"
    assert af.route_for({}, def_count=1, family="seeded") == "theorem"


def test_resolve_route_cli_override_beats_auto():
    # an explicit --route wins over the automatic classifier (operator/test
    # affordance); None falls back to route_for. Lets a def-rich-pointer target
    # like #73 be driven straight to the defs route without a wasted theorem tick.
    iss = {"number": 73}
    assert af.resolve_route("defs", iss, def_count=5, family=None) == "defs"
    assert af.resolve_route("theorem", iss, def_count=0, family="needs_primitives") == "theorem"
    assert af.resolve_route(None, iss, def_count=0, family=None) == "defs"
    assert af.resolve_route(None, iss, def_count=5, family=None) == "theorem"


def test_dump_draft_writes_only_when_env_set(tmp_path, monkeypatch):
    import json as _json
    issue = {"number": 73}
    lean = "theorem t : True := by sorry"
    elab = {"errors": ["boom"], "sorry_count": 1, "warnings": []}
    # unset ⇒ no-op (failed drafts are discarded unless a dir is requested)
    monkeypatch.delenv("AUTOFORM_DUMP_DRAFTS", raising=False)
    af._dump_draft(issue, 1, lean, elab)
    assert not list(tmp_path.iterdir())
    # set ⇒ writes the emitted draft + its elaborator verdict, per round
    monkeypatch.setenv("AUTOFORM_DUMP_DRAFTS", str(tmp_path))
    af._dump_draft(issue, 2, lean, elab)
    assert (tmp_path / "draft-73-r2.lean").read_text() == lean
    v = _json.loads((tmp_path / "draft-73-r2.errors.json").read_text())
    assert v["errors"] == ["boom"] and v["sorry_count"] == 1


def test_order_by_route_route_does_not_rank():
    # the route selects the PATH, not the priority — defs-routed issues carry
    # positive evidence and must not queue behind the whole theorem backlog.
    xs = [{"number": 1, "route": "defs"}, {"number": 2, "route": "theorem"},
          {"number": 3, "route": "defs"}, {"number": 4, "route": "theorem"}]
    assert [i["number"] for i in af.order_by_route(xs)] == [1, 2, 3, 4]


def test_load_refill_families_latest_wins_and_tolerates_junk(tmp_path):
    A = af.ROUTING_ARCH
    p = tmp_path / "h.jsonl"
    p.write_text(f'{{"issue": 53, "outcome": "depth", "arch": "{A}"}}\n'   # no family → classified
                 f'{{"issue": 9, "outcome": "trivial", "arch": "{A}"}}\n'
                 'not-json\n'
                 f'{{"issue": 53, "outcome": "seeded", "family": "seeded", "arch": "{A}"}}\n')
    fams = af.load_refill_families(str(p))
    assert fams[53] == "seeded"                      # latest record wins
    assert fams[9] == "trivial_restatement"
    assert af.load_refill_families(str(tmp_path / "absent.jsonl")) == {}


def test_evidence_is_architecture_scoped(tmp_path):
    # R's rule: never steer the current architecture by a past architecture's
    # failures. Unstamped and foreign-arch records are inert for routing (they
    # stay in the file as telemetry); an arch bump runs from zero automatically.
    p = tmp_path / "h.jsonl"
    p.write_text(
        '{"issue": 53, "outcome": "depth"}\n'                              # pre-stamp era
        '{"issue": 53, "outcome": "depth", "arch": "routing-v0-old"}\n'    # old architecture
        '{"issue": 53, "history": [{"unknown_identifiers": ["MathFin.old"]}], '
        '"arch": "routing-v0-old"}\n')
    assert af.load_refill_families(str(p)) == {}
    assert af.load_prior_unknowns(str(p)) == {}


def test_refill_records_carry_arch_stamp(monkeypatch, tmp_path):
    _two_stage_ok(monkeypatch)
    _pass_gates(monkeypatch)
    res = af.refill([_issue(5)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert res["attempted"][0]["arch"] == af.ROUTING_ARCH


def test_load_prior_unknowns_unions_across_records(tmp_path):
    A = af.ROUTING_ARCH
    p = tmp_path / "h.jsonl"
    p.write_text(
        f'{{"issue": 53, "history": [{{"gate": "depth", "unknown_identifiers": ["MathFin.a"]}}], "arch": "{A}"}}\n'
        f'{{"issue": 53, "history": [{{"gate": "depth", "unknown_identifiers": ["MathFin.a", "MathFin.b"]}}], "arch": "{A}"}}\n'
        f'{{"issue": 9, "history": [{{"gate": "trivial"}}], "arch": "{A}"}}\n')
    u = af.load_prior_unknowns(str(p))
    assert u[53] == ["MathFin.a", "MathFin.b"]
    assert u.get(9, []) == []


# --- adversarial-gate goal cache (item L) ------------------------------------

def test_try_prove_returns_cached_verdict_without_running(tmp_path):
    import gate_cache as gc
    c = gc.GateCache(str(tmp_path / "c.json"))
    c.put("GOAL", True)                          # pre-seeded refutation
    calls = []
    proved, tokens = af._try_prove(
        "GOAL", "t", chat_fn=lambda m: ("x", 5),
        check_fn=lambda code: calls.append(code) or {"success": True, "sorry_count": 0, "errors": []},
        budget=100, cache=c)
    assert proved is True and tokens == 0        # substituted — no new spend
    assert calls == []                           # the daemon/prover were never touched


def test_try_prove_stores_verdict_on_miss(monkeypatch, tmp_path):
    import gate_cache as gc
    c = gc.GateCache(str(tmp_path / "c.json"))
    monkeypatch.setattr(af, "run_target", lambda target, **k: {"outcome": "max_rounds", "tokens": 7})
    goal = "theorem g : False := by sorry"
    proved, tokens = af._try_prove(goal, "g", chat_fn=lambda m: ("", 0),
                                   check_fn=lambda code: {}, budget=100, cache=c)
    assert proved is False and tokens == 7
    assert c.get(goal) is False                  # verdict cached
    # a repeat now hits the cache — run_target must not run again
    monkeypatch.setattr(af, "run_target",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-ran a cached goal")))
    assert af._try_prove(goal, "g", chat_fn=lambda m: ("", 0),
                         check_fn=lambda code: {}, budget=100, cache=c) == (False, 0)


def test_try_prove_without_cache_is_unchanged(monkeypatch):
    # default cache=None → the existing behaviour, no caching
    monkeypatch.setattr(af, "run_target", lambda target, **k: {"outcome": "max_rounds", "tokens": 3})
    assert af._try_prove("g", "g", chat_fn=lambda m: ("", 0),
                         check_fn=lambda c: {}, budget=100) == (False, 3)


def test_autoformalize_config_gate_cache_defaults_off():
    assert pl.AutoformalizeConfig.load(None).gate_cache is False


# --- statement-integrity pin: _probed_signature (item J) ---------------------

def test_probed_signature_captures_binders_and_conclusion():
    text = "import Mathlib\ntheorem foo (h : p) : q := by sorry"
    assert af._probed_signature(text, "foo") == "(h : p) : q"


def test_probed_signature_none_when_name_absent():
    assert af._probed_signature("theorem foo : q := by sorry", "bar") is None


def test_probed_signature_selects_the_named_theorem_past_helpers():
    # a helper lemma precedes the target — the pin must locate the NAMED one
    text = ("lemma helper : True := trivial\n"
            "theorem foo (h : p) : q := by sorry")
    assert af._probed_signature(text, "foo") == "(h : p) : q"


def test_probed_signature_is_whitespace_normalized():
    a = "theorem foo (h : p) : q := by sorry"
    b = "theorem foo    (h : p)  :   q := h"
    assert af._probed_signature(a, "foo") == af._probed_signature(b, "foo")


# --- cross-tick lessons-learned + diversity injection (item K) ----------------

def test_load_prior_lessons_summarizes_latest_failed_record(tmp_path):
    A = af.ROUTING_ARCH
    p = tmp_path / "h.jsonl"
    p.write_text(
        f'{{"issue": 88, "outcome": "formalize", "arch": "{A}", '
        f'"history": [{{"gate": "intent"}}, {{"gate": "formalize", "detail": "no elaborating Lean"}}]}}\n'
        f'{{"issue": 88, "outcome": "unfaithful", "arch": "{A}", '
        f'"history": [{{"gate": "unfaithful", "detail": "inequality direction wrong"}}]}}\n')
    lessons = af.load_prior_lessons(str(p))
    assert 88 in lessons
    L = lessons[88]
    assert L["family"] == "fidelity"                 # latest record's family
    assert L["last_gate"] == "unfaithful"
    assert L["last_detail"] == "inequality direction wrong"
    assert L["gates_tried"] == ["unfaithful"]        # from the latest record's history
    assert L["prior_ticks"] == 2                      # two failed ticks counted


def test_load_prior_lessons_skips_wins_and_non_verdicts_and_foreign_arch(tmp_path):
    A = af.ROUTING_ARCH
    p = tmp_path / "h.jsonl"
    p.write_text(
        f'{{"issue": 1, "outcome": "seeded", "arch": "{A}", "history": []}}\n'      # a win
        f'{{"issue": 2, "outcome": "indeterminate", "arch": "{A}", "history": [{{"gate": "indeterminate"}}]}}\n'
        '{"issue": 3, "outcome": "depth", "arch": "routing-v0-old", "history": [{"gate": "depth"}]}\n'
        'not-json\n')
    lessons = af.load_prior_lessons(str(p))
    assert lessons == {}                               # nothing substantive to learn from
    assert af.load_prior_lessons(str(tmp_path / "absent.jsonl")) == {}


def test_load_prior_lessons_seeded_clears_a_stale_lesson(tmp_path):
    A = af.ROUTING_ARCH
    p = tmp_path / "h.jsonl"
    p.write_text(
        f'{{"issue": 5, "outcome": "depth", "arch": "{A}", "history": [{{"gate": "depth"}}]}}\n'
        f'{{"issue": 5, "outcome": "seeded", "arch": "{A}", "history": []}}\n')
    assert af.load_prior_lessons(str(p)) == {}         # a later win retires the lesson


def test_render_prior_lessons_cites_failures_and_rotates_diversity():
    base = {"family": "fidelity", "last_gate": "unfaithful",
            "last_detail": "inequality direction wrong",
            "gates_tried": ["formalize", "unfaithful"]}
    note0 = af.render_prior_lessons({**base, "prior_ticks": 3})   # 3 % 3 == 0
    note1 = af.render_prior_lessons({**base, "prior_ticks": 1})   # 1 % 3 == 1
    for note in (note0, note1):
        assert "fidelity" in note
        assert "unfaithful" in note
        assert "inequality direction wrong" in note
    assert note0 != note1                              # the diversity nudge rotated


def test_intent_messages_carry_prior_lessons():
    user = af.intent_messages(_ISSUE, "SIGS", prior_lessons="PRIOR-NOTE")[1]["content"]
    assert "PRIOR-NOTE" in user


def test_intent_messages_no_prior_lessons_by_default():
    assert "PRIOR-NOTE" not in af.intent_messages(_ISSUE, "SIGS")[1]["content"]


def test_draft_intent_threads_prior_lessons_to_chat():
    seen = {}

    def chat(msgs):
        seen["user"] = msgs[1]["content"]
        return ('{"statement": "s", "module_name": "M", "benchmark_id": "b"}', 7)
    af.draft_intent(_ISSUE, "", chat_fn=chat, prior_lessons="LESSON-Z")
    assert "LESSON-Z" in seen["user"]


def test_refill_injects_prior_lessons_into_the_intent_prompt(monkeypatch, tmp_path):
    # the real draft_intent runs (not the _two_stage_ok stand-in) so the cross-tick
    # lesson threads intent → intent_messages → the drafter chat_fn.
    monkeypatch.setattr(af, "formalize_with_repair", _good_formalize)
    _pass_gates(monkeypatch)
    seen = {}

    def intent_fn(msgs):
        seen["user"] = msgs[1]["content"]
        return ('{"statement": "s", "module_name": "M", "benchmark_id": "b"}', 5)

    issue = _issue(7, prior_lessons={
        "family": "fidelity", "last_gate": "unfaithful",
        "last_detail": "inequality direction wrong",
        "gates_tried": ["formalize", "unfaithful"], "prior_ticks": 1})
    res = af.refill([issue], reason_fn=intent_fn, intent_fn=intent_fn, prove_fn=_NOOP,
                    check_fn=_ELAB_OK, context_fn=lambda i: "", queue_dir=str(tmp_path),
                    budget=100000)
    assert [s["issue"] for s in res["seeded"]] == [7]
    assert "PRIOR ATTEMPTS" in seen["user"]
    assert "fidelity" in seen["user"]


def test_unknown_identifiers_match_live_elaborator_format():
    # the VERBATIM error from the 2026-07-17 live run — capital U + backticks.
    # the old regex (lowercase + straight quotes) never matched it, so the
    # retrieval hook silently never fired in production.
    live = "line 28:36: Unknown identifier `MathFin.vanillaPayoff`"
    legacy = "unknown constant 'Foo.bar'"
    assert af._unknown_identifiers([live, legacy]) == ["MathFin.vanillaPayoff", "Foo.bar"]


def test_formalize_with_repair_returns_collected_unknowns():
    checks = iter([{"errors": ["Unknown identifier `MathFin.vanillaPayoff`"], "sorry_count": 1},
                   {"errors": [], "sorry_count": 1}])
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5),
                                 chat_fn=_script_chat([_formalize_reply(), _formalize_reply()]),
                                 check_fn=lambda c: next(checks),
                                 emit_fn=af.emit_target_files, rounds=2)
    assert r["ok"] is True
    assert r["unknowns"] == ["MathFin.vanillaPayoff"]


# --- definitions path (F1): prompts, def extraction, defs gates ---------------

_DEFS_STUB = ("noncomputable def knockInPayoff (hit f : ℝ) : ℝ := hit * f\n\n"
              "noncomputable def knockOutPayoff (hit f : ℝ) : ℝ := (1 - hit) * f\n\n"
              "example : knockInPayoff 1 5 = 5 := by norm_num [knockInPayoff]\n\n"
              "example : knockOutPayoff 1 5 = 0 := by norm_num [knockOutPayoff]\n\n"
              "theorem in_out_parity (hit f : ℝ) :\n"
              "    knockInPayoff hit f + knockOutPayoff hit f = f * 1 := by sorry")


def test_intent_messages_defs_route_addendum_and_prior_unknowns():
    msgs = af.intent_messages(_ISSUE, "SIGS", route="defs",
                              prior_unknowns=["MathFin.vanillaPayoff"])
    user = msgs[1]["content"]
    assert "definitions" in user                       # the JSON array contract
    assert "MathFin.vanillaPayoff" in user             # prior guesses become hints
    assert "SIGS" in user


def test_intent_messages_theorem_route_has_no_defs_addendum():
    user = af.intent_messages(_ISSUE, "SIGS")[1]["content"]
    assert "NEW definitions" not in user


def test_formalize_messages_defs_block_when_intent_names_definitions():
    intent = {"statement": "s", "objects": [],
              "definitions": [{"name": "knockInPayoff", "signature": "ℝ → ℝ → ℝ",
                               "meaning": "in-part", "built_from": ["max"]}]}
    user = af.formalize_messages(intent, "")[1]["content"]
    assert "knockInPayoff" in user
    assert "definitions" in user.lower()
    assert "sorry" in user                             # only the THEOREM carries sorry


def test_drafted_def_names_extracts_defs_not_theorem():
    assert af.drafted_def_names(_DEFS_STUB) == ["knockInPayoff", "knockOutPayoff"]
    assert af.drafted_def_names("theorem t : True := by sorry") == []


def test_defs_probe_checks_consumption_and_grounding():
    probe = af.defs_probe(_DEFS_STUB, "in_out_parity",
                          ["knockInPayoff", "knockOutPayoff"])
    assert _DEFS_STUB.rstrip() in probe                # elaborates the stub first
    assert "`MathFin.in_out_parity" in probe           # theorem looked up by name
    assert "`MathFin.knockInPayoff" in probe and "`MathFin.knockOutPayoff" in probe
    assert "getUsedConstants" in probe
    assert "bindingBody!" in probe                     # grounding peels lambda binders:
    #                                                    binder types (ℝ) must not count
    assert "defs-gate:" in probe                       # the reject marker
    assert "newdef_depth" in probe and "ungrounded" in probe


def test_defs_rejection_requires_defs_without_daemon():
    called = {"n": 0}

    def check(code):
        called["n"] += 1
        return {"errors": [], "sorry_count": 1}
    r = af.defs_rejection("theorem t : True := by sorry", "t", [], check_fn=check)
    assert r["failed"] is True and r["gate"] == "newdef_depth"
    assert called["n"] == 0                            # no daemon consulted


def test_defs_rejection_classifies_marker_errors():
    ungrounded = {"errors": ["defs-gate: ungrounded: MathFin.k is a free-floating wrapper"],
                  "sorry_count": 1}
    r = af.defs_rejection(_DEFS_STUB, "in_out_parity", ["knockInPayoff"],
                          check_fn=lambda c: ungrounded)
    assert r["failed"] is True and r["gate"] == "ungrounded"
    newdef = {"errors": ["defs-gate: newdef_depth: the theorem's type uses none"],
              "sorry_count": 1}
    r = af.defs_rejection(_DEFS_STUB, "in_out_parity", ["knockInPayoff"],
                          check_fn=lambda c: newdef)
    assert r["failed"] is True and r["gate"] == "newdef_depth"


def test_defs_rejection_fails_open_on_unmarked_daemon_error():
    infra = {"errors": ["daemon check did not complete: TimeoutError"], "sorry_count": 0}
    r = af.defs_rejection(_DEFS_STUB, "in_out_parity", ["knockInPayoff"],
                          check_fn=lambda c: infra)
    assert r["failed"] is False


def test_semantic_verdict_defs_route_swaps_depth_for_defs_gate(monkeypatch):
    order = []
    monkeypatch.setattr(af, "depth_rejection",
                        lambda *a, **k: order.append("depth") or {"shallow": True, "verdict": "x", "tokens": 0})
    monkeypatch.setattr(af, "defs_rejection",
                        lambda lt, nm, dn, **k: order.append("defs") or
                        {"failed": True, "gate": "ungrounded", "verdict": "w", "tokens": 0})
    fail, _tok = af.semantic_verdict(
        lean_text="lt", stub="s", name="t", intent={}, issue={"pointers": []},
        deferred=[], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
        gate_budget=100, route="defs", def_names=["k"])
    assert order == ["defs"]                           # pointer-depth gate not consulted
    assert fail == {"gate": "ungrounded", "detail": "w"}


def test_render_gate_feedback_covers_defs_gates():
    assert "drafted def" in af.render_gate_feedback("newdef_depth", "", None)
    assert "wrapper" in af.render_gate_feedback("ungrounded", "", None)


# --- instance-probe gate (item M): concrete-value examples per new def -------

_PROBE_DEF = ("noncomputable def upCapture {S : Type*} (up : Finset S) (p b : S → ℝ) : ℝ :=\n"
              "  (∑ s ∈ up, p s) / (∑ s ∈ up, b s)\n\n")
_PROBE_THM = ("theorem upCapture_scale_invariant {S : Type*} (up : Finset S) (p b : S → ℝ)\n"
              "    (c : ℝ) (h : (∑ s ∈ up, b s) ≠ 0) :\n"
              "    upCapture up (fun s => c * p s) b = c * upCapture up p b := by sorry")
_GOOD_PROBE = ("example : upCapture (Finset.univ : Finset (Fin 2)) ![1, 2] ![3, 4] = 3 / 7 := by\n"
               "  norm_num [upCapture, Fin.sum_univ_two]\n\n")


def test_instance_probe_rejects_missing_probe():
    r = af.instance_probe_rejection(_PROBE_DEF + _PROBE_THM, ["upCapture"])
    assert r["failed"] is True and r["gate"] == "instance_probe"
    assert "upCapture" in r["verdict"]


def test_instance_probe_rejects_fake_true_probe():
    # an example that never mentions the def evaluates nothing about it
    stub = _PROBE_DEF + "example : True := by trivial\n\n" + _PROBE_THM
    assert af.instance_probe_rejection(stub, ["upCapture"])["failed"] is True


def test_instance_probe_rejects_sorry_probe():
    stub = (_PROBE_DEF
            + "example : upCapture (Finset.univ : Finset (Fin 2)) ![1,2] ![3,4] = 3/7 := by sorry\n\n"
            + _PROBE_THM)
    assert af.instance_probe_rejection(stub, ["upCapture"])["failed"] is True


def test_instance_probe_rejects_tautological_probe():
    # RHS repeats the def application -> asserts no concrete value
    taut = "example : upCapture Finset.univ p b = upCapture Finset.univ p b := by norm_num\n\n"
    assert af.instance_probe_rejection(_PROBE_DEF + taut + _PROBE_THM, ["upCapture"])["failed"] is True


def test_instance_probe_passes_concrete_norm_num_probe():
    r = af.instance_probe_rejection(_PROBE_DEF + _GOOD_PROBE + _PROBE_THM, ["upCapture"])
    assert r["failed"] is False and r["gate"] is None


def test_instance_probe_requires_a_probe_for_every_def():
    two = (_PROBE_DEF
           + "noncomputable def downCapture {S : Type*} (dn : Finset S) (p b : S → ℝ) : ℝ :=\n"
             "  (∑ s ∈ dn, p s) / (∑ s ∈ dn, b s)\n\n"
           + _GOOD_PROBE            # probes upCapture only
           + _PROBE_THM)
    r = af.instance_probe_rejection(two, ["upCapture", "downCapture"])
    assert r["failed"] is True and "downCapture" in r["verdict"] and "upCapture" not in r["verdict"]


def test_instance_probe_no_defs_passes():
    # theorem route: no new defs -> the gate is a no-op
    assert af.instance_probe_rejection("theorem t : True := by sorry", [])["failed"] is False


def test_render_gate_feedback_covers_instance_probe():
    fb = af.render_gate_feedback("instance_probe", "no probe for upCapture", None)
    assert "example" in fb and ("norm_num" in fb or "decide" in fb)


def test_intent_defs_addendum_requires_instance_probes():
    assert "example" in af.INTENT_DEFS_ADDENDUM
    assert "norm_num" in af.INTENT_DEFS_ADDENDUM or "decide" in af.INTENT_DEFS_ADDENDUM


def test_semantic_verdict_defs_route_runs_instance_probe_after_defs(monkeypatch):
    order = []
    monkeypatch.setattr(af, "defs_rejection",
                        lambda lt, nm, dn, **k: order.append("defs") or
                        {"failed": False, "gate": None, "verdict": "", "tokens": 0})
    monkeypatch.setattr(af, "instance_probe_rejection",
                        lambda lt, dn: order.append("instance_probe") or
                        {"failed": True, "gate": "instance_probe", "verdict": "upCapture", "tokens": 0})
    monkeypatch.setattr(af, "triviality_rejection",
                        lambda *a, **k: order.append("triviality") or {"trivial": False, "verdict": "", "tokens": 0})
    fail, _tok = af.semantic_verdict(
        lean_text="lt", stub="s", name="t", intent={}, issue={"pointers": []},
        deferred=[], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
        gate_budget=100, route="defs", def_names=["upCapture"])
    assert order == ["defs", "instance_probe"]        # after defs, short-circuits before triviality
    assert fail == {"gate": "instance_probe", "detail": "upCapture"}


def test_emit_target_files_defs_meta_header_and_provenance():
    meta = {**_META, "definitions": ["knockInPayoff", "knockOutPayoff"]}
    lean_text, entry, placement = af.emit_target_files(_ISSUE, _DEFS_STUB, meta)
    assert "-- new-defs: knockInPayoff, knockOutPayoff" in lean_text
    assert entry["metadata"]["provenance"]["new_defs"] == ["knockInPayoff", "knockOutPayoff"]
    assert lean_text.count("sorry") == 1               # defs are sorry-free
    meta2 = bm.parse_meta(lean_text)                   # header block still parses
    assert meta2["benchmark_id"] == "mf-fi-fra"


def test_refill_defs_route_requires_intent_definitions(monkeypatch, tmp_path):
    # a defs-routed issue whose intent names NO definitions fails the intent gate
    # (repairable), never reaching formalize.
    calls = {"formalize": 0}
    monkeypatch.setattr(af, "draft_intent", _good_intent)      # definitions: absent

    def formalize(*a, **k):
        calls["formalize"] += 1
        return _good_formalize(*a, **k)
    monkeypatch.setattr(af, "formalize_with_repair", formalize)
    _pass_gates(monkeypatch)
    res = af.refill([_issue(5, route="defs")], reason_fn=_NOOP, prove_fn=_NOOP,
                    check_fn=_ELAB_OK, context_fn=lambda i: "", queue_dir=str(tmp_path),
                    budget=100000, semantic_rounds=1)
    assert res["seeded"] == []
    assert res["attempted"][0]["history"][0]["gate"] == "intent"
    assert calls["formalize"] == 0


def test_refill_defs_route_seeds_with_new_defs_header(monkeypatch, tmp_path):
    def intent(i, ctx, *, chat_fn, **_kw):
        base = _good_intent(i, ctx, chat_fn=chat_fn)
        base["intent"]["definitions"] = [{"name": "knockInPayoff"}]
        return base

    def formalize(intent_, g, *, issue, emit_fn, **kw):
        lean_text, entry, _ = emit_fn(issue, _DEFS_STUB,
                                      {"module_name": intent_["module_name"],
                                       "benchmark_id": intent_["benchmark_id"],
                                       "docstring": "d",
                                       "definitions": ["knockInPayoff", "knockOutPayoff"]})
        return {"ok": True, "stub": _DEFS_STUB, "meta": {}, "lean_text": lean_text,
                "entry": entry, "tokens": 10}
    monkeypatch.setattr(af, "draft_intent", intent)
    monkeypatch.setattr(af, "formalize_with_repair", formalize)
    _pass_gates(monkeypatch)
    monkeypatch.setattr(af, "defs_rejection",
                        lambda lt, nm, dn, **k: {"failed": False, "gate": None,
                                                 "verdict": "", "tokens": 0})
    res = af.refill([_issue(6, route="defs")], reason_fn=_NOOP, prove_fn=_NOOP,
                    check_fn=_ELAB_OK, context_fn=lambda i: "", queue_dir=str(tmp_path),
                    budget=100000)
    assert [s["issue"] for s in res["seeded"]] == [6]
    staged = (tmp_path / "cal-bk-6.lean").read_text()
    assert "-- new-defs:" in staged
    assert "theorem in_out_parity" in staged


def test_refill_history_rows_carry_unknowns_and_family(monkeypatch, tmp_path):
    def formalize(intent, g, **kw):
        stub = "theorem t5 (h : p) : q := by sorry"
        lean_text, entry, _ = kw["emit_fn"]({"number": 5, "area": "fixed-income",
                                             "pointers": []}, stub,
                                            {"module_name": "T5", "benchmark_id": "b"})
        return {"ok": True, "stub": stub, "meta": {}, "lean_text": lean_text,
                "entry": entry, "tokens": 10, "unknowns": ["MathFin.wanted"]}
    monkeypatch.setattr(af, "draft_intent", _good_intent)
    monkeypatch.setattr(af, "formalize_with_repair", formalize)
    _pass_gates(monkeypatch)
    monkeypatch.setattr(af, "depth_rejection",
                        lambda lt, nm, ptr, **k: {"shallow": True, "verdict": "v", "tokens": 0})
    res = af.refill([_issue(5, pointers=["MathFin/A.lean"])], reason_fn=_NOOP, prove_fn=_NOOP,
                    check_fn=_ELAB_OK, context_fn=lambda i: "", queue_dir=str(tmp_path),
                    budget=100000, semantic_rounds=1)
    rec = res["attempted"][0]
    assert rec["family"] == "needs_primitives"
    assert rec["history"][0]["unknown_identifiers"] == ["MathFin.wanted"]


# --- refill orchestrator (monkeypatched steps; control flow only) ------------

def _issue(n, **kw):
    d = {"number": n, "area": "fixed-income", "title": f"t{n}", "body": "b",
         "pointers": [], "difficulty": "small"}
    d.update(kw)
    return d


def _good_intent(i, ctx, *, chat_fn, feedback=None, **_kw):
    """Stand-in for draft_intent — a parseable intent with naming meta."""
    n = i["number"]
    return {"ok": True, "tokens": 5, "intent": {
        "statement": f"stmt {n}", "objects": [], "module_name": f"T{n}",
        "benchmark_id": f"mf-fi-t{n}", "docstring": "d", "deferred": []}}


def _good_formalize(intent, grounding, *, issue, chat_fn, check_fn, emit_fn, rounds,
                    retrieve_fn=None, token_budget=None, proactive_premises=None,
                    revision_note="", log=None, **_kw):
    """Stand-in for formalize_with_repair — emits a real lean_text/entry from the intent
    meta. `**_kw` absorbs future kwargs (the derivable_fn lesson, twice now)."""
    n = issue["number"]
    stub = f"theorem t{n} (h : p) : q := by sorry"
    meta = {"module_name": intent["module_name"], "benchmark_id": intent["benchmark_id"],
            "docstring": "d", "deferred": []}
    lean_text, entry, _ = emit_fn(issue, stub, meta)
    return {"ok": True, "stub": stub, "meta": meta, "lean_text": lean_text,
            "entry": entry, "tokens": 10}


def _two_stage_ok(monkeypatch):
    monkeypatch.setattr(af, "draft_intent", _good_intent)
    monkeypatch.setattr(af, "formalize_with_repair", _good_formalize)


def _pass_gates(monkeypatch):
    monkeypatch.setattr(af, "depth_rejection", lambda lt, nm, ptr, **k: {"shallow": False, "tokens": 0})
    monkeypatch.setattr(af, "hypothesis_rejection", lambda *a, **k: {"vacuous": False, "tokens": 1})
    monkeypatch.setattr(af, "disproof", lambda *a, **k: {"false": False, "tokens": 1})
    monkeypatch.setattr(af, "judge_faithfulness",
                        lambda i, s, chat_fn, deferred=None: {"faithful": True, "tokens": 1})
    monkeypatch.setattr(af, "intent_fidelity_check",
                        lambda intent, s, *, reason_fn: {"faithful": True, "tokens": 1})


_NOOP = lambda m: ("", 0)
_ELAB_OK = lambda c: {"success": True, "sorry_count": 1, "errors": []}


def test_refill_stages_a_good_issue(monkeypatch, tmp_path):
    _two_stage_ok(monkeypatch)
    _pass_gates(monkeypatch)
    res = af.refill([_issue(5)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert [s["issue"] for s in res["seeded"]] == [5]
    assert (tmp_path / "cal-bk-5.lean").exists()
    assert (tmp_path / "cal-bk-5.entry.json").exists()


def test_refill_skips_when_intent_unparseable(monkeypatch, tmp_path):
    # stage 1 (magistral) fails to produce a parseable intent → skip before formalizing.
    monkeypatch.setattr(af, "draft_intent",
                        lambda i, ctx, *, chat_fn, feedback=None, **_kw: {"ok": False, "intent": None, "tokens": 3})
    _pass_gates(monkeypatch)
    res = af.refill([_issue(5)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert res["seeded"] == []


def test_refill_skips_when_formalization_fails(monkeypatch, tmp_path):
    # stage 2 (leanstral) cannot produce an elaborating statement → skip.
    monkeypatch.setattr(af, "draft_intent", _good_intent)
    monkeypatch.setattr(af, "formalize_with_repair",
                        lambda *a, **k: {"ok": False, "stub": None, "meta": None,
                                         "lean_text": None, "entry": None, "tokens": 20})
    _pass_gates(monkeypatch)
    res = af.refill([_issue(5)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert res["seeded"] == []
    assert not (tmp_path / "cal-bk-5.lean").exists()


def test_refill_skips_shallow_statement(monkeypatch, tmp_path):
    # a true-but-shallow draft (consumes no pointer-module def) is rejected by the
    # depth gate before the expensive prover gates — never staged.
    _two_stage_ok(monkeypatch)
    _pass_gates(monkeypatch)
    monkeypatch.setattr(af, "depth_rejection",
                        lambda lt, nm, ptr, **k: {"shallow": True, "verdict": "no domain def", "tokens": 0})
    res = af.refill([_issue(5)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert res["seeded"] == []
    assert not (tmp_path / "cal-bk-5.lean").exists()


def test_refill_depth_gate_can_be_disabled(monkeypatch, tmp_path):
    # depth_gate=False bypasses the gate entirely — it is not even invoked.
    _two_stage_ok(monkeypatch)
    _pass_gates(monkeypatch)
    calls = {"n": 0}

    def dep(*a, **k):
        calls["n"] += 1
        return {"shallow": True, "verdict": "would reject", "tokens": 0}
    monkeypatch.setattr(af, "depth_rejection", dep)
    res = af.refill([_issue(5)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000, depth_gate=False)
    assert [s["issue"] for s in res["seeded"]] == [5]
    assert calls["n"] == 0


def test_refill_skips_vacuous_then_stages_next(monkeypatch, tmp_path):
    _two_stage_ok(monkeypatch)
    monkeypatch.setattr(af, "depth_rejection", lambda lt, nm, ptr, **k: {"shallow": False, "tokens": 0})
    monkeypatch.setattr(af, "disproof", lambda *a, **k: {"false": False, "tokens": 1})
    monkeypatch.setattr(af, "judge_faithfulness",
                        lambda i, s, chat_fn, deferred=None: {"faithful": True, "tokens": 1})
    monkeypatch.setattr(af, "intent_fidelity_check", lambda intent, s, *, reason_fn: {"faithful": True, "tokens": 1})
    # issue 1's stub is vacuous, issue 2's is not
    monkeypatch.setattr(af, "hypothesis_rejection",
                        lambda lt, nm, **k: {"vacuous": "theorem t1 " in lt, "tokens": 1})
    res = af.refill([_issue(1), _issue(2)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert [s["issue"] for s in res["seeded"]] == [2]
    assert not (tmp_path / "cal-bk-1.lean").exists()
    assert (tmp_path / "cal-bk-2.lean").exists()


def test_refill_skips_unfaithful_judge(monkeypatch, tmp_path):
    _two_stage_ok(monkeypatch)
    monkeypatch.setattr(af, "depth_rejection", lambda lt, nm, ptr, **k: {"shallow": False, "tokens": 0})
    monkeypatch.setattr(af, "hypothesis_rejection", lambda *a, **k: {"vacuous": False, "tokens": 1})
    monkeypatch.setattr(af, "disproof", lambda *a, **k: {"false": False, "tokens": 1})
    monkeypatch.setattr(af, "judge_faithfulness",
                        lambda i, s, chat_fn, deferred=None: {"faithful": False, "verdict": "weaker", "tokens": 1})
    monkeypatch.setattr(af, "intent_fidelity_check", lambda intent, s, *, reason_fn: {"faithful": True, "tokens": 1})
    res = af.refill([_issue(7)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert res["seeded"] == []
    assert not (tmp_path / "cal-bk-7.lean").exists()


def test_refill_skips_intent_drift(monkeypatch, tmp_path):
    # the folded roundtrip: leanstral's Lean does not render magistral's intent → skip.
    _two_stage_ok(monkeypatch)
    monkeypatch.setattr(af, "depth_rejection", lambda lt, nm, ptr, **k: {"shallow": False, "tokens": 0})
    monkeypatch.setattr(af, "hypothesis_rejection", lambda *a, **k: {"vacuous": False, "tokens": 1})
    monkeypatch.setattr(af, "disproof", lambda *a, **k: {"false": False, "tokens": 1})
    monkeypatch.setattr(af, "judge_faithfulness", lambda i, s, chat_fn, deferred=None: {"faithful": True, "tokens": 1})
    monkeypatch.setattr(af, "intent_fidelity_check",
                        lambda intent, s, *, reason_fn: {"faithful": False, "verdict": "dropped hyp", "tokens": 1})
    res = af.refill([_issue(7)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert res["seeded"] == []


def test_refill_skips_issue_on_step_exception(monkeypatch, tmp_path):
    # a transient error (e.g. HTTP 429 exhaustion) on one issue must not crash the
    # whole refill — log it and skip to the next issue.
    def boom(i, ctx, *, chat_fn, feedback=None, **_kw):
        if i["number"] == 1:
            raise RuntimeError("HTTP 429 from Mistral API")
        return _good_intent(i, ctx, chat_fn=chat_fn)
    monkeypatch.setattr(af, "draft_intent", boom)
    monkeypatch.setattr(af, "formalize_with_repair", _good_formalize)
    _pass_gates(monkeypatch)
    res = af.refill([_issue(1), _issue(2)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert [s["issue"] for s in res["seeded"]] == [2]
    assert (tmp_path / "cal-bk-2.lean").exists()


def test_refill_wires_intent_formalize_prove_fns(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(
        af, "draft_intent",
        lambda i, ctx, *, chat_fn, feedback=None, **_kw:
        seen.update(intent=chat_fn) or _good_intent(i, ctx, chat_fn=chat_fn))
    monkeypatch.setattr(
        af, "formalize_with_repair",
        lambda intent, g, *, issue, chat_fn, check_fn, emit_fn, rounds, retrieve_fn=None,
        token_budget=None, proactive_premises=None, revision_note="", log=None, **_kw:
        seen.update(formalize=chat_fn) or _good_formalize(intent, g, issue=issue, chat_fn=chat_fn,
                                                          check_fn=check_fn, emit_fn=emit_fn, rounds=rounds))
    monkeypatch.setattr(af, "depth_rejection", lambda lt, nm, ptr, **k: {"shallow": False, "tokens": 0})
    monkeypatch.setattr(af, "hypothesis_rejection",
                        lambda lt, nm, **k: seen.update(gate=k["chat_fn"]) or {"vacuous": False, "tokens": 1})
    monkeypatch.setattr(af, "disproof", lambda *a, **k: {"false": False, "tokens": 1})
    monkeypatch.setattr(af, "judge_faithfulness",
                        lambda i, s, chat_fn, deferred=None: seen.update(judge=chat_fn) or {"faithful": True, "tokens": 1})
    monkeypatch.setattr(af, "intent_fidelity_check", lambda intent, s, *, reason_fn: {"faithful": True, "tokens": 1})
    R = lambda m: ("R", 0)
    P = lambda m: ("P", 0)
    F = lambda m: ("F", 0)
    af.refill([_issue(9)], reason_fn=R, prove_fn=P, formalize_fn=F, check_fn=_ELAB_OK,
              context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert seen["intent"] is R and seen["judge"] is R    # magistral: intent + judge
    assert seen["formalize"] is F                        # leanstral: formalize
    assert seen["gate"] is P                             # leanstral: kernel gates


# --- semantic repair cascade (bounded feedback re-draft loop) -----------------

def test_semantic_verdict_cheapest_first_short_circuits(monkeypatch):
    # structural gates run before any prover-token gate; the first failure stops
    # the battery.
    order = []
    monkeypatch.setattr(af, "depth_rejection",
                        lambda *a, **k: order.append("depth") or {"shallow": False, "tokens": 0})
    monkeypatch.setattr(af, "triviality_rejection",
                        lambda *a, **k: order.append("triv") or {"trivial": True,
                                                                 "verdict": "rfl", "tokens": 0})
    called = {"vac": False}
    monkeypatch.setattr(af, "hypothesis_rejection",
                        lambda *a, **k: called.update(vac=True) or {"vacuous": False, "tokens": 1})
    fail, tokens = af.semantic_verdict(
        lean_text="lt", stub="s", name="n", intent={}, issue={"pointers": ["MathFin/A.lean"]},
        deferred=[], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK, gate_budget=1000)
    assert fail == {"gate": "trivial", "detail": "rfl"}
    assert order == ["depth", "triv"]
    assert called["vac"] is False
    assert tokens == 0


def test_refill_repairs_shallow_draft_with_feedback(monkeypatch, tmp_path):
    # attempt 1 is depth-rejected; the re-draft (attempt 2) must carry the depth
    # feedback into BOTH stages, then seed. This is the observed #53/#66 failure.
    seen = {"intent_feedback": [], "revision_notes": []}

    def intent(i, ctx, *, chat_fn, feedback=None, **_kw):
        seen["intent_feedback"].append(feedback)
        return _good_intent(i, ctx, chat_fn=chat_fn)

    def formalize(intent_, g, *, issue, chat_fn, check_fn, emit_fn, rounds,
                  retrieve_fn=None, token_budget=None, proactive_premises=None,
                  revision_note="", log=None, **_kw):
        seen["revision_notes"].append(revision_note)
        return _good_formalize(intent_, g, issue=issue, chat_fn=chat_fn,
                               check_fn=check_fn, emit_fn=emit_fn, rounds=rounds)

    monkeypatch.setattr(af, "draft_intent", intent)
    monkeypatch.setattr(af, "formalize_with_repair", formalize)
    _pass_gates(monkeypatch)
    calls = {"n": 0}

    def dep(lt, nm, ptr, **k):
        calls["n"] += 1
        return {"shallow": calls["n"] == 1, "verdict": "no pointer def", "tokens": 0}
    monkeypatch.setattr(af, "depth_rejection", dep)
    res = af.refill([_issue(5, pointers=["MathFin/FixedIncome/ZCB.lean"])],
                    reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert [s["issue"] for s in res["seeded"]] == [5]
    assert seen["intent_feedback"][0] is None                       # round 1: fresh
    assert "depth" in seen["intent_feedback"][1]                    # round 2: the verdict
    assert "EXPRESSED THROUGH" in seen["intent_feedback"][1]        # the repair direction
    assert "theorem t5" in seen["intent_feedback"][1]               # rejected stub included
    assert seen["revision_notes"][0] == ""                          # round 1: no note
    assert seen["revision_notes"][1] == seen["intent_feedback"][1]  # both stages see it


def test_refill_repairs_trivial_draft_with_feedback(monkeypatch, tmp_path):
    _two_stage_ok(monkeypatch)
    _pass_gates(monkeypatch)
    calls = {"n": 0}

    def triv(lt, *, check_fn):
        calls["n"] += 1
        return {"trivial": calls["n"] == 1, "verdict": "closed by rfl", "tokens": 0}
    monkeypatch.setattr(af, "triviality_rejection", triv)
    res = af.refill([_issue(6)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert [s["issue"] for s in res["seeded"]] == [6]
    assert calls["n"] == 2


def test_refill_exhausts_semantic_rounds_and_records_obstruction(monkeypatch, tmp_path):
    _two_stage_ok(monkeypatch)
    _pass_gates(monkeypatch)
    monkeypatch.setattr(af, "depth_rejection",
                        lambda lt, nm, ptr, **k: {"shallow": True, "verdict": "v", "tokens": 0})
    res = af.refill([_issue(5, pointers=["MathFin/A.lean"])], reason_fn=_NOOP, prove_fn=_NOOP,
                    check_fn=_ELAB_OK, context_fn=lambda i: "", queue_dir=str(tmp_path),
                    budget=100000, semantic_rounds=2)
    assert res["seeded"] == []
    rec = res["attempted"][0]
    assert rec["issue"] == 5 and rec["outcome"] == "depth" and rec["attempts"] == 2
    assert [h["gate"] for h in rec["history"]] == ["depth", "depth"]


def test_refill_semantic_rounds_one_is_single_shot(monkeypatch, tmp_path):
    calls = {"n": 0}

    def intent(i, ctx, *, chat_fn, feedback=None, **_kw):
        calls["n"] += 1
        return _good_intent(i, ctx, chat_fn=chat_fn)
    monkeypatch.setattr(af, "draft_intent", intent)
    monkeypatch.setattr(af, "formalize_with_repair", _good_formalize)
    _pass_gates(monkeypatch)
    monkeypatch.setattr(af, "depth_rejection",
                        lambda lt, nm, ptr, **k: {"shallow": True, "verdict": "v", "tokens": 0})
    res = af.refill([_issue(5, pointers=["MathFin/A.lean"])], reason_fn=_NOOP, prove_fn=_NOOP,
                    check_fn=_ELAB_OK, context_fn=lambda i: "", queue_dir=str(tmp_path),
                    budget=100000, semantic_rounds=1)
    assert res["seeded"] == []
    assert calls["n"] == 1                              # the old single-shot behavior


def test_refill_attempted_records_success(monkeypatch, tmp_path):
    _two_stage_ok(monkeypatch)
    _pass_gates(monkeypatch)
    res = af.refill([_issue(5)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    rec = res["attempted"][0]
    assert rec["issue"] == 5 and rec["outcome"] == "seeded" and rec["attempts"] == 1
    assert rec["history"] == []


def test_refill_budget_stops_semantic_loop(monkeypatch, tmp_path):
    # intent (5 tok) + formalize (10 tok) exceed budget=8 after attempt 1 → the
    # loop must stop instead of re-drafting, and say so in the history.
    calls = {"n": 0}

    def intent(i, ctx, *, chat_fn, feedback=None, **_kw):
        calls["n"] += 1
        return _good_intent(i, ctx, chat_fn=chat_fn)
    monkeypatch.setattr(af, "draft_intent", intent)
    monkeypatch.setattr(af, "formalize_with_repair", _good_formalize)
    _pass_gates(monkeypatch)
    monkeypatch.setattr(af, "depth_rejection",
                        lambda lt, nm, ptr, **k: {"shallow": True, "verdict": "v", "tokens": 0})
    res = af.refill([_issue(5, pointers=["MathFin/A.lean"])], reason_fn=_NOOP, prove_fn=_NOOP,
                    check_fn=_ELAB_OK, context_fn=lambda i: "", queue_dir=str(tmp_path),
                    budget=8, semantic_rounds=3)
    assert calls["n"] == 1
    assert res["attempted"][0]["history"][-1]["gate"] == "budget"


def test_refill_records_error_outcome(monkeypatch, tmp_path):
    def boom(i, ctx, *, chat_fn, feedback=None, **_kw):
        raise RuntimeError("HTTP 429 from Mistral API")
    monkeypatch.setattr(af, "draft_intent", boom)
    res = af.refill([_issue(1)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert res["seeded"] == []
    assert res["attempted"][0]["outcome"] == "error"


# --- two-stage draft components: intent (magistral) + formalize (leanstral) ---

def test_parse_intent_extracts_statement_and_meta():
    reply = ('reasoning...\n```json\n{"statement": "For a ZCB B ...", "objects": ["MathFin.zcb"], '
             '"module_name": "FRA", "benchmark_id": "mf-fi-fra", "docstring": "d", "deferred": []}\n```')
    it = af.parse_intent(reply)
    assert it["statement"].startswith("For a ZCB")
    assert it["objects"] == ["MathFin.zcb"]
    assert it["module_name"] == "FRA" and it["benchmark_id"] == "mf-fi-fra"


def test_parse_intent_none_when_missing_required():
    assert af.parse_intent('```json\n{"statement": "x"}\n```') is None    # no module/benchmark
    assert af.parse_intent("no json at all") is None


def test_intent_messages_carry_issue_and_context():
    msgs = af.intent_messages({"number": 1, "title": "FRA", "body": "F = ...", "pointers": []}, "SIGPACK")
    joined = " ".join(m["content"] for m in msgs)
    assert "FRA" in joined and "SIGPACK" in joined
    assert any(m["role"] == "system" for m in msgs)


def test_draft_intent_ok_and_tokens():
    reply = '```json\n{"statement":"S","objects":[],"module_name":"M","benchmark_id":"mf-x","docstring":"d"}\n```'
    r = af.draft_intent(_issue(3), "", chat_fn=_script_chat([reply]))
    assert r["ok"] is True and r["intent"]["module_name"] == "M" and r["tokens"] == 10


def test_draft_intent_not_ok_when_unparseable():
    r = af.draft_intent(_issue(3), "", chat_fn=_script_chat(["sorry, no json"]))
    assert r["ok"] is False and r["intent"] is None


_INTENT = {"statement": "P1 = P2*(1+d)", "objects": ["MathFin.zcb"], "module_name": "FRA",
           "benchmark_id": "mf-fi-fra", "docstring": "d", "deferred": []}


def _formalize_reply(concl="x = x", name="foo"):
    return f"```lean\ntheorem {name} (x : ℝ) : {concl} := by sorry\n```"


def test_formalize_messages_carry_intent_objects_and_grounding():
    msgs = af.formalize_messages(_INTENT, "SIGS")
    joined = " ".join(m["content"] for m in msgs)
    assert "P1 = P2*(1+d)" in joined and "MathFin.zcb" in joined and "SIGS" in joined
    assert ":= by sorry" in joined


def test_formalize_with_repair_succeeds_first_round():
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5), chat_fn=_script_chat([_formalize_reply()]),
                                 check_fn=_ELAB_OK, emit_fn=af.emit_target_files, rounds=3)
    assert r["ok"] is True and "theorem foo" in r["lean_text"] and r["tokens"] == 10


def test_formalize_with_repair_repairs_on_elaboration_error():
    replies = [_formalize_reply(concl="x ²"), _formalize_reply(concl="x = x")]
    checks = iter([{"success": False, "errors": ["unexpected token '²'"], "sorry_count": 1},
                   {"success": True, "errors": [], "sorry_count": 1}])
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5), chat_fn=_script_chat(replies),
                                 check_fn=lambda c: next(checks), emit_fn=af.emit_target_files, rounds=3)
    assert r["ok"] is True and "x = x" in r["lean_text"]


def test_formalize_with_repair_repairs_lint_violations():
    # elaborates on round 1 but lint-dirty (snake_case def, missing docstring) —
    # the loop must feed the lint report back and accept the clean round 2
    dirty = ("```lean\ndef par_swap_rate (x : ℝ) : ℝ := x\n"
             "theorem foo (x : ℝ) : x = x := by sorry\n```")
    clean = ("```lean\n/-- Par rate. -/\ndef parSwapRate (x : ℝ) : ℝ := x\n"
             "theorem foo (x : ℝ) : x = x := by sorry\n```")
    seen = []

    def chat(msgs):
        seen.append(msgs)
        return ([dirty, clean][len(seen) - 1], 10)
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5), chat_fn=chat,
                                 check_fn=_ELAB_OK, emit_fn=af.emit_target_files, rounds=3)
    assert r["ok"] is True and "parSwapRate" in r["lean_text"]
    fb = " ".join(m["content"] for m in seen[1])
    assert "lake lint" in fb and "lowerCamelCase" in fb and "parSwapRate" in fb


def test_formalize_with_repair_gives_up_on_persistent_lint_dirt():
    dirty = ("```lean\ndef par_swap_rate (x : ℝ) : ℝ := x\n"
             "theorem foo (x : ℝ) : x = x := by sorry\n```")
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5),
                                 chat_fn=_script_chat([dirty, dirty]),
                                 check_fn=_ELAB_OK, emit_fn=af.emit_target_files, rounds=2)
    assert r["ok"] is False


def test_formalize_contract_demands_lint_cleanliness():
    # the drafter is TOLD the lint bar (right-first-time), the gate enforces it
    assert "docstring" in af.FORMALIZE_SYSTEM and "lowerCamelCase" in af.FORMALIZE_SYSTEM
    intent = {**_INTENT, "definitions": [{"name": "annuity", "signature": "ℝ", "meaning": "m"}]}
    joined = " ".join(m["content"] for m in af.formalize_messages(intent, ""))
    assert "/--" in joined and "lowerCamelCase" in joined


def test_formalize_with_repair_injects_loogle_on_unknown_identifier():
    replies = [_formalize_reply(concl="Foo.bar x"), _formalize_reply(concl="x = x")]
    seen = []

    def chat(msgs):
        seen.append(msgs)
        return (replies[len(seen) - 1], 10)
    checks = iter([{"success": False, "errors": ["unknown identifier 'Foo.bar'"], "sorry_count": 1},
                   {"success": True, "errors": [], "sorry_count": 1}])
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5), chat_fn=chat,
                                 check_fn=lambda c: next(checks), emit_fn=af.emit_target_files,
                                 rounds=3, retrieve_fn=lambda nm: f"CANDIDATES:{nm}=real.bar")
    assert r["ok"] is True
    assert "CANDIDATES:Foo.bar" in " ".join(m["content"] for m in seen[1])   # loogle fed back


def test_assistant_turn_substitutes_placeholder_for_empty():
    assert af._assistant("hi")["content"] == "hi"
    assert af._assistant("")["content"] == "(no output)"       # Mistral 400s on empty content
    assert af._assistant(None)["content"] == "(no output)"


# --- strengthen: post-proof unused-hypothesis stripping (option b) -----------
# 2/2 production PRs shipped an unused hypothesis (#123 hTn, #124 hσ_eq).
# Dropping one can only STRENGTHEN the theorem, so the transform is
# faithfulness-safe by construction; the full gate re-runs before acceptance.

_STRONG_MOD = (
    "module\n\npublic import Mathlib\n\nset_option autoImplicit false\n\n"
    "@[expose] public section\n\nnamespace MathFin\n\n"
    "/-- Std-dev premium. -/\ndef stdDevPremium (β μ σ : ℝ) : ℝ := μ + β * σ\n\n"
    "theorem premium_ge_mean (μ σ : ℝ) (hμ : 0 ≤ μ) (hσ : 0 ≤ σ) "
    "(hσ_eq : σ = Real.sqrt σ) : stdDevPremium 1 μ σ ≥ μ := by\n"
    "  dsimp [stdDevPremium]; nlinarith\n\nend MathFin\n"
)
_STRONG_SNIP = (
    "import MathFin.Actuarial.ActuarialInsurance\n\nopen MathFin\n\n"
    "/-- d. -/\n"
    "theorem mf_ins (μ σ : ℝ) (hμ : 0 ≤ μ) (hσ : 0 ≤ σ) "
    "(hσ_eq : σ = Real.sqrt σ) : stdDevPremium 1 μ σ ≥ μ :=\n"
    "  MathFin.premium_ge_mean μ σ hμ hσ hσ_eq\n"
)
_UNUSED_WARN = ["line 12:34: unused variable `hσ_eq` [linter.unusedVariables]"]


def test_remove_explicit_binders_drops_single_name_group():
    out, dropped = af.remove_explicit_binders(
        "(μ σ : ℝ) (hμ : 0 ≤ μ) (hσ_eq : σ = Real.sqrt σ)", {"hσ_eq"})
    assert dropped == ["hσ_eq"] and "hσ_eq" not in out and "(hμ : 0 ≤ μ)" in out


def test_remove_explicit_binders_multi_name_group_keeps_the_rest():
    out, dropped = af.remove_explicit_binders("(a b : ℝ) (h : 0 ≤ a)", {"b"})
    assert dropped == ["b"] and "(a : ℝ)" in out and "b" not in out.split(":")[0]


def test_remove_explicit_binders_never_touches_implicit_groups():
    out, dropped = af.remove_explicit_binders("{ι : Type*} (h : 0 ≤ 1)", {"ι"})
    assert dropped == [] and "{ι : Type*}" in out


def test_unused_theorem_hypotheses_filters_to_explicit_binders():
    binders = "(μ : ℝ) (hσ_eq : μ = μ)"
    warns = _UNUSED_WARN + ["unused variable `hlocal`", "unused variable `_hμ`"]
    assert af.unused_theorem_hypotheses(warns, binders) == ["hσ_eq"]


def test_strengthen_candidate_strips_regates_and_rebuilds_snippet():
    regates = []

    def regate(cand):
        regates.append(cand)
        return {"passed": True, "reason": "ok", "warnings": []}
    s = af.strengthen_candidate(_STRONG_MOD, _STRONG_SNIP, "premium_ge_mean",
                                _UNUSED_WARN, regate_fn=regate)
    assert s["stripped"] == ["hσ_eq"]
    assert "hσ_eq" not in s["candidate"] and "(hσ : 0 ≤ σ)" in s["candidate"]
    assert len(regates) == 1 and "hσ_eq" not in regates[0]
    assert s["entry_code"] is not None and "hσ_eq" not in s["entry_code"]
    assert "MathFin.premium_ge_mean μ σ hμ hσ" in s["entry_code"]


def test_strengthen_candidate_fails_open_when_regate_rejects():
    s = af.strengthen_candidate(_STRONG_MOD, _STRONG_SNIP, "premium_ge_mean",
                                _UNUSED_WARN,
                                regate_fn=lambda c: {"passed": False, "reason": "axiom_dirty"})
    assert s["stripped"] == [] and s["candidate"] == _STRONG_MOD and s["entry_code"] is None


def test_strengthen_candidate_reverts_when_snippet_cannot_rebuild():
    # module and snippet must stay coherent — an unrebuildable snippet reverts
    # the whole strip (else open-pr regen would block on a mismatched re-export)
    s = af.strengthen_candidate(_STRONG_MOD, "not a lean snippet", "premium_ge_mean",
                                _UNUSED_WARN,
                                regate_fn=lambda c: {"passed": True, "warnings": []})
    assert s["stripped"] == [] and s["candidate"] == _STRONG_MOD


def test_strengthen_candidate_noop_without_relevant_warnings():
    regates = []
    s = af.strengthen_candidate(_STRONG_MOD, _STRONG_SNIP, "premium_ge_mean",
                                ["unused variable `hproof_local`"],
                                regate_fn=lambda c: regates.append(c))
    assert s["stripped"] == [] and s["candidate"] == _STRONG_MOD and regates == []


def test_trim_unused_imports_drops_only_unneeded_mathfin_imports():
    cand = ("module\n\npublic import Mathlib\npublic import MathFin.FixedIncome.ZCB\n"
            "public import MathFin.Futures.Black76\n\ntheorem t : True := trivial\n")

    def check(code):
        # ZCB is load-bearing: without it elaboration breaks; Black76 is decorative
        if "MathFin.FixedIncome.ZCB" not in code:
            return {"success": False, "errors": ["unknown constant 'MathFin.zcb'"],
                    "sorry_count": 0}
        return {"success": True, "errors": [], "sorry_count": 0}
    r = af.trim_unused_imports(cand, check_fn=check)
    assert r["removed"] == ["MathFin.Futures.Black76"]
    assert "Black76" not in r["candidate"]
    assert "public import MathFin.FixedIncome.ZCB" in r["candidate"]
    assert "public import Mathlib" in r["candidate"]


def test_trim_unused_imports_noop_without_mathfin_imports():
    cand = "public import Mathlib\n\ntheorem t : True := trivial\n"
    calls = []
    r = af.trim_unused_imports(cand, check_fn=lambda c: calls.append(c))
    assert r["candidate"] == cand and r["removed"] == [] and calls == []


def test_formalize_contract_demands_natural_generality():
    s = af.FORMALIZE_SYSTEM
    assert "generality" in s and "Nonempty" in s and "↦" in s


def test_strengthen_candidate_cascades_on_fresh_warnings():
    # dropping hσ_eq may leave another binder newly unused — the re-gate's own
    # warnings drive the next pass, bounded by max_passes
    seq = iter([{"passed": True, "warnings": ["unused variable `hσ`"]},
                {"passed": True, "warnings": []}])
    s = af.strengthen_candidate(_STRONG_MOD, _STRONG_SNIP, "premium_ge_mean",
                                _UNUSED_WARN, regate_fn=lambda c: next(seq))
    assert s["stripped"] == ["hσ_eq", "hσ"]
    assert "hσ" not in s["candidate"].split(":= by")[0].split("theorem")[1]


# --- derivable-hypothesis probe (the #123 hP class) ---------------------------

_DER_MOD = ("module\n\npublic import Mathlib\n\nnamespace MathFin\n\n"
            "/-- d. -/\ndef zcbX (r : ℝ) : ℝ := Real.exp r\n\n"
            "theorem t (r δ : ℝ) (hδ : 0 < δ) (hP : 0 < Real.exp r) :"
            " δ * Real.exp r > 0 := by sorry\n\n"
            "end MathFin\n")


def test_derivable_probe_builds_prop_guarded_examples():
    probe, names, base = af.derivable_probe(_DER_MOD)
    assert names == ["hδ", "hP"]
    assert "theorem t" not in probe and "def zcbX" in probe
    assert probe.rstrip().endswith("end MathFin")
    # hP's example binds only the EARLIER groups and Prop-guards the goal, so a
    # data binder's example is a type error (never a false hit)
    assert "example (r δ : ℝ) (hδ : 0 < δ) : ((0 < Real.exp r) : Prop) := by" in probe
    assert "maxHeartbeats" in probe


def test_derivable_probe_skips_multi_name_groups_and_no_theorem():
    lean = ("namespace MathFin\ntheorem t (a b : ℝ) (h : 0 ≤ 1) : a + b = b + a "
            ":= by sorry\nend MathFin\n")
    probe, names, _base = af.derivable_probe(lean)
    assert names == ["h"]
    assert af.derivable_probe("def x : Nat := 3") is None


def test_derivable_hypotheses_maps_error_lines():
    probe, names, base = af.derivable_probe(_DER_MOD)
    # error ON the first example line (hδ genuinely needed) → only hP derivable
    def check(code):
        assert code == probe
        return {"success": False, "sorry_count": 0,
                "errors": [f"line {base}:60: tactic 'first' failed"]}
    assert af.derivable_hypotheses(_DER_MOD, check_fn=check) == ["hP"]


def test_derivable_hypotheses_fails_open_on_foreign_or_unlocatable_errors():
    def foreign(code):
        return {"success": False, "sorry_count": 0, "errors": ["line 2:0: bad import"]}
    assert af.derivable_hypotheses(_DER_MOD, check_fn=foreign) == []
    def unlocatable(code):
        return {"success": False, "sorry_count": 0, "errors": ["daemon exploded"]}
    assert af.derivable_hypotheses(_DER_MOD, check_fn=unlocatable) == []


def test_formalize_with_repair_feeds_back_derivable_hypotheses():
    calls = {"n": 0}

    def fake_der(lean_text):
        calls["n"] += 1
        return ["hP"] if calls["n"] == 1 else []
    seen = []

    def chat(msgs):
        seen.append(msgs)
        return (_formalize_reply(), 10)
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5), chat_fn=chat,
                                 check_fn=_ELAB_OK, emit_fn=af.emit_target_files,
                                 rounds=3, derivable_fn=fake_der)
    assert r["ok"] is True and len(seen) == 2
    fb = " ".join(m["content"] for m in seen[1])
    assert "hP" in fb and "provable" in fb


# --- ∧-bundle advisory + core/corollary stub shape -----------------------------


def test_bundle_conclusion_detects_top_level_and_only():
    assert af.bundle_conclusion(" x = y ∧ y = x ") is True
    assert af.bundle_conclusion(" (A ∧ B) → C ") is False
    assert af.bundle_conclusion(" x = y ") is False


def test_formalize_with_repair_advises_once_on_bundle_then_accepts():
    bundle = "```lean\ntheorem foo (x : ℝ) : x = x ∧ 0 ≤ x * x := by sorry\n```"
    seen = []

    def chat(msgs):
        seen.append(msgs)
        return (bundle, 10)
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5), chat_fn=chat,
                                 check_fn=_ELAB_OK, emit_fn=af.emit_target_files, rounds=3)
    assert r["ok"] is True and len(seen) == 2   # one advisory round, then accepted as-is
    fb = " ".join(m["content"] for m in seen[1])
    assert "∧" in fb and "corollar" in fb.lower()


def test_formalize_with_repair_accepts_bundle_on_last_round():
    # regression (#73 defs seed): a ∧-bundle stub that ELABORATES on the FINAL
    # round must be ACCEPTED, not discarded by the soft advisory (which has no
    # round left to land in). Before the fix this dropped a good errors:[]/sorry:1
    # stub and reported "no elaborating Lean after N rounds".
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5),
                                 chat_fn=_script_chat([_formalize_reply(concl="x = y ∧ y = x")]),
                                 check_fn=_ELAB_OK, emit_fn=af.emit_target_files, rounds=1)
    assert r["ok"] is True and "∧" in r["lean_text"]


def test_formalize_with_repair_retries_no_code_without_consuming_a_round():
    # a no-code reply (leanstral at high reasoning effort returns ~21k tokens and no
    # ```lean block ~half the time) is a transient glitch, NOT a round: with rounds=1,
    # two no-code replies then a good stub must still succeed — the no-code replies
    # neither consume the productive round nor charge the token budget.
    replies = iter(["(reasoning, no code block)", "(still no code)",
                    _formalize_reply(concl="x = x")])

    def chat(_msgs):
        return (next(replies), 10)
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5), chat_fn=chat,
                                 check_fn=_ELAB_OK, emit_fn=af.emit_target_files, rounds=1)
    assert r["ok"] is True and "x = x" in r["lean_text"]
    assert r["tokens"] == 10          # only the productive reply is charged


def test_formalize_with_repair_gives_up_after_persistent_no_code():
    # all replies lack a ```lean block — the no-code retries are BOUNDED (no hang),
    # then it gives up with ok=False.
    calls = {"n": 0}

    def chat(_msgs):
        calls["n"] += 1
        return ("no code here at all", 10)
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5), chat_fn=chat,
                                 check_fn=_ELAB_OK, emit_fn=af.emit_target_files, rounds=2)
    assert r["ok"] is False
    assert calls["n"] <= 4            # bounded retries, not an infinite loop


def test_formalize_accepts_core_plus_sorry_free_corollary():
    two = ("```lean\n/-- core. -/\ntheorem coreThm (x : ℝ) (hx : x ≠ 0) : x / x = 1 "
           ":= by sorry\n\n/-- issue-shaped. -/\ntheorem corThm (x : ℝ) (hx : x ≠ 0) :"
           " x / x = 1 := coreThm x hx\n```")
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5), chat_fn=_script_chat([two]),
                                 check_fn=_ELAB_OK, emit_fn=af.emit_target_files, rounds=3)
    assert r["ok"] is True and "corThm" in r["lean_text"]


def test_rebuild_snippet_refuses_foreign_application():
    # the snippet re-exports a DIFFERENT theorem than the stripped core (the
    # corollary shape) — rebuilding it against the core would corrupt it
    snip = ("import M\n\ntheorem mf_x (a : ℝ) (h : 0 ≤ a) : a ≥ 0 :=\n"
            "  MathFin.otherThm a h\n")
    assert af._rebuild_snippet(snip, _STRONG_MOD, "premium_ge_mean") is None


def test_contracts_carry_corollary_shape():
    assert "corollar" in af.FORMALIZE_SYSTEM.lower()
    assert "corollary" in af.INTENT_DEFS_ADDENDUM.lower()
    intent = {**_INTENT, "corollary": {"name": "mf_shape", "statement": "the zcb case"}}
    joined = " ".join(m["content"] for m in af.formalize_messages(intent, ""))
    assert "mf_shape" in joined and "the zcb case" in joined


# --- post-gate proof golf -------------------------------------------------------

_GOLFABLE = ("module\n\nnamespace MathFin\n\n/-- d. -/\ndef fooX (x : ℝ) : ℝ := x\n\n"
             "theorem foo_ge (x : ℝ) (hx : 0 ≤ x) : fooX x ≥ 0 := by\n"
             "  dsimp [fooX]; nlinarith\n\nend MathFin\n")
_GOLFED = _GOLFABLE.replace("by\n  dsimp [fooX]; nlinarith", "hx")


def test_decl_signatures_extract_up_to_proof_separator():
    sigs = af._decl_signatures(_GOLFABLE)
    assert len(sigs) == 2
    assert sigs[1].startswith("theorem foo_ge") and "nlinarith" not in sigs[1]
    assert af._decl_signatures(_GOLFABLE) == af._decl_signatures(_GOLFED)


def test_golf_candidate_accepts_signature_preserving_green_golf():
    r = af.golf_candidate(_GOLFABLE, chat_fn=lambda m: (f"```lean\n{_GOLFED}\n```", 10),
                          regate_fn=lambda c: {"passed": True})
    assert r["golfed"] is True and r["candidate"].strip() == _GOLFED.strip()


def test_golf_candidate_rejects_statement_drift_before_regating():
    drifted = _GOLFED.replace("(hx : 0 ≤ x) ", "")

    def no_regate(c):
        raise AssertionError("statement drift must be rejected before the daemon is hit")
    r = af.golf_candidate(_GOLFABLE, chat_fn=lambda m: (f"```lean\n{drifted}\n```", 10),
                          regate_fn=no_regate)
    assert r["golfed"] is False and r["candidate"] == _GOLFABLE


def test_golf_candidate_fails_open_on_regate_failure_or_no_lean():
    r = af.golf_candidate(_GOLFABLE, chat_fn=lambda m: (f"```lean\n{_GOLFED}\n```", 10),
                          regate_fn=lambda c: {"passed": False, "reason": "axiom_dirty"})
    assert r["golfed"] is False and r["candidate"] == _GOLFABLE
    r2 = af.golf_candidate(_GOLFABLE, chat_fn=lambda m: ("no lean block", 10),
                           regate_fn=lambda c: {"passed": True})
    assert r2["golfed"] is False
    r3 = af.golf_candidate(_GOLFABLE,
                           chat_fn=lambda m: (f"```lean\n{_GOLFED.replace('hx', 'sorry')}\n```", 10),
                           regate_fn=lambda c: {"passed": True})
    assert r3["golfed"] is False   # a golf that reintroduces sorry is never accepted


def test_formalize_with_repair_guards_empty_assistant_content():
    # a free-tier EMPTY reply must not become an empty-content assistant message on the
    # next round (Mistral 400: "Assistant message must have either content or tool_calls").
    seen = []
    replies = ["", _formalize_reply(concl="x = x")]

    def chat(msgs):
        seen.append(list(msgs))
        return (replies[len(seen) - 1], 5)
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5), chat_fn=chat,
                                 check_fn=lambda c: {"success": True, "errors": [], "sorry_count": 1},
                                 emit_fn=af.emit_target_files, rounds=3)
    assert r["ok"] is True
    assert all(not (m["role"] == "assistant" and not m["content"]) for m in seen[1])   # 2nd call clean


def test_formalize_with_repair_logs_each_round():
    # instrumentation: each round emits a one-line diagnostic (why a draft fails is not opaque).
    logs = []
    replies = [_formalize_reply(concl="x ²"), _formalize_reply(concl="x = x")]
    checks = iter([{"success": False, "errors": ["unexpected token '²'"], "sorry_count": 1},
                   {"success": True, "errors": [], "sorry_count": 1}])
    af.formalize_with_repair(_INTENT, "", issue=_issue(5), chat_fn=_script_chat(replies),
                             check_fn=lambda c: next(checks), emit_fn=af.emit_target_files,
                             rounds=3, log=lambda m: logs.append(m))
    assert any("round 1" in m and "elab error" in m for m in logs)   # first round: the error
    assert any("elaborates" in m for m in logs)                      # second round: success


def test_formalize_with_repair_aborts_at_token_budget():
    # a doomed draft must not burn every round (#61 spent 77k tokens failing) — stop once
    # the cumulative token budget is exceeded, even if rounds remain.
    calls = {"n": 0}

    def chat(msgs):
        calls["n"] += 1
        return (_formalize_reply(concl="x ²"), 30)   # never elaborates; 30 tokens/round
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5), chat_fn=chat,
                                 check_fn=lambda c: {"success": False, "errors": ["bad"], "sorry_count": 1},
                                 emit_fn=af.emit_target_files, rounds=10, token_budget=50)
    assert r["ok"] is False
    assert calls["n"] == 2       # r1: 0<50 spend→30; r2: 30<50 spend→60; r3: 60>=50 break


def test_formalize_with_repair_gives_up_after_rounds():
    r = af.formalize_with_repair(_INTENT, "", issue=_issue(5),
                                 chat_fn=_script_chat([_formalize_reply("bad ²"), _formalize_reply("worse ²")]),
                                 check_fn=lambda c: {"success": False, "errors": ["e"], "sorry_count": 1},
                                 emit_fn=af.emit_target_files, rounds=2)
    assert r["ok"] is False


def test_repair_hint_partial_application():
    # #67's real CI failure: `… * MathFin.zcb r` (a ℝ→ℝ→ℝ function) instead of `… * zcb r t T`.
    errs = ["line 29:13: failed to synthesize instance of type class\n  HMul ℝ (ℝ → ℝ → ℝ) ?m.33"]
    h = af._repair_hint(errs)
    assert "PARTIALLY-APPLIED" in h and "all its arguments" in h.lower()


def test_import_error_handled_by_prelint_not_hint():
    # the mid-file-import error is now PREVENTED deterministically by _prelint_stub (it
    # strips stub-level imports), so no repair hint is emitted for it — the error can no
    # longer reach the model. Superseded the old soft-hint approach it kept ignoring.
    errs = ["line 26:0: invalid 'import' command, it must be used in the beginning of the file"]
    assert af._repair_hint(errs) == ""
    assert "import" not in af._prelint_stub("import Mathlib\ntheorem t : True := by sorry")


def test_repair_hint_empty_for_generic_error():
    assert af._repair_hint(["line 5: unsolved goals"]) == ""


def test_formalize_with_repair_appends_targeted_hint_on_partial_application():
    # the HMul-function hint must reach the repair prompt (generic feedback couldn't fix #67).
    seen = []
    replies = [_formalize_reply(concl="x * (fun a b => a)"), _formalize_reply(concl="x = x")]

    def chat(msgs):
        seen.append(list(msgs))
        return (replies[len(seen) - 1], 10)
    checks = iter([{"success": False, "sorry_count": 1,
                    "errors": ["line 29:13: failed to synthesize instance of type class\n  HMul ℝ (ℝ → ℝ → ℝ) ?m"]},
                   {"success": True, "errors": [], "sorry_count": 1}])
    af.formalize_with_repair(_INTENT, "", issue=_issue(5), chat_fn=chat,
                             check_fn=lambda c: next(checks), emit_fn=af.emit_target_files, rounds=3)
    assert "PARTIALLY-APPLIED" in " ".join(m["content"] for m in seen[1])   # 2nd call got the hint


def test_unknown_identifiers_extracted_and_deduped():
    errs = ["line 3: unknown identifier 'Foo.bar'", "line 5: unknown constant 'Baz'",
            "again unknown identifier 'Foo.bar'", "unrelated error"]
    assert af._unknown_identifiers(errs) == ["Foo.bar", "Baz"]


def test_loogle_candidates_uses_injected_runner():
    assert af.loogle_candidates("zcb", main_repo="/x", run_fn=lambda nm: f"hit:{nm}") == "hit:zcb"


def test_fidelity_system_accepts_concrete_realization_refinement():
    # #67 passed the judge but fidelity flagged "drops positivity" — the same over-strictness:
    # omitting `0 < P` is correct when the Lean realizes P with `zcb` (Real.exp, provably positive).
    sys = " ".join(m["content"] for m in af.fidelity_messages(_INTENT, "theorem foo : True := by sorry"))
    assert "PROVABLE" in sys and "correct refinement" in sys


def test_intent_fidelity_faithful_and_tokens():
    r = af.intent_fidelity_check(_INTENT, "theorem foo : True := by sorry",
                                 reason_fn=_canned_chat('{"faithful": true, "verdict": "ok"}', 8))
    assert r["faithful"] is True and r["tokens"] == 8


def test_intent_fidelity_rejects_on_explicit_false():
    r = af.intent_fidelity_check(_INTENT, "theorem foo : True := by sorry",
                                 reason_fn=_canned_chat('{"faithful": false, "verdict": "dropped hyp"}', 8))
    assert r["faithful"] is False


def test_intent_fidelity_fails_open_when_unparseable():
    r = af.intent_fidelity_check(_INTENT, "theorem foo : True := by sorry",
                                 reason_fn=_canned_chat("hmm no json", 3))
    assert r["faithful"] is True    # reject only on an explicit false


# --- issue preparation (pointers extraction + filter/enrich) -----------------

def test_extract_pointers_finds_dedups_orders_mathfin_paths():
    body = ("## Pointers\n- `MathFin/FixedIncome/ZCB.lean` (zcb)\n"
            "- MathFin/FixedIncome/ForwardRate.lean\n"
            "again MathFin/FixedIncome/ZCB.lean and MathFin/Foo/Bar.lean")
    assert af.extract_pointers(body) == [
        "MathFin/FixedIncome/ZCB.lean",
        "MathFin/FixedIncome/ForwardRate.lean",
        "MathFin/Foo/Bar.lean",
    ]


def test_extract_pointers_empty():
    assert af.extract_pointers("no lean paths here") == []


def test_prepare_issues_filters_and_enriches():
    raw = [
        {"number": 67, "title": "FRA",
         "body": "## Task\nfoo\n## Pointers\nMathFin/FixedIncome/ZCB.lean",
         "labels": [{"name": "status:ready"}, {"name": "type:proof"},
                    {"name": "area:fixed-income"}, {"name": "difficulty:small"}]},
        {"number": 99, "title": "research",
         "body": "x", "labels": [{"name": "status:blocked-research"},
                                  {"name": "type:research"}]},
    ]
    out = af.prepare_issues(raw)
    assert [i["number"] for i in out] == [67]        # 99 (not ready+proof) filtered
    assert out[0]["area"] == "fixed-income"
    assert out[0]["pointers"] == ["MathFin/FixedIncome/ZCB.lean"]
    assert out[0]["body"].startswith("## Task")


def test_formalize_injects_proactive_premises_into_first_message():
    captured = {}

    def chat(msgs):
        captured["msgs"] = msgs
        return ("```lean\ntheorem t : True := by sorry\n```", 10)

    intent = {"module_name": "M", "benchmark_id": "b", "statement": "True", "docstring": ""}
    af.formalize_with_repair(
        intent, "GROUNDING", issue={"number": 1, "name": "n", "domain": "d"},
        chat_fn=chat, check_fn=lambda t: {"errors": [], "sorry_count": 1},
        emit_fn=lambda i, s, m: ("LEAN", {"id": "x"}, None),
        rounds=1, proactive_premises="MathFin.zcb : ℝ → ℝ")
    blob = "\n".join(m["content"] for m in captured["msgs"])
    assert "MathFin.zcb : ℝ → ℝ" in blob


# --- build_retrieve_fns (backend selection + fails-open fallback) -----------

def test_build_retrieve_fns_selects_loogle_when_configured():
    # backend "loogle" ⇒ reactive loogle fn, no proactive fn (loogle is name-only)
    r, p = af.build_retrieve_fns(backend="loogle", main_repo="/x", index_dir="/no/index",
                                 k=8, embed_model="mistral-embed", api_key="k")
    assert r is not None and p is None


def test_build_retrieve_fns_falls_open_to_loogle_when_index_absent():
    # backend "embedding" but no cache present ⇒ degrade to loogle, no proactive
    r, p = af.build_retrieve_fns(backend="embedding", main_repo="/x",
                                 index_dir="/no/index", k=8,
                                 embed_model="mistral-embed", api_key="k")
    assert r is not None and p is None
