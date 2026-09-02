"""Byte-identical golden snapshot of every domain-flavoured rendering.

Runbook 02 (`formal-mathfin/docs/plans/2026-08-09-program-execution/02-foundry-domain-packs.md`)
has one acceptance criterion that matters: moving MathFin content out of `probe/`
and into `domains/mathfin/` must not change a single byte of what the pipeline
sends to a model or hands to Lean. This file pins BOTH surfaces under the current
code so the refactor is checkable rather than eyeballed.

Why both halves. A silently reworded prompt is an invisible regression — the
foundry's close-rate is tuned on these strings. A silently reshaped module
skeleton is an invisible regression the KERNEL GATES will blame on the prover.
The first version of the runbook scoped this test to prompts; that is the visible
surface but the wrong half of the risk.

The snapshot lives in `probe/golden/domain_pack_golden.json`. Regenerate ONLY when
a rendering change is intended:

    python3 probe/test_domain_pack_golden.py --write

and name the diff explicitly in the commit message. An unexplained snapshot bump
is exactly the regression this test exists to catch.

Hermetic: every pin/patterns-dependent prompt renders against a FIXTURE main repo
built in a temp dir, never the real checkout, so the snapshot does not drift when
formal-mathfin does.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

import af_gates
import af_parse
import af_prompts
import autoformalize
import decompose
import domain_pack
import house_context

PACK = domain_pack.load("mathfin")

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "golden", "domain_pack_golden.json")


# --- deterministic fixtures ---------------------------------------------------
#
# Fixed pins and a fixed patterns.md, so the snapshot is a function of the CODE
# only. The real repo's pins move; this test must not.

_TOOLCHAIN = "leanprover/lean4:v4.32.0"

_MANIFEST = {
    "packages": [
        {"name": "mathlib", "rev": "81a5d257deadbeefcafe0123456789abcdef0000"},
        {"name": "brownianmotion", "rev": "4d52fa77deadbeefcafe0123456789abcdef1111"},
    ]
}

# carries a `## Statement design` header so `build_drafter_prompt` exercises the
# LIVE slice; the fallback path is rendered separately below.
_PATTERNS = """\
# Patterns

## Tactic ladder

Try `grind` first.

## Statement design

- Name objects only from the shown declarations.
- Prefer the structural hypothesis.

## Something after

Trailing section, to prove the slice stops at the next `## `.
"""

_POINTER_MODULE = "MathFin/FixedIncome/ZCB.lean"

_POINTER_SRC = """\
namespace MathFin

/-- The zero-coupon bond price. -/
def zcb (r t T : ℝ) : ℝ := Real.exp (-(r * (T - t)))

theorem zcb_pos (r t T : ℝ) : 0 < zcb r t T := by positivity

