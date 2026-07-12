"""Tests for the issue->stub autoformalizer sub-probe. Pure logic only —
injected chat_fn/check_fn, temp trees; no Lean, no API, no network."""

from __future__ import annotations

import build_manifest as bm
import pipeline_lib as pl

import autoformalize as af


# --- AutoformalizeConfig -----------------------------------------------------

def test_autoformalize_config_defaults():
    cfg = pl.AutoformalizeConfig.load(None)
    assert cfg.enabled is True
    assert cfg.budget > 0
    assert cfg.draft_model == "magistral-medium-latest"
    assert cfg.prover_model == "labs-leanstral-1-5"


def test_autoformalize_config_reads_toml(tmp_path):
    toml = tmp_path / "pipeline.toml"
    toml.write_text("[autoformalize]\nenabled = false\nbudget = 123456\nmax_issues = 2\n")
    cfg = pl.AutoformalizeConfig.load(str(toml))
    assert cfg.enabled is False
    assert cfg.budget == 123456
    assert cfg.max_issues == 2
    assert cfg.gate_budget == 20_000        # unspecified keys keep defaults


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


# --- honest subsetting: declared `deferred` remainder ------------------------

_DEFERRED_FACT = "term-structure monotonicity: T ↦ F(T) increasing iff r > δ"
_META_SUBSET = {**_META, "deferred": [_DEFERRED_FACT]}


def test_normalize_deferred_cleans_and_coerces():
    assert af.normalize_deferred(None) == []
    assert af.normalize_deferred([]) == []
    assert af.normalize_deferred(["a", " b ", "", "  "]) == ["a", "b"]
    assert af.normalize_deferred("solo fact") == ["solo fact"]   # bare string → one item
    assert af.normalize_deferred(["x", 3]) == ["x", "3"]         # non-str coerced


def test_draft_system_documents_subset_and_deferred_contract():
    joined = " ".join(m["content"] for m in af.draft_messages(
        {"number": 88, "title": "t", "body": "b", "pointers": []}, "", ""))
    assert "deferred" in joined      # the json field the drafter must emit on a subset
    assert "SUBSET" in joined         # subsetting is explicitly allowed
    assert ":= by sorry" in joined    # unchanged stub-format contract still present


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

_DRAFT_REPLY = (
    "Here's the formalization.\n\n"
    "```lean\ntheorem foo (x : ℝ) : x = x := by sorry\n```\n\n"
    '```json\n{"module_name": "Foo", "benchmark_id": "mf-fi-foo", "docstring": "reflexivity"}\n```\n'
)


def test_parse_draft_extracts_stub_and_meta():
    stub, meta = af.parse_draft(_DRAFT_REPLY)
    assert stub == "theorem foo (x : ℝ) : x = x := by sorry"
    assert meta["module_name"] == "Foo"
    assert meta["benchmark_id"] == "mf-fi-foo"
    assert meta["docstring"] == "reflexivity"


def test_parse_draft_none_when_no_lean_block():
    assert af.parse_draft("no code fence here") is None


def test_parse_draft_none_when_missing_required_meta():
    reply = '```lean\ntheorem foo : True := by sorry\n```\n```json\n{"docstring": "x"}\n```'
    assert af.parse_draft(reply) is None            # module_name/benchmark_id missing


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


def test_draft_messages_includes_issue_context_and_contract():
    msgs = af.draft_messages(
        {"number": 88, "title": "Contango", "body": "F = S e^{rT}", "pointers": ["A.lean"]},
        "CTXPACK", "PINSXYZ")
    joined = " ".join(m["content"] for m in msgs)
    assert any(m["role"] == "system" for m in msgs)
    assert "Contango" in joined and "F = S e^{rT}" in joined
    assert "CTXPACK" in joined and "PINSXYZ" in joined
    assert ":= by sorry" in joined                    # the stub-format contract


def test_judge_faithfulness_parses_verdict():
    chat = _canned_chat('```json\n{"faithful": true, "verdict": "ok", "issues": []}\n```', 42)
    r = af.judge_faithfulness({"number": 1, "title": "t", "body": "b"},
                              "theorem foo : True := by sorry", chat_fn=chat)
    assert r["faithful"] is True
    assert r["tokens"] == 42


def _lean_reply(concl="x = x", name="foo"):
    return f"```lean\ntheorem {name} (x : ℝ) : {concl} := by sorry\n```"


