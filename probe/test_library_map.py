"""library_map reads the target library's own map from a checkout.

Every test runs against a FIXTURE checkout built in a temp dir, shaped like the
flagship after its 2026-11-08 reorganisation (layers, one corpus file per area, a
taxonomy, a bibliography, a blueprint with a frontier). None reads the real library,
so none drifts when it does — which is the failure this module exists to end.
"""
from __future__ import annotations

import json
import os

import pytest

import af_drafting
import assemble
import autoformalize as af
import domain_pack
import issue_state
import library_map as lm

PACK = domain_pack.load("mathfin")

TAXONOMY = '''
[[cell]]
key = "valuation/term"
title = "Term structures"
modules = ["MathFin.Models.Rates"]
subjects = ["MSC 91G30", "JEL E43"]

[[cell]]
key = "risk/measures"
title = "Risk measures"
modules = ["MathFin.Risk.Coherent", "MathFin.Risk.Spectral"]
subjects = ["MSC 91G70"]

[[cell]]
key = "probability/gaussian"
modules = ["MathFin.Probability.Gaussian"]
subjects = ["MSC 60G15"]
'''

BIB = '''
% comment
@book{Hull,
  author    = {Hull, John C.},
  title     = {Options, Futures, and Other Derivatives},
  publisher = {Pearson}
}

@article{AcerbiTasche2002,
  author = {Acerbi, Carlo and Tasche, Dirk},
  title  = {On the coherence of expected shortfall},
  year   = {2002}
}

@book{McNeilFreyEmbrechts,
  author = {McNeil, Alexander J. and Frey, R{\\"u}diger and Embrechts, Paul},
  title  = {Quantitative Risk Management}
}
'''

FRONTIER = '''# the frontier
[[node]]
label = "thm:ho-lee"
title = "Ho–Lee bond price"
statement = "The Ho–Lee bond price is affine."
uses = ["thm:vasicek"]
area = "valuation/term"

[[node]]
label = "thm:survival"
title = "Survival model"
statement = "Survival relations."
uses = []
ground = "Mathlib: conditional probability"
area = "valuation/insurance"
core = ["mf-survival-a"]

[[node]]
label = "thm:last"
title = "Last"
statement = "The last node."
uses = []
area = "x"
'''


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _entry(eid, module, **prov):
    meta = {"formalization_status": "full"}
    if prov:
        meta["provenance"] = prov
    return {"id": eid, "description": f"about {eid}.",
            "code": {"lean": f"import {module}\n\ntheorem t : True := trivial\n"},
            "metadata": meta}


@pytest.fixture()
def repo(tmp_path):
    root = str(tmp_path)
    for rel in ("MathFin/Models/Rates/ZCB.lean", "MathFin/Models/Rates/Vasicek.lean",
                "MathFin/Risk/Coherent.lean", "MathFin/Risk/Spectral.lean",
                "MathFin/Probability/Gaussian/StandardNormal.lean",
                "MathFin/Models/Rates/ForwardMeasure.lean",
                "MathFin/Pricing/ForwardMeasure.lean"):
        _write(root, rel, "namespace MathFin\n\nnoncomputable def zcb (r : ℝ) : ℝ := r\n\n"
                          "theorem zcb_pos : True := trivial\n\nend MathFin\n")
    _write(root, "docs/taxonomy.toml", TAXONOMY)
    _write(root, "docs/references.bib", BIB)
    _write(root, "docs/blueprint_frontier.toml", FRONTIER)
    _write(root, "docs/blueprint_nodes.json", json.dumps([
        {"label": "thm:vasicek", "name": "MathFin.vasicekBondPrice", "title": "Vasicek"},
        {"label": "def:phi", "name": "MathFin.Phi", "title": "Normal CDF"}]))
    _write(root, "benchmarks/interest_rates.json", json.dumps({"theorems": [
        _entry("mf-zcb-a", "MathFin.Models.Rates.ZCB"),
        _entry("mf-zcb-b", "MathFin.Models.Rates.Vasicek"),
        _entry("mf-auto", "MathFin.Models.Rates.ZCB", source="leanstral-autoform", issue=66)]}))
    _write(root, "benchmarks/risk_measures.json", json.dumps([
        _entry("mf-risk-a", "MathFin.Risk.Coherent"),
        _entry("mf-survival-a", "MathFin.Risk.Spectral", source="afp", issue=112)]))
    return root