end MathFin
"""

_STUB = """\
theorem zcb_le_one (r t T : ℝ) (hr : 0 ≤ r) (h : t ≤ T) :
    MathFin.zcb r t T ≤ 1 := by sorry"""

_ISSUE = {
    "number": 42,
    "title": "the zero-coupon bond price never exceeds par",
    "body": ("Task: show the ZCB price is at most 1 under a nonnegative short rate.\n"
             "Pointers: MathFin/FixedIncome/ZCB.lean, MathFin/Foundations/Basic.lean\n"),
    "area": "fixed-income",
    "difficulty": "medium",
    "pointers": [_POINTER_MODULE],
}

_META = {
    "module_name": "ZcbLeOne",
    "benchmark_id": "mf-fi-zcb-le-one",
    "docstring": "The zero-coupon bond price is at most par.",
    "deferred": [],
}

_INTENT = {
    "statement": "For a nonnegative short rate the ZCB price is at most 1.",
    "objects": ["MathFin.zcb"],
    "definitions": [{"name": "parYield", "signature": "ℝ → ℝ",
                     "meaning": "the par yield of a bond"}],
}


def _build_fixture_repo(root: str) -> str:
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    os.makedirs(os.path.join(root, os.path.dirname(_POINTER_MODULE)), exist_ok=True)
    with open(os.path.join(root, "lean-toolchain"), "w", encoding="utf-8") as f:
        f.write(_TOOLCHAIN + "\n")
    with open(os.path.join(root, "lake-manifest.json"), "w", encoding="utf-8") as f:
        json.dump(_MANIFEST, f)
    with open(os.path.join(root, "docs", "patterns.md"), "w", encoding="utf-8") as f:
        f.write(_PATTERNS)
    with open(os.path.join(root, _POINTER_MODULE), "w", encoding="utf-8") as f:
        f.write(_POINTER_SRC)
    return root


def _emit(issue_overrides: dict | None = None, meta_overrides: dict | None = None) -> str:
    """`emit_target_files`'s Lean module, as a labelled bundle of all three returns."""
    issue = dict(_ISSUE, **(issue_overrides or {}))
    meta = dict(_META, **(meta_overrides or {}))
    lean, entry, placement = autoformalize.emit_target_files(PACK, issue, _STUB, meta)
    return (lean
            + "\n--- entry.json ---\n"
            + json.dumps(entry, indent=2, ensure_ascii=False, sort_keys=True)
            + "\n--- placement ---\n"
            + json.dumps(placement, indent=2, ensure_ascii=False, sort_keys=True))