def test_roundtrip_is_cross_model_reformalize_uses_prove_fn():
    # the point of (b): informalize (magistral reason_fn) → RE-FORMALIZE (LEANSTRAL
    # prove_fn, independent of the drafter) → compare (magistral reason_fn).
    order = []
    reason_replies = iter(["prose describing the theorem", '{"faithful": true, "verdict": "same"}'])
    def reason(m):
        order.append("reason"); return (next(reason_replies), 5)
    def prove(m):
        order.append("prove"); return (_lean_reply(), 7)
    r = af.roundtrip_check(_issue(5), "theorem foo (x : ℝ) : x = x := by sorry",
                           reason_fn=reason, prove_fn=prove)
    assert order == ["reason", "prove", "reason"]     # magistral → LEANSTRAL → magistral
    assert r["faithful"] is True
    assert r["tokens"] == 17                           # 5 + 7 + 5, all charged


def test_roundtrip_flags_cross_model_divergence():
    reason = _script_chat(["prose", '{"faithful": false, "verdict": "differ"}'])
    r = af.roundtrip_check(_issue(5), "theorem foo (x : ℝ) : x = x := by sorry",
                           reason_fn=reason, prove_fn=_script_chat([_lean_reply("x = y")]))
    assert r["faithful"] is False


def test_roundtrip_inconclusive_when_leanstral_gives_no_statement():
    # leanstral re-formalize produced no Lean block → inconclusive → do NOT block
    # the draft (a soft signal that could not run is not a rejection).
    reason = _script_chat(["prose"])                   # compare is never reached
    r = af.roundtrip_check(_issue(5), "theorem foo : True := by sorry",
                           reason_fn=reason, prove_fn=_script_chat(["I cannot formalize this."]))
    assert r["faithful"] is True
    assert r.get("inconclusive") is True


# --- draft-repair loop (compiler feedback on the draft) ----------------------

def _script_chat(replies, tokens=10):
    it = iter(replies)
    return lambda msgs: (next(it), tokens)


def _draft_reply(concl="x = x", name="foo", mod="Foo", bid="mf-fi-foo"):
    return (f"```lean\ntheorem {name} (x : ℝ) : {concl} := by sorry\n```\n"
            f'```json\n{{"module_name": "{mod}", "benchmark_id": "{bid}", "docstring": "d"}}\n```')


def test_draft_with_repair_succeeds_first_round():
    r = af.draft_with_repair(_issue(5), "", "", chat_fn=_script_chat([_draft_reply()]),
                             check_fn=_ELAB_OK, emit_fn=af.emit_target_files, rounds=2)
    assert r["ok"] is True
    assert "theorem foo" in r["lean_text"]
    assert r["tokens"] == 10


def test_draft_with_repair_repairs_on_elaboration_failure():
    replies = [_draft_reply(concl="x ²"), _draft_reply(concl="x = x")]
    checks = iter([{"success": False, "errors": ["unexpected token '²'"], "sorry_count": 1},
                   {"success": True, "errors": [], "sorry_count": 1}])
    r = af.draft_with_repair(_issue(5), "", "", chat_fn=_script_chat(replies),
                             check_fn=lambda c: next(checks), emit_fn=af.emit_target_files, rounds=2)
    assert r["ok"] is True
    assert "x = x" in r["lean_text"]        # the corrected statement is returned
    assert r["tokens"] == 20                # both attempts charged


def test_draft_with_repair_accepts_valid_stub_despite_success_false():
    # the daemon reports success=False for a well-formed statement that still has
    # its `sorry` (the sorry is a warning, not an error). A stub with EMPTY errors
    # and exactly one sorry IS elaborating — accept it (regression: an earlier check
    # keyed on `success` and rejected every valid draft).
    r = af.draft_with_repair(_issue(5), "", "", chat_fn=_script_chat([_draft_reply()]),
                             check_fn=lambda c: {"success": False, "errors": [], "sorry_count": 1},
                             emit_fn=af.emit_target_files, rounds=2)
    assert r["ok"] is True


def test_draft_with_repair_gives_up_after_rounds():
    replies = [_draft_reply(concl="bad ²"), _draft_reply(concl="worse ²")]
    r = af.draft_with_repair(_issue(5), "", "", chat_fn=_script_chat(replies),
                             check_fn=lambda c: {"success": False, "errors": ["e"], "sorry_count": 1},
                             emit_fn=af.emit_target_files, rounds=2)
    assert r["ok"] is False