LAYOUT = PACK.layout


# --- pointers -------------------------------------------------------------------

def test_a_moved_pointer_is_followed_by_basename(repo):
    live, notes = lm.heal_pointers(repo, "MathFin", [
        "MathFin/FixedIncome/ZCB.lean",          # moved: one module has this name
        "MathFin/Models/Rates/Vasicek.lean",     # already live
        "MathFin/FixedIncome/ForwardMeasure.lean",   # two modules share the name
        "MathFin/FX/Siegel.lean"])               # names a module to create
    assert live == ["MathFin/Models/Rates/ZCB.lean", "MathFin/Models/Rates/Vasicek.lean"]
    how = {n["pointer"]: n["how"] for n in notes}
    assert how == {"MathFin/FixedIncome/ZCB.lean": "moved",
                   "MathFin/FixedIncome/ForwardMeasure.lean": "ambiguous",
                   "MathFin/FX/Siegel.lean": "missing"}


def test_healing_never_duplicates_a_pointer(repo):
    live, _ = lm.heal_pointers(repo, "MathFin", ["MathFin/Old/ZCB.lean",
                                                  "MathFin/Models/Rates/ZCB.lean"])
    assert live == ["MathFin/Models/Rates/ZCB.lean"]


# --- taxonomy -------------------------------------------------------------------

def test_a_cell_names_its_directory(repo):
    tax = lm.Taxonomy.load(repo, LAYOUT.taxonomy)
    assert tax.directory_for(repo, ["valuation/term"]) == "MathFin/Models/Rates"


def test_a_cell_listed_module_by_module_lives_where_its_modules_do(repo):
    # the risk measures: every prefix is a module, none is a directory
    tax = lm.Taxonomy.load(repo, LAYOUT.taxonomy)
    assert tax.directory_for(repo, ["risk/measures"]) == "MathFin/Risk"


def test_the_first_label_that_names_a_cell_wins_and_none_is_none(repo):
    tax = lm.Taxonomy.load(repo, LAYOUT.taxonomy)
    assert tax.directory_for(repo, ["no/such", "valuation/term"]) == "MathFin/Models/Rates"
    assert tax.directory_for(repo, ["no/such"]) is None
    assert not lm.Taxonomy.load(repo, "docs/absent.toml")


def test_subjects_come_from_the_longest_prefix(repo):
    tax = lm.Taxonomy.load(repo, LAYOUT.taxonomy)
    assert tax.subjects_of_module("MathFin.Models.Rates.HoLee") == ["MSC 91G30", "JEL E43"]
    assert tax.subjects_of_module("MathFin.Elsewhere.X") == []


# --- corpus ---------------------------------------------------------------------

def test_an_entry_goes_where_its_neighbours_are(repo):
    assert lm.benchmark_for(repo, "benchmarks", "MathFin/Models/Rates") \
        == "benchmarks/interest_rates.json"
    assert lm.benchmark_for(repo, "benchmarks", "MathFin/Risk") \
        == "benchmarks/risk_measures.json"


def test_a_directory_with_no_entries_walks_up_then_falls_back(repo):
    # Models/Jumps has no entries; Models does (the Rates ones)
    assert lm.benchmark_for(repo, "benchmarks", "MathFin/Models/Jumps") \
        == "benchmarks/interest_rates.json"
    assert lm.benchmark_for(repo, "", "MathFin/Models/Rates", fallback="b/x.json") == "b/x.json"


def test_served_issues_are_the_pipelines_own_contributions(repo):
    # issue 112's entry came from a port, not from the pipeline
    assert lm.served_issues(repo, "benchmarks") == {66}
    assert "mf-risk-a" in lm.corpus_ids(repo, "benchmarks")


def test_declared_in_library_finds_a_name_and_skips_the_scratch(repo):
    assert lm.declared_in_library(repo, "MathFin", "MathFin.zcb").startswith("MathFin/")
    assert lm.declared_in_library(repo, "MathFin", "zcb_positive") is None   # a prefix is not it
    every = tuple(os.path.relpath(os.path.join(d, f), repo)
                  for d, _s, fs in os.walk(os.path.join(repo, "MathFin")) for f in fs)
    assert lm.declared_in_library(repo, "MathFin", "zcb", exclude=every) is None


