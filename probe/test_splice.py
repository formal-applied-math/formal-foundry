"""Tests for splicing a contribution into an EXISTING module (backlog S)."""
from __future__ import annotations

import json
import os

import assemble as A

import domain_pack

PACK = domain_pack.load("mathfin")


_EXISTING = """/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
-/
module

public import Mathlib
public import MathFin.Performance.Ratios

/-!
# Extended performance ratios
-/

@[expose] public section

namespace MathFin

open Real

/-- Sortino. -/
noncomputable def sortinoRatio (a b c : ℝ) : ℝ := (a - b) / c

end MathFin
"""

_CANDIDATE = """/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
-/
module

public import Mathlib
public import MathFin.Foundations.Extra

/-!
Gain-to-pain ratio.
-/

set_option autoImplicit false

@[expose] public section

namespace MathFin

/-- Gain-to-pain. -/
noncomputable def gainToPain (s : Finset a) (r : a -> ℝ) : ℝ := 0

theorem gainToPain_nonneg (s : Finset a) (r : a -> ℝ) : 0 <= gainToPain s r := by
  simp [gainToPain]

end MathFin
"""


def test_splice_keeps_the_existing_declarations():
    out = A.splice_into_module(PACK, _EXISTING, _CANDIDATE)
    assert "sortinoRatio" in out
    assert "open Real" in out


def test_splice_adds_the_new_declarations_before_end_namespace():
    out = A.splice_into_module(PACK, _EXISTING, _CANDIDATE)
    assert "gainToPain_nonneg" in out
    assert out.rstrip().endswith("end MathFin")
    assert out.count("end MathFin") == 1
    assert out.index("sortinoRatio") < out.index("gainToPain")


def test_splice_does_not_duplicate_the_module_scaffold():
    out = A.splice_into_module(PACK, _EXISTING, _CANDIDATE)
    assert out.count("@[expose] public section") == 1
    assert out.count("namespace MathFin") == 1
    assert out.count("Copyright") == 1
    assert out.count("public import Mathlib") == 1


def test_splice_carries_over_imports_the_candidate_needs():
    out = A.splice_into_module(PACK, _EXISTING, _CANDIDATE)
    assert "public import MathFin.Foundations.Extra" in out
    # and keeps the existing one
    assert "public import MathFin.Performance.Ratios" in out
    # imports stay in the header, above the module docstring
    assert out.index("MathFin.Foundations.Extra") < out.index("/-!")


def test_splice_refuses_a_candidate_it_cannot_parse():
    assert A.splice_into_module(PACK, _EXISTING, "theorem foo : True := trivial\n") is None
    assert A.splice_into_module(PACK, "not a module", _CANDIDATE) is None


def test_apply_contribution_still_refuses_an_accidental_clobber(tmp_path):
    main = str(tmp_path)
    os.makedirs(os.path.join(main, "MathFin", "Performance"))
    mod = os.path.join(main, "MathFin", "Performance", "RatiosExtended.lean")
    open(mod, "w").write(_EXISTING)
    open(os.path.join(main, "b.json"), "w").write('{"theorems": []}')
    target = {"main_module": "MathFin/Performance/RatiosExtended.lean",
              "benchmark": "b.json"}
    try:
        A.apply_contribution(PACK, _CANDIDATE, target, {"id": "x"}, main)
        assert False, "expected a refusal"
    except FileExistsError:
        pass


def test_apply_contribution_appends_when_the_target_says_so(tmp_path):
    main = str(tmp_path)
    os.makedirs(os.path.join(main, "MathFin", "Performance"))
    mod = os.path.join(main, "MathFin", "Performance", "RatiosExtended.lean")
    open(mod, "w").write(_EXISTING)
    open(os.path.join(main, "b.json"), "w").write('{"theorems": []}\n')
    target = {"main_module": "MathFin/Performance/RatiosExtended.lean",
              "benchmark": "b.json", "append": True}
    written = A.apply_contribution(PACK, _CANDIDATE, target, {"id": "x"}, main)
    text = open(mod).read()
    assert "sortinoRatio" in text and "gainToPain_nonneg" in text
    assert text.count("namespace MathFin") == 1
    assert "MathFin/Performance/RatiosExtended.lean" in written