def renders(main_repo: str) -> dict[str, str]:
    """Every domain-flavoured string the pipeline produces, keyed by surface."""
    out: dict[str, str] = {}

    # ---- prompts: bare constants ----
    out["prompt/JUDGE_SYSTEM"] = PACK.prompt("judge-system")
    out["prompt/INTENT_SYSTEM"] = PACK.prompt("intent-system")
    out["prompt/FORMALIZE_SYSTEM"] = PACK.prompt("formalize-system")
    out["prompt/INTENT_DEFS_ADDENDUM"] = PACK.prompt("intent-defs-addendum")
    out["prompt/GOLF_SYSTEM"] = PACK.prompt("golf-system")
    out["prompt/_AGENTIC_PITFALLS"] = PACK.prompt("agentic-pitfalls")
    out["prompt/DECOMPOSE_SYSTEM"] = PACK.prompt("decompose-system")
    out["prompt/HOUSE_DOCTRINE"] = PACK.house_doctrine
    out["prompt/DRAFTER_STATEMENT_DESIGN_FALLBACK"] = PACK.statement_design_fallback

    # ---- prompts: gate-feedback instructions (every key, plus the default) ----
    for gate in sorted(PACK.gate_instructions):
        out[f"gate-instruction/{gate}"] = PACK.gate_instructions[gate]
    for gate in sorted(PACK.gate_instructions) + ["unknown-gate"]:
        out[f"gate-feedback/{gate}"] = af_prompts.render_gate_feedback(
            PACK, gate, "the gate's own verdict text", _STUB)
    out["gate-feedback/no-stub-no-detail"] = af_prompts.render_gate_feedback(
        PACK, "depth", "", None)

    # ---- prompts: assembled message lists ----
    out["messages/judge"] = json.dumps(
        af_prompts.judge_messages(PACK, _ISSUE, _STUB), indent=2, ensure_ascii=False)
    out["messages/judge-deferred"] = json.dumps(
        af_prompts.judge_messages(PACK, _ISSUE, _STUB,
                                  ["the convexity bound", "the par case"]),
        indent=2, ensure_ascii=False)

    # The drafter preamble used to be module-global mutable state (`_DRAFTER_PROMPT`,
    # set once at pipeline start); the pack refactor made it a parameter. Render BOTH
    # the unwired form (no preamble — what a caller that never wires it gets) and the
    # wired one, since the snapshot has to pin the join as well as the pieces.
    preamble = house_context.build_drafter_prompt(main_repo, PACK)
    out["messages/intent-unwired"] = json.dumps(
        af_prompts.intent_messages(PACK, _ISSUE, "CONTEXT PACK"),
        indent=2, ensure_ascii=False)
    out["prompt/_DRAFTER_PROMPT-wired"] = preamble
    out["messages/intent-theorem"] = json.dumps(
        af_prompts.intent_messages(PACK, _ISSUE, "CONTEXT PACK",
                                   drafter_preamble=preamble),
        indent=2, ensure_ascii=False)
    out["messages/intent-defs"] = json.dumps(
        af_prompts.intent_messages(PACK, _ISSUE, "CONTEXT PACK", route="defs",
                                   prior_unknowns=["MathFin.parYield"],
                                   drafter_preamble=preamble),
        indent=2, ensure_ascii=False)
    out["messages/intent-feedback"] = json.dumps(
        af_prompts.intent_messages(
            PACK, _ISSUE, "CONTEXT PACK",
            feedback=af_prompts.render_gate_feedback(PACK, "depth",
                                                     "consumed no pointer def", _STUB),
            prior_lessons="PRIOR TICKS: attempt 3 died fail_gate.",
            drafter_preamble=preamble),
        indent=2, ensure_ascii=False)

    # ---- prompts: house context assembled against the fixture repo ----
    out["house/build_system_prompt"] = house_context.build_system_prompt(main_repo, PACK)
    out["house/build_drafter_prompt"] = preamble
    # the fail-open half: no patterns.md at all => curated fallback, no patterns block
    empty = tempfile.mkdtemp(prefix="golden-nopatterns-")
    try:
        with open(os.path.join(empty, "lean-toolchain"), "w", encoding="utf-8") as f:
            f.write(_TOOLCHAIN + "\n")
        with open(os.path.join(empty, "lake-manifest.json"), "w", encoding="utf-8") as f:
            json.dump(_MANIFEST, f)
        out["house/build_system_prompt-no-patterns"] = \
            house_context.build_system_prompt(empty, PACK)
        out["house/build_drafter_prompt-no-patterns"] = \
            house_context.build_drafter_prompt(empty, PACK)
    finally:
        shutil.rmtree(empty, ignore_errors=True)
    # context pack: index absent => the regex fallback's header prose
    out["house/context-pack-regex"] = house_context.extract_signatures(
        main_repo, [_POINTER_MODULE], index_dir=os.path.join(main_repo, "no-such-index"))

    # ---- prompts: the agentic formalize task ----
    scaffold, _entry, _placement = autoformalize.emit_target_files(PACK, _ISSUE, _STUB, _META)
    out["prompt/agentic-formalize"] = autoformalize._agentic_formalize_prompt(
        PACK, _INTENT, scaffold, PACK.scratch_module,
        premises="zcb_pos : 0 < zcb r t T")
    out["config/lean-lsp-mcp"] = json.dumps(
        autoformalize._lean_lsp_mcp_config(PACK.lean_lsp_container),
        indent=2, sort_keys=True)
    out["config/agentic-scratch-rel"] = PACK.scratch_module

    # ---- emitted Lean: the module skeleton, every header branch ----
    out["emit/base"] = _emit()
    out["emit/append-location"] = _emit(issue_overrides={
        "body": _ISSUE["body"] + "location: MathFin/Performance/RatiosExtended.lean\n"})
    out["emit/deferred"] = _emit(meta_overrides={
        "deferred": ["the convexity bound", "the par case"]})
    out["emit/new-defs"] = _emit(meta_overrides={
        "definitions": ["MathFin.parYield"], "module_name": "ParYield"})
    out["emit/no-pointers"] = _emit(issue_overrides={"pointers": []})

    # ---- emitted Lean: the gate probes the kernel actually sees ----
    module = autoformalize.emit_target_files(PACK, _ISSUE, _STUB, _META)[0]
    out["gate-lean/depth_probe"] = af_gates.depth_probe(
        PACK, module, "zcb_le_one", [_POINTER_MODULE, "MathFin/Foundations/Basic.lean"])
    out["gate-lean/defs_probe"] = af_gates.defs_probe(
        PACK, module, "zcb_le_one", ["parYield", "parSpread"])
    out["gate-lean/triviality_goal"] = af_gates.triviality_goal(module)
    out["gate-lean/vacuity_goal"] = af_parse.vacuity_goal(module)
    out["gate-lean/disproof_goal"] = af_parse.disproof_goal(module)
    out["gate-lean/_TRIV_TACTIC"] = af_gates._TRIV_TACTIC
    out["gate-lean/_DEPTH_MARKER"] = af_gates._DEPTH_MARKER
    out["gate-lean/_mod_name"] = af_gates._mod_name(PACK, _POINTER_MODULE)

    # ---- emitted Lean: the decomposer's skeleton boilerplate ----
    out["decompose/module_text"] = decompose._module_text(
        PACK, [_POINTER_MODULE, "MathFin/Foundations/Basic.lean"],
        "theorem leaf_one : True := by sorry")
    out["decompose/_LICENSE"] = PACK.license

    # ---- splice round-trip: emit, then extract the core back out ----
    out["splice/extract-core"] = autoformalize._extract_core_stub(PACK, module)
    out["splice/extract-core-absent"] = autoformalize._extract_core_stub(
        PACK, "no markers here")

    # ---- pack-derived regexes and maps ----
    out["regex/_POINTER_RE"] = PACK.pointer_re.pattern
    out["regex/_LOCATION_RE"] = PACK.location_re.pattern
    out["regex/_MATHFIN_IMPORT_RE"] = PACK.import_re.pattern
    out["regex/pointers-found"] = json.dumps(
        autoformalize.extract_pointers(PACK, _ISSUE["body"]), ensure_ascii=False)
    out["regex/location-found"] = json.dumps(
        autoformalize.extract_location(
            PACK, _ISSUE["body"] + "location: MathFin/Performance/RatiosExtended.lean\n"),
        ensure_ascii=False)
    areas = sorted(PACK.areas) + ["not-a-mapped-area"]
    out["map/section_for_area"] = json.dumps(
        {a: PACK.section_for_area(a) for a in areas},
        indent=2, ensure_ascii=False, sort_keys=True)
    out["const/_LICENSE"] = PACK.license
    out["const/_BENCHMARK"] = PACK.benchmark
    out["const/_DOMAIN"] = PACK.domain

    return out