# --- bibliography ---------------------------------------------------------------

def test_the_bibliography_renders_the_librarys_citation_forms(repo):
    bib = lm.Bibliography.load(repo, LAYOUT.bibliography)
    assert set(bib.entries) == {"Hull", "AcerbiTasche2002", "McNeilFreyEmbrechts"}
    assert bib.bullet("Hull", "§5.4") == \
        "* [Hull, *Options, Futures, and Other Derivatives*][Hull], §5.4"
    assert bib.bullet("Hull") == "* [Hull, *Options, Futures, and Other Derivatives*][Hull]"
    assert bib.reference("AcerbiTasche2002") == \
        "Acerbi and Tasche, On the coherence of expected shortfall"
    assert bib.authors("McNeilFreyEmbrechts") == "McNeil, Frey and Embrechts"
    assert "- AcerbiTasche2002: Acerbi and Tasche (2002), On the coherence" in bib.catalogue()


# --- blueprint ------------------------------------------------------------------

def test_named_nodes_reads_the_blueprint_line():
    assert lm.named_nodes("text\n\nBlueprint: thm:ho-lee, def:phi\n") == ["thm:ho-lee", "def:phi"]
    assert lm.named_nodes("- **Blueprint:** `thm:ho-lee`") == ["thm:ho-lee"]
    assert lm.named_nodes("no such line; blueprint mentioned in passing") == []


def test_the_blueprint_tells_proved_from_frontier(repo):
    bp = lm.Blueprint.load(repo, LAYOUT)
    assert bp.kind("def:phi") == "proved" and bp.kind("thm:ho-lee") == "frontier"
    assert bp.kind("thm:nope") == "unknown"
    assert bp.frontier_of(["def:phi", "thm:ho-lee"]) == ["thm:ho-lee"]
    note = bp.note(["thm:ho-lee", "thm:survival"], {"mf-survival-a": "the ratio."})
    assert "builds on thm:vasicek: `MathFin.vasicekBondPrice`" in note
    assert "do NOT restate: `mf-survival-a` — the ratio." in note
    assert "ground: Mathlib: conditional probability" in note
    # the issue's own anchors first, then the map's definition nodes
    assert bp.hint(["thm:ho-lee"]) == "`MathFin.vasicekBondPrice`, `MathFin.Phi`"


def test_add_core_creates_extends_and_is_idempotent():
    one = lm.add_core(FRONTIER, "thm:ho-lee", "mf-a")
    assert 'area = "valuation/term"\ncore = ["mf-a"]\n\n[[node]]\nlabel = "thm:survival"' in one
    two = lm.add_core(one, "thm:survival", "mf-b")
    assert 'core = ["mf-survival-a", "mf-b"]' in two
    assert lm.add_core(two, "thm:survival", "mf-b") == two
    last = lm.add_core(FRONTIER, "thm:last", "mf-c")
    assert last.endswith('area = "x"\ncore = ["mf-c"]\n')
    assert lm.add_core(FRONTIER, "thm:absent", "mf-a") is None
    # the comment at the top of the file survives a text edit
    assert two.startswith("# the frontier\n")


def test_record_frontier_core_writes_only_frontier_labels(repo):
    done = assemble.record_frontier_core(repo, LAYOUT.blueprint_frontier,
                                         ["def:phi", "thm:ho-lee"], "mf-new")
    assert done == ["thm:ho-lee"]
    text = open(os.path.join(repo, LAYOUT.blueprint_frontier), encoding="utf-8").read()
    assert 'core = ["mf-new"]' in text
    import tomllib
    nodes = {n["label"]: n for n in tomllib.loads(text)["node"]}   # still valid TOML
    assert nodes["thm:ho-lee"]["core"] == ["mf-new"]


# --- the module docstring --------------------------------------------------------