def test_draft_with_repair_feedback_carries_error_and_caret_hint():
    seen = []
    def chat(msgs):
        seen.append(msgs)
        return (_draft_reply(concl="x ²"), 10)
    af.draft_with_repair(_issue(5), "", "", chat_fn=chat,
                         check_fn=lambda c: {"success": False, "errors": ["unexpected token '²'"], "sorry_count": 1},
                         emit_fn=af.emit_target_files, rounds=2)
    round2 = " ".join(m["content"] for m in seen[1])
    assert "unexpected token '²'" in round2      # the compiler error is fed back
    assert "^" in round2                         # the "use ^ not ²" hint


# --- kernel-gate runners (drive run_target with injected chat_fn/check_fn) ----

_PROVES = lambda code: {"success": True, "errors": [], "sorry_count": 0}
_FAILS = lambda code: {"success": False, "errors": ["unsolved goals"], "sorry_count": 0}


def test_hypothesis_rejection_flags_provable_false():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    r = af.hypothesis_rejection(lean_text, "fra_value",
                                chat_fn=_canned_chat("```lean\nproof\n```", 50),
                                check_fn=_PROVES, budget=20000)
    assert r["vacuous"] is True
    assert r["tokens"] > 0


def test_hypothesis_rejection_passes_when_unprovable():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    r = af.hypothesis_rejection(lean_text, "fra_value",
                                chat_fn=_canned_chat("```lean\nattempt\n```", 50),
                                check_fn=_FAILS, budget=20000)
    assert r["vacuous"] is False


def test_disproof_flags_provable_negation():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    r = af.disproof(lean_text, "fra_value",
                    chat_fn=_canned_chat("```lean\nproof\n```", 50),
                    check_fn=_PROVES, budget=20000)
    assert r["false"] is True


def test_disproof_passes_when_negation_unprovable():
    lean_text, _e, _p = af.emit_target_files(_ISSUE, _STUB, _META)
    r = af.disproof(lean_text, "fra_value",
                    chat_fn=_canned_chat("```lean\nattempt\n```", 50),
                    check_fn=_FAILS, budget=20000)
    assert r["false"] is False


# --- refill orchestrator (monkeypatched steps; control flow only) ------------

def _issue(n, **kw):
    d = {"number": n, "area": "fixed-income", "title": f"t{n}", "body": "b",
         "pointers": [], "difficulty": "small"}
    d.update(kw)
    return d


def _good_dwr(i, cp, p, *, chat_fn, check_fn, emit_fn, rounds):
    """A stand-in for draft_with_repair that returns an ok result with a real
    emitted lean_text/entry (so refill's _write_target produces valid files)."""
    n = i["number"]
    stub = f"theorem t{n} (h : p) : q := by sorry"
    meta = {"module_name": f"T{n}", "benchmark_id": f"mf-fi-t{n}", "docstring": "d"}
    lean_text, entry, _ = emit_fn(i, stub, meta)
    return {"ok": True, "stub": stub, "meta": meta, "lean_text": lean_text,
            "entry": entry, "tokens": 10}


def _pass_gates(monkeypatch):
    monkeypatch.setattr(af, "hypothesis_rejection", lambda *a, **k: {"vacuous": False, "tokens": 1})
    monkeypatch.setattr(af, "disproof", lambda *a, **k: {"false": False, "tokens": 1})
    monkeypatch.setattr(af, "judge_faithfulness",
                        lambda i, s, chat_fn, deferred=None: {"faithful": True, "tokens": 1})
    monkeypatch.setattr(af, "roundtrip_check", lambda i, s, *, reason_fn, prove_fn: {"faithful": True, "tokens": 1})


_NOOP = lambda m: ("", 0)
_ELAB_OK = lambda c: {"success": True, "sorry_count": 1, "errors": []}