def test_append_to_a_missing_module_falls_back_to_creating_it(tmp_path):
    main = str(tmp_path)
    os.makedirs(os.path.join(main, "MathFin", "Performance"))
    open(os.path.join(main, "b.json"), "w").write('{"theorems": []}\n')
    target = {"main_module": "MathFin/Performance/New.lean",
              "benchmark": "b.json", "append": True}
    A.apply_contribution(PACK, _CANDIDATE, target, {"id": "x"}, main)
    text = open(os.path.join(main, "MathFin", "Performance", "New.lean")).read()
    assert text == _CANDIDATE


def test_unspliceable_append_aborts_rather_than_clobbering(tmp_path):
    main = str(tmp_path)
    os.makedirs(os.path.join(main, "MathFin", "Performance"))
    mod = os.path.join(main, "MathFin", "Performance", "RatiosExtended.lean")
    open(mod, "w").write("garbage, not a module\n")
    open(os.path.join(main, "b.json"), "w").write('{"theorems": []}\n')
    target = {"main_module": "MathFin/Performance/RatiosExtended.lean",
              "benchmark": "b.json", "append": True}
    try:
        A.apply_contribution(PACK, _CANDIDATE, target, {"id": "x"}, main)
        assert False, "expected a refusal"
    except ValueError:
        pass
    assert open(mod).read() == "garbage, not a module\n"   # untouched


# --- emit-side: the issue's `location:` decides placement (backlog S) ----------

import autoformalize as af

_ISSUE_BODY = """add the gain-to-pain ratio to MathFin/Performance (schwager).

location: MathFin/Performance/RatiosExtended.lean (beside sortinoRatio /
informationRatio), re-export from the MathFin umbrella
"""


def test_extract_location_reads_the_issue_directive():
    assert af.extract_location(PACK, _ISSUE_BODY) == "MathFin/Performance/RatiosExtended.lean"


def test_extract_location_is_none_without_one():
    assert af.extract_location(PACK, "## Task\nprove a thing\n") is None
    assert af.extract_location(PACK, "") is None


def test_extract_location_ignores_a_bare_pointer_mention():
    # a Pointers section names modules too; only an explicit `location:` counts
    body = "## Pointers\n- `MathFin/FixedIncome/ZCB.lean` (zcb discount factors)\n"
    assert af.extract_location(PACK, body) is None


def test_emit_places_the_contribution_where_the_issue_said():
    issue = {"number": 161, "title": "gain-to-pain", "area": "performance",
             "body": _ISSUE_BODY, "pointers": ["MathFin/Performance/RatiosExtended.lean"],
             "difficulty": "good-first"}
    meta = {"benchmark_id": "mf-performance-gain_to_pain", "module_name": "GainToPain",
            "docstring": "Gain-to-pain.", "definitions": ["gainToPain"],
            "theorem_name": "gainToPain_nonneg"}
    _lean, _entry, placement = af.emit_target_files(PACK,
        issue, "theorem gainToPain_nonneg (s : Finset a) : True := by sorry\n", meta)
    assert placement["main_module"] == "MathFin/Performance/RatiosExtended.lean"
    assert placement["append"] is True


def test_emit_still_mints_a_module_when_the_issue_names_no_location():
    issue = {"number": 161, "title": "gain-to-pain", "area": "performance",
             "body": "no directive here", "pointers": [], "difficulty": "good-first"}
    meta = {"benchmark_id": "x", "module_name": "GainToPain", "docstring": "d",
            "definitions": ["gainToPain"], "theorem_name": "gainToPain_nonneg"}
    _l, _e, placement = af.emit_target_files(PACK,
        issue, "theorem gainToPain_nonneg (s : Finset a) : True := by sorry\n", meta)
    assert placement["main_module"] == "MathFin/Performance/GainToPain.lean"
    assert placement["append"] is False


def test_append_rides_the_stub_header_into_the_manifest():
    import build_manifest as B
    issue = {"number": 161, "title": "t", "area": "performance", "body": _ISSUE_BODY,
             "pointers": [], "difficulty": "good-first"}
    meta = {"benchmark_id": "x", "module_name": "GainToPain", "docstring": "d",
            "definitions": ["gainToPain"], "theorem_name": "gainToPain_nonneg"}
    lean, _e, placement = af.emit_target_files(PACK,
        issue, "theorem gainToPain_nonneg (s : Finset a) : True := by sorry\n", meta)
    assert "-- append: true" in lean
    assert B.parse_meta(lean)["append"] is True
    assert B.parse_meta(lean)["main_module"] == placement["main_module"]