def test_module_doc_is_the_librarys_layout_and_fits_the_line_limit():
    doc = lm.module_doc(
        title="contango of the cost-of-carry forward.",
        summary="For a positive spot the forward `S * exp ((r - δ) * T)` exceeds the spot "
                "exactly when the rate exceeds the convenience yield, " + "and so on " * 12,
        definitions=[("carry", "the net carry rate")],
        results=[("forward_gt_spot_iff", "the forward exceeds the spot iff `δ < r`")],
        references=["* [Hull, *Options*][Hull], §5.11"],
        tags=["Contango", "cost of carry", "JEL G13", "contango"],
        subjects=["MSC 91G30"])
    heads = [ln for ln in doc.splitlines() if ln.startswith("#")]
    assert heads == ["# Contango of the cost-of-carry forward", "## Main definitions",
                     "## Main results", "## References", "## Tags"]
    assert all(len(ln) <= lm.LINE_LIMIT for ln in doc.splitlines())
    # a drafter-supplied subject code is dropped: the codes are the CELL's
    assert doc.rstrip().endswith("contango, cost of carry, MSC 91G30")
    # every heading is followed by a blank line (a hard rule of the standard)
    lines = doc.splitlines()
    assert all(lines[i + 1] == "" for i, ln in enumerate(lines) if ln.startswith("#"))


def test_module_doc_never_breaks_inside_a_code_span():
    doc = lm.module_doc(title="t", summary="x " * 40 + "`Phi x - Phi (-x) = 2 * Phi x - 1` holds.",
                        definitions=[], results=[], references=[], tags=[], subjects=[])
    assert "`Phi x - Phi (-x) = 2 * Phi x - 1`" in doc
    assert "## Main results" not in doc and "## References" not in doc


def test_long_lines_counts_what_the_ratchet_counts():
    assert lm.long_lines("a\n" + "b" * 101 + "\n" + "c" * 100) == [2]


# --- prepare_issues: the whole reading, on one issue -------------------------------

def _raw(number, body, *labels):
    return {"number": number, "title": f"issue {number}", "body": body,
            "labels": [{"name": n} for n in ("status:ready", "type:proof",
                                             "difficulty:small", *labels)]}


def test_prepare_issues_places_an_issue_from_the_checkout(repo):
    body = ("## Pointers\n- `MathFin/FixedIncome/ZCB.lean`, `MathFin/FX/New.lean`\n\n"
            "Blueprint: thm:ho-lee\n")
    logs = []
    (issue,) = af.prepare_issues(PACK, [_raw(61, body, "area:valuation/term")],
                                 main_repo=repo, log=logs.append)
    assert issue["pointers"] == ["MathFin/Models/Rates/ZCB.lean"]
    assert issue["directory"] == "MathFin/Models/Rates"
    assert issue["benchmark"] == "benchmarks/interest_rates.json"
    assert issue["subjects"] == ["MSC 91G30", "JEL E43"]
    assert issue["blueprint"] == ["thm:ho-lee"] and issue["frontier"] == ["thm:ho-lee"]
    assert issue["map_anchors"] == ["MathFin.Phi", "MathFin.vasicekBondPrice"]
    assert "Hull" in issue["bibliography"] and "- Hull:" in issue["citable"]
    assert any("moved in the library, followed" in m for m in logs)
    assert any("dropped pointer(s)" in m and "missing" in m for m in logs)


def test_prepare_issues_skips_an_issue_the_corpus_already_serves(repo):
    logs = []
    out = af.prepare_issues(PACK, [_raw(66, "Blueprint: thm:ho-lee", "area:valuation/term")],
                            main_repo=repo, log=logs.append)
    assert out == [] and any("already holds a pipeline contribution" in m for m in logs)


def test_a_location_that_no_longer_resolves_is_not_honoured(repo):
    gone = _raw(7, "location: MathFin/Performance/Nowhere.lean\n", "area:valuation/term")
    moved = _raw(8, "location: MathFin/FixedIncome/Vasicek.lean\n", "area:valuation/term")
    a, b = af.prepare_issues(PACK, [gone, moved], main_repo=repo)
    assert a["location"] is None
    assert b["location"] == "MathFin/Models/Rates/Vasicek.lean"