def test_refill_stages_a_good_issue(monkeypatch, tmp_path):
    monkeypatch.setattr(af, "draft_with_repair", _good_dwr)
    _pass_gates(monkeypatch)
    res = af.refill([_issue(5)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert [s["issue"] for s in res["seeded"]] == [5]
    assert (tmp_path / "cal-bk-5.lean").exists()
    assert (tmp_path / "cal-bk-5.entry.json").exists()


def test_refill_skips_vacuous_then_stages_next(monkeypatch, tmp_path):
    monkeypatch.setattr(af, "draft_with_repair", _good_dwr)
    monkeypatch.setattr(af, "disproof", lambda *a, **k: {"false": False, "tokens": 1})
    monkeypatch.setattr(af, "judge_faithfulness",
                        lambda i, s, chat_fn, deferred=None: {"faithful": True, "tokens": 1})
    monkeypatch.setattr(af, "roundtrip_check", lambda i, s, *, reason_fn, prove_fn: {"faithful": True, "tokens": 1})
    # issue 1's stub is vacuous, issue 2's is not
    monkeypatch.setattr(af, "hypothesis_rejection",
                        lambda lt, nm, **k: {"vacuous": "theorem t1 " in lt, "tokens": 1})
    res = af.refill([_issue(1), _issue(2)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert [s["issue"] for s in res["seeded"]] == [2]
    assert not (tmp_path / "cal-bk-1.lean").exists()
    assert (tmp_path / "cal-bk-2.lean").exists()


def test_refill_skips_unfaithful_judge(monkeypatch, tmp_path):
    monkeypatch.setattr(af, "draft_with_repair", _good_dwr)
    monkeypatch.setattr(af, "hypothesis_rejection", lambda *a, **k: {"vacuous": False, "tokens": 1})
    monkeypatch.setattr(af, "disproof", lambda *a, **k: {"false": False, "tokens": 1})
    monkeypatch.setattr(af, "judge_faithfulness",
                        lambda i, s, chat_fn, deferred=None: {"faithful": False, "verdict": "weaker", "tokens": 1})
    monkeypatch.setattr(af, "roundtrip_check", lambda i, s, *, reason_fn, prove_fn: {"faithful": True, "tokens": 1})
    res = af.refill([_issue(7)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert res["seeded"] == []
    assert not (tmp_path / "cal-bk-7.lean").exists()


def test_refill_skips_issue_on_step_exception(monkeypatch, tmp_path):
    # a transient error (e.g. HTTP 429 exhaustion) on one issue must not crash the
    # whole refill — log it and skip to the next issue.
    def boom(i, cp, p, *, chat_fn, check_fn, emit_fn, rounds):
        if i["number"] == 1:
            raise RuntimeError("HTTP 429 from Mistral API")
        return _good_dwr(i, cp, p, chat_fn=chat_fn, check_fn=check_fn, emit_fn=emit_fn, rounds=rounds)
    monkeypatch.setattr(af, "draft_with_repair", boom)
    _pass_gates(monkeypatch)
    res = af.refill([_issue(1), _issue(2)], reason_fn=_NOOP, prove_fn=_NOOP, check_fn=_ELAB_OK,
                    context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert [s["issue"] for s in res["seeded"]] == [2]
    assert (tmp_path / "cal-bk-2.lean").exists()


def test_refill_wires_reason_and_prove_fns(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(
        af, "draft_with_repair",
        lambda i, cp, p, *, chat_fn, check_fn, emit_fn, rounds:
        seen.update(draft=chat_fn) or _good_dwr(i, cp, p, chat_fn=chat_fn,
                                                check_fn=check_fn, emit_fn=emit_fn, rounds=rounds))
    monkeypatch.setattr(af, "hypothesis_rejection",
                        lambda lt, nm, **k: seen.update(gate=k["chat_fn"]) or {"vacuous": False, "tokens": 1})
    monkeypatch.setattr(af, "disproof", lambda *a, **k: {"false": False, "tokens": 1})
    monkeypatch.setattr(af, "judge_faithfulness",
                        lambda i, s, chat_fn, deferred=None: seen.update(judge=chat_fn) or {"faithful": True, "tokens": 1})
    monkeypatch.setattr(af, "roundtrip_check", lambda i, s, *, reason_fn, prove_fn: {"faithful": True, "tokens": 1})
    R = lambda m: ("R", 0)
    P = lambda m: ("P", 0)
    af.refill([_issue(9)], reason_fn=R, prove_fn=P, check_fn=_ELAB_OK,
              context_fn=lambda i: "", queue_dir=str(tmp_path), budget=100000)
    assert seen["draft"] is R and seen["judge"] is R    # magistral drafts + judges
    assert seen["gate"] is P                            # leanstral runs the kernel gates


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