def test_no_append_header_when_the_module_is_new():
    import build_manifest as B
    issue = {"number": 161, "title": "t", "area": "performance", "body": "no directive",
             "pointers": [], "difficulty": "good-first"}
    meta = {"benchmark_id": "x", "module_name": "GainToPain", "docstring": "d",
            "definitions": ["gainToPain"], "theorem_name": "gainToPain_nonneg"}
    lean, _e, _p = af.emit_target_files(PACK,
        issue, "theorem gainToPain_nonneg (s : Finset a) : True := by sorry\n", meta)
    assert "-- append:" not in lean
    assert B.parse_meta(lean).get("append") in (None, False)


# --- provenance sanitizer (backlog U) -----------------------------------------

def _entry(src, model):
    return {"id": "x", "metadata": {"provenance": {
        "statement_source": src, "statement_model": model,
        "source": "leanstral-autoform", "model": "labs-leanstral-1-5", "issue": 161}}}


def test_sanitizer_strips_a_retired_drafter():
    out, changed = A.sanitize_provenance(_entry("magistral-autoform", "magistral-medium"))
    p = out["metadata"]["provenance"]
    assert p["statement_source"] == "autoform" and p["statement_model"] == "autoform"
    assert changed == ["statement_source", "statement_model"]


def test_sanitizer_leaves_the_prover_credited():
    out, _ = A.sanitize_provenance(_entry("magistral-autoform", "magistral-medium"))
    p = out["metadata"]["provenance"]
    assert p["source"] == "leanstral-autoform" and p["model"] == "labs-leanstral-1-5"
    assert p["issue"] == 161


def test_sanitizer_is_a_noop_on_a_current_entry():
    e = _entry("autoform", "autoform")
    out, changed = A.sanitize_provenance(e)
    assert changed == [] and out == e


def test_sanitizer_does_not_mutate_its_input():
    e = _entry("magistral-autoform", "magistral-medium")
    A.sanitize_provenance(e)
    assert e["metadata"]["provenance"]["statement_source"] == "magistral-autoform"


def test_sanitizer_tolerates_an_entry_without_provenance():
    out, changed = A.sanitize_provenance({"id": "x"})
    assert changed == [] and out == {"id": "x"}


def test_apply_contribution_scrubs_before_the_entry_lands(tmp_path):
    main = str(tmp_path)
    os.makedirs(os.path.join(main, "MathFin", "Performance"))
    open(os.path.join(main, "b.json"), "w").write('{"theorems": []}\n')
    target = {"main_module": "MathFin/Performance/New.lean", "benchmark": "b.json"}
    A.apply_contribution(PACK, _CANDIDATE, target,
                         _entry("magistral-autoform", "magistral-medium"), main)
    got = json.load(open(os.path.join(main, "b.json")))["theorems"][0]
    assert "magistral" not in json.dumps(got)


def test_sanitizer_also_scrubs_the_prose_claim():
    e = {"id": "x", "metadata": {"formalization_scope":
         "Full formal proof in M.lean (magistral-drafted statement, leanstral proof)."}}
    out, changed = A.sanitize_provenance(e)
    scope = out["metadata"]["formalization_scope"]
    assert "magistral" not in scope and "autoformalized statement" in scope
    assert "leanstral proof" in scope          # the prover stays named
    assert "formalization_scope" in changed


def test_no_queued_target_still_names_a_retired_drafter():
    # the queue is the enqueue-time record; nothing in it should assert a drafter that
    # has left the pipeline, or a re-pick would emit that claim (backlog U)
    import glob
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    stale = []
    for f in glob.glob(os.path.join(root, "targets", "queue", "*.entry.json")):
        if any(d in open(f, encoding="utf-8").read().lower()
               for d in A._RETIRED_DRAFTERS):
            stale.append(os.path.basename(f))
    assert stale == [], f"queued entries still name a retired drafter: {stale}"
