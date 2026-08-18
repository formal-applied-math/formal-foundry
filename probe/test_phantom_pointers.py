"""An issue naming a module to CREATE must not have it imported as one to CONSUME.

Reproduces the live failure. `formal-mathfin#71`'s Pointers section reads

    - New module suggestion: `MathFin/Portfolio/ArbitragePricingTheory.lean`.

The pointer regex cannot tell "consume this" from "create this", so emit adds
`public import MathFin.Portfolio.ArbitragePricingTheory` for a file that does not
exist, the drafter names its new module the same thing, and emit's own collision
check refuses — correctly, since a module cannot import itself.

It failed that way SEVEN times across ticks and appeared in zero obstruction
reports, because an `error` outcome classified as None and was dropped from the
census. Both halves are fixed; both are pinned here.
"""
from __future__ import annotations

import os
import tempfile

import autoformalize as af
import domain_pack
import obstructions
from af_routing import route_for

PACK = domain_pack.load("mathfin")

# the real shape, verbatim in the part that matters
_ISSUE_71_BODY = """\
## Task
Derive the APT exact-factor pricing relation from no-arbitrage.

## Pointers
- New module suggestion: `MathFin/Portfolio/ArbitragePricingTheory.lean`.
- `MathFin/Portfolio/MeanVariance.lean` for the existing portfolio algebra.
"""


def _raw(number: int, body: str) -> list[dict]:
    return [{"number": number, "title": "t", "body": body,
             "labels": [{"name": "status:ready"}, {"name": "type:proof"},
                        {"name": "difficulty:medium"}, {"name": "area:portfolio"}]}]


def _repo_with(*existing: str) -> str:
    d = tempfile.mkdtemp(prefix="phantom-")
    for rel in existing:
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write("-- real module\n")
    return d


def test_without_a_checkout_nothing_is_dropped():
    """`main_repo=None` keeps the old behaviour exactly — extraction is parsing and
    stays pure, which is also what keeps the golden snapshot valid."""
    got = af.prepare_issues(PACK, _raw(71, _ISSUE_71_BODY))
    assert got[0]["pointers"] == ["MathFin/Portfolio/ArbitragePricingTheory.lean",
                                  "MathFin/Portfolio/MeanVariance.lean"]


def test_a_pointer_that_is_not_in_the_checkout_is_dropped():
    repo = _repo_with("MathFin/Portfolio/MeanVariance.lean")
    got = af.prepare_issues(PACK, _raw(71, _ISSUE_71_BODY), main_repo=repo)
    assert got[0]["pointers"] == ["MathFin/Portfolio/MeanVariance.lean"], \
        "the suggested-new module must not survive as a pointer to import"


def test_the_drop_is_logged_not_silent():
    """Silently dropping a pointer hides a mis-written issue exactly as well as
    silently keeping one did."""
    repo = _repo_with("MathFin/Portfolio/MeanVariance.lean")
    said: list[str] = []
    af.prepare_issues(PACK, _raw(71, _ISSUE_71_BODY), main_repo=repo, log=said.append)
    assert any("ArbitragePricingTheory" in m for m in said), said
    assert any("CONSUME" in m for m in said), said


def test_an_all_phantom_issue_routes_to_defs_and_skips_the_depth_gate():
    """The downstream story, which is what makes this a fix and not just a filter:
    an issue proposing a new module has no consumable pointer, so it IS a defs-route
    issue, and the pointers-scoped depth gate correctly has nothing to scope to."""
    body = "## Pointers\n- New module suggestion: `MathFin/Portfolio/Apt.lean`.\n"
    repo = _repo_with()                      # nothing exists
    got = af.prepare_issues(PACK, _raw(71, body), main_repo=repo)
    assert got[0]["pointers"] == []
    assert route_for({"number": 71}, def_count=0, family=None) == "defs"

    skipped = af.depth_rejection(PACK, "theorem t : True := by sorry", "t", [],
                                 check_fn=lambda _c: {"errors": []})
    assert skipped["shallow"] is False and "skipped" in skipped["verdict"]


def test_emit_no_longer_collides_once_the_phantom_is_gone():
    """The actual live failure: emit raised ValueError because its own main-module
    was in the pointer list."""
    repo = _repo_with("MathFin/Portfolio/MeanVariance.lean")
    issue = af.prepare_issues(PACK, _raw(71, _ISSUE_71_BODY), main_repo=repo)[0]
    issue["area"] = "portfolio"
    meta = {"module_name": "ArbitragePricingTheory", "benchmark_id": "mf-portfolio-apt",
            "docstring": "APT.", "deferred": []}
    lean, _entry, placement = af.emit_target_files(
        PACK, issue, "theorem apt (x : ℝ) : x = x := by sorry", meta)
    assert placement["main_module"] == "MathFin/Portfolio/ArbitragePricingTheory.lean"
    assert "public import MathFin.Portfolio.ArbitragePricingTheory" not in lean, \
        "the module must not import itself"
    assert "public import MathFin.Portfolio.MeanVariance" in lean


def test_emit_still_collides_when_the_pointer_is_real():
    """The guard must keep firing when it should: a pointer that genuinely exists
    and matches the new module's name is still a self-import."""
    repo = _repo_with("MathFin/Portfolio/ArbitragePricingTheory.lean")
    issue = af.prepare_issues(PACK, _raw(71, _ISSUE_71_BODY), main_repo=repo)[0]
    issue["area"] = "portfolio"
    meta = {"module_name": "ArbitragePricingTheory", "benchmark_id": "x",
            "docstring": "d", "deferred": []}
    try:
        af.emit_target_files(PACK, issue, "theorem apt : True := by sorry", meta)
        raise AssertionError("expected the self-import collision to be refused")
    except ValueError as e:
        assert "collides with a pointer import" in str(e)


# --- the other half: the census must not drop what it cannot name --------------

def test_an_unrecognised_outcome_is_counted_not_dropped():
    rows = [{"issue": 71, "outcome": "error", "history": [{"gate": "error"}]}]
    b = obstructions.bucket_obstructions(rows, [])
    assert b["drafter-error"]["count"] == 1
    assert b["drafter-error"]["issues"]["71"] == 1
    assert sum(f["count"] for f in b.values()) == 1, "counted exactly once"


def test_a_seeded_record_is_still_not_an_obstruction():
    """The fix must not turn successes into obstructions."""
    b = obstructions.bucket_obstructions([{"issue": 9, "outcome": "seeded"}], [])
    assert sum(f["count"] for f in b.values()) == 0


def test_recognised_families_are_unchanged():
    """The new fallback must only catch what previously vanished."""
    b = obstructions.bucket_obstructions(
        [{"issue": 53, "outcome": "depth", "history": [{"gate": "depth"}]},
         {"issue": 61, "outcome": "formalize", "history": [{"gate": "formalize"}]}], [])
    assert b["depth-gate"]["count"] == 1
    assert b["no-elaborating-draft"]["count"] == 1
    assert b["drafter-error"]["count"] == 0