def test_without_a_checkout_nothing_is_attached():
    (issue,) = af.prepare_issues(PACK, [_raw(9, "`MathFin/A/B.lean`", "area:fx")])
    assert issue["pointers"] == ["MathFin/A/B.lean"]
    for key in ("directory", "benchmark", "bibliography", "map_anchors", "location"):
        assert key not in issue


# --- the intent's contract with the library ----------------------------------------

_INTENT = {"statement": "s", "module_name": "HoLee", "benchmark_id": "mf-ho-lee",
           "docstring": "The Ho–Lee bond price is affine in the short rate.",
           "title": "The Ho–Lee bond price", "tags": ["ho-lee", "short rate"],
           "citation": {"key": "Hull", "locator": "§31.3"}}


def _prepared(repo):
    body = "## Pointers\n- `MathFin/Models/Rates/ZCB.lean`\n\nBlueprint: thm:ho-lee\n"
    return af.prepare_issues(PACK, [_raw(61, body, "area:valuation/term")], main_repo=repo)[0]


def test_the_intent_contract_names_what_the_library_would_refuse(repo):
    issue = _prepared(repo)
    assert af_drafting.intent_contract_reason(PACK, issue, _INTENT) is None
    bad_key = {**_INTENT, "citation": {"key": "Shreve"}}
    assert "not in the library's bibliography" in \
        af_drafting.intent_contract_reason(PACK, issue, bad_key)
    assert "missing 'citation'" in af_drafting.intent_contract_reason(
        PACK, issue, {k: v for k, v in _INTENT.items() if k != "citation"})
    taken = {**_INTENT, "benchmark_id": "mf-zcb-a"}
    assert "already an entry in the corpus" in \
        af_drafting.intent_contract_reason(PACK, issue, taken)
    # an issue nobody prepared against a checkout is held to no contract
    assert af_drafting.intent_contract_reason(PACK, {"number": 1}, {"benchmark_id": "x"}) is None


def test_draft_intent_rejects_a_reply_that_breaks_the_contract(repo):
    issue = _prepared(repo)
    reply = json.dumps({**_INTENT, "citation": {"key": "Nope"}})
    out = af_drafting.draft_intent(PACK, issue, "", chat_fn=lambda m: (reply, 5))
    assert not out["ok"] and "Nope" in out["reason"]
    ok = af_drafting.draft_intent(PACK, issue, "", chat_fn=lambda m: (json.dumps(_INTENT), 5))
    assert ok["ok"] and ok["intent"]["citation"]["key"] == "Hull"


def test_the_specifier_is_shown_the_bibliography_and_the_program_nodes(repo):
    issue = _prepared(repo)
    user = af.intent_messages(PACK, issue, "ctx")[1]["content"]
    assert "BIBLIOGRAPHY" in user and "- Hull: Hull, Options, Futures" in user
    assert "PROGRAM NODES" in user and "thm:ho-lee (frontier" in user
    plain = af.intent_messages(PACK, {"number": 1, "title": "t", "body": "b"}, "ctx")[1]["content"]
    assert "BIBLIOGRAPHY" not in plain and "PROGRAM NODES" not in plain


# --- the emitter, on a prepared issue ------------------------------------------------

_STUB = ("theorem hoLee_bond_affine (r T : ℝ) (hT : 0 < T) :\n"
         "    zcb r = zcb r := by sorry")


def test_emit_writes_what_the_library_gates(repo):
    issue = _prepared(repo)
    lean, entry, placement = af.emit_target_files(PACK, issue, _STUB, _INTENT)
    assert placement["main_module"] == "MathFin/Models/Rates/HoLee.lean"
    assert placement["benchmark"] == "benchmarks/interest_rates.json"
    assert "-- blueprint: thm:ho-lee" in lean
    assert "# The Ho–Lee bond price\n" in lean
    assert "## Main results\n\n* `hoLee_bond_affine`: The Ho–Lee bond price is affine" in lean
    assert "* [Hull, *Options, Futures, and Other Derivatives*][Hull], §31.3" in lean
    assert "ho-lee, short rate, MSC 91G30, JEL E43" in lean
    # the re-export imports the module by its own dotted name (a section is a path now)
    assert entry["code"]["lean"].startswith("import MathFin.Models.Rates.HoLee\n")
    meta = entry["metadata"]
    assert meta["citations"] == [{"key": "Hull", "locator": "§31.3"}]
    assert meta["reference"] == "Hull, Options, Futures, and Other Derivatives, §31.3"