def _current() -> dict[str, str]:
    root = tempfile.mkdtemp(prefix="golden-mainrepo-")
    try:
        return renders(_build_fixture_repo(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_renderings_are_byte_identical_to_the_golden_snapshot():
    assert os.path.isfile(GOLDEN_PATH), (
        f"missing snapshot {GOLDEN_PATH} — generate it with "
        "`python3 probe/test_domain_pack_golden.py --write`")
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        expected = json.load(f)
    actual = _current()

    assert sorted(actual) == sorted(expected), (
        "the set of captured surfaces changed:\n"
        f"  added:   {sorted(set(actual) - set(expected))}\n"
        f"  removed: {sorted(set(expected) - set(actual))}")

    drifted = [k for k in sorted(expected) if actual[k] != expected[k]]
    if drifted:
        k = drifted[0]
        raise AssertionError(
            f"{len(drifted)} rendering(s) drifted from the golden snapshot: {drifted}\n\n"
            f"first drift — {k}\n"
            f"--- expected ---\n{expected[k]}\n"
            f"--- actual ---\n{actual[k]}")


if __name__ == "__main__":
    if "--write" in sys.argv:
        os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
        snapshot = _current()
        with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")
        print(f"wrote {GOLDEN_PATH} ({len(snapshot)} surfaces)")
    else:
        print("usage: python3 probe/test_domain_pack_golden.py --write")