def test_a_module_with_no_library_import_imports_the_conventions_module(repo):
    issue = {**_prepared(repo), "pointers": []}
    lean, _e, _p = af.emit_target_files(PACK, issue, _STUB, _INTENT)
    assert f"public import {PACK.layout.init_module}\n" in lean
    with_ptr, _e, _p = af.emit_target_files(PACK, _prepared(repo), _STUB, _INTENT)
    assert PACK.layout.init_module not in with_ptr


def test_the_queues_bookkeeping_does_not_ship_in_the_module(repo):
    issue = _prepared(repo)
    lean, _e, _p = af.emit_target_files(
        PACK, issue, _STUB, {**_INTENT, "deferred": ["a long fact " * 20]})
    assert any(len(ln) > lm.LINE_LIMIT for ln in lean.splitlines())   # the deferred header
    shipped = assemble.strip_placement_headers(lean + "-- decompose\n")
    assert "-- pointers:" not in shipped and "-- deferred:" not in shipped
    assert "-- blueprint:" not in shipped and "-- decompose" not in shipped
    assert not lm.long_lines(shipped)
    assert "\n\n\n" not in shipped
    assert "theorem hoLee_bond_affine" in shipped and "# The Ho–Lee bond price" in shipped


# --- reflow: a formatting edit that can be checked exactly ----------------------------

_LONG = ("theorem t (a b : ℝ) (h : 0 < a) : 0 < a + b * b := by\n"
         "  nlinarith [sq_nonneg (a - b), sq_nonneg (a + b), mul_pos h h, sq_nonneg a, "
         "sq_nonneg b, sq_abs a, sq_abs b]  -- certificate\n")
_FLOWED = ("theorem t (a b : ℝ) (h : 0 < a) : 0 < a + b * b := by\n"
           "  nlinarith [sq_nonneg (a - b), sq_nonneg (a + b), mul_pos h h, sq_nonneg a,\n"
           "    sq_nonneg b, sq_abs a, sq_abs b]\n")


def _reply(code):
    return lambda msgs: (f"```lean\n{code}```", 1)


def test_reflow_accepts_a_whitespace_only_edit_that_regates():
    out = af.reflow_candidate(PACK, _LONG, chat_fn=_reply(_FLOWED),
                              regate_fn=lambda c: {"passed": True})
    assert out["reflowed"] and out["long"] == [] and out["candidate"] == _FLOWED


def test_reflow_rejects_anything_that_touches_the_code():
    changed = _FLOWED.replace("sq_abs b", "sq_abs a")
    out = af.reflow_candidate(PACK, _LONG, chat_fn=_reply(changed),
                              regate_fn=lambda c: {"passed": True})
    assert not out["reflowed"] and out["candidate"] == _LONG and out["long"] == [2]


def test_reflow_keeps_the_original_when_the_regate_fails_or_the_model_raises():
    out = af.reflow_candidate(PACK, _LONG, chat_fn=_reply(_FLOWED),
                              regate_fn=lambda c: {"passed": False, "reason": "compile_or_sorry"})
    assert not out["reflowed"] and out["long"] == [2]

    def boom(msgs):
        raise RuntimeError("no auth")
    out = af.reflow_candidate(PACK, _LONG, chat_fn=boom, regate_fn=lambda c: {"passed": True})
    assert out["candidate"] == _LONG and out["long"] == [2]


def test_reflow_does_not_call_a_model_for_a_file_that_already_fits():
    def never(msgs):
        raise AssertionError("no call expected")
    out = af.reflow_candidate(PACK, _FLOWED, chat_fn=never, regate_fn=never)
    assert out == {"candidate": _FLOWED, "reflowed": False, "long": []}


# --- the map gate ---------------------------------------------------------------------

def test_the_map_gate_skips_without_a_map_and_reads_its_own_marker():
    skipped = af.map_rejection(PACK, "x", "t", [], check_fn=None)
    assert not skipped["off_map"]
    probe = af.map_probe(PACK, "theorem t : True := trivial", "t", ["MathFin.Phi", "A.b"])
    assert "`MathFin.t" in probe and "[`MathFin.Phi, `A.b]" in probe
    off = af.map_rejection(PACK, "x", "t", ["MathFin.Phi"], check_fn=lambda c: {
        "errors": ["map-gate: the statement reaches no declaration of the program map"]})
    assert off["off_map"]
    on = af.map_rejection(PACK, "x", "t", ["MathFin.Phi"], check_fn=lambda c: {"errors": []})
    assert not on["off_map"]
    # some OTHER error is not this gate's verdict, and a dead daemon is no verdict at all
    other = af.map_rejection(PACK, "x", "t", ["MathFin.Phi"],
                             check_fn=lambda c: {"errors": ["unknown identifier"]})
    assert not other["off_map"]
    dead = af.map_rejection(PACK, "x", "t", ["MathFin.Phi"], check_fn=lambda c: {"error": "EOF"})
    assert dead.get("indeterminate") and not dead["off_map"]


# --- issue state: the pipeline's own refs-PR is a claim ---------------------------------

def test_a_pipeline_pr_that_refs_an_issue_claims_it():
    rows = [{"number": 1, "title": "autoform: X (toward #61)", "body": "refs #61 (frontier)",
             "headRefName": "autoform/cal-bk-61"},
            {"number": 2, "title": "tidy", "body": "refs #70 in passing", "headRefName": "tidy"},
            {"number": 3, "title": "fix", "body": "closes #80", "headRefName": "fix"}]

    class R:
        returncode, stdout, stderr = 0, json.dumps(rows), ""
    assert issue_state.claimed_issue_numbers("o/r", run_fn=lambda a: R()) == {61, 80}


# --- issue state: a stub whose issue closed is no longer a claim -------------------------

def _gh_stub(open_nums, states, fail=()):
    """A `gh` stand-in: `issue list` returns `open_nums`, `issue view N` its state."""
    class R:
        def __init__(self, rc, out):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    def run(argv):
        if argv[1:3] == ["issue", "list"]:
            return R(1, "") if "list" in fail else R(0, json.dumps(
                [{"number": n} for n in open_nums]))
        if argv[1:3] == ["issue", "view"]:
            n = int(argv[3])
            return R(1, "") if n in fail else R(0, json.dumps({"state": states[n]}))
        raise AssertionError(argv)
    return run


def _queue(tmp_path, nums):
    q = str(tmp_path / "queue")
    os.makedirs(q)
    for n in nums:
        _write(q, f"cal-bk-{n}.lean", "theorem t : True := by sorry\n")
        _write(q, f"cal-bk-{n}.entry.json", "{}")
    _write(q, "manifest.json", json.dumps({"targets": [{"id": f"cal-bk-{n}"} for n in nums],
                                           "quarantined": []}))
    return q


def test_a_stub_whose_issue_closed_is_retired_with_its_manifest_row(tmp_path):
    q = _queue(tmp_path, [129, 61])
    run = _gh_stub(open_nums=[61], states={129: "CLOSED"})
    assert issue_state.retire_closed("o/r", q, run_fn=run) == [129]      # report only
    assert os.path.exists(os.path.join(q, "cal-bk-129.lean"))
    assert issue_state.retire_closed("o/r", q, apply=True, run_fn=run) == [129]
    assert sorted(os.listdir(q)) == ["cal-bk-61.entry.json", "cal-bk-61.lean", "manifest.json"]
    man = json.load(open(os.path.join(q, "manifest.json")))
    assert [t["id"] for t in man["targets"]] == ["cal-bk-61"]


def test_a_stub_is_never_retired_on_a_failed_lookup(tmp_path):
    q = _queue(tmp_path, [129])
    assert issue_state.retire_closed("o/r", q, apply=True,
                                     run_fn=_gh_stub([], {}, fail=("list",))) == []
    assert issue_state.retire_closed("o/r", q, apply=True,
                                     run_fn=_gh_stub([], {}, fail=(129,))) == []
    # absent from the open list is not enough either: the direct lookup must say CLOSED
    assert issue_state.retire_closed("o/r", q, apply=True,
                                     run_fn=_gh_stub([], {129: "OPEN"})) == []
    assert os.path.exists(os.path.join(q, "cal-bk-129.lean"))
