"""The pack contract, exercised by a SECOND domain.

Runbook 02's golden test proves the extraction changed nothing for mathfin. That is
a regression check, and it cannot tell you the contract generalizes — a pack shape
that only ever describes one library is a rename, not an abstraction. This file is
the other half: it loads `domains/econometrics/` and asserts the things a second
domain has to get right, against the shape its real sources actually have.

It found the field runbook 02 guessed. `formal-econometrics` opens BEFORE
`namespace` (PotentialOutcomes.lean:43-45); the flagship opens after it
(Girsanov.lean:50-51). The contract grew `opens_before_namespace` rather than the
emitter growing a special case — which is what runbook 06 step 1 says to do.

Hermetic and daemon-free: nothing here needs Lean, a key, or the target checkout.
"""
from __future__ import annotations

import re

import af_gates
import autoformalize
import domain_pack

MATHFIN = domain_pack.load("mathfin")
ECON = domain_pack.load("econometrics")

_STUB = ("theorem ovb_decomposition {Ω : Type*} [MeasurableSpace Ω]\n"
         "    (μ : Measure Ω) (s : Set Ω) (f g : Ω → ℝ) :\n"
         "    Econometrics.condMean μ s (f + g)\n"
         "      = Econometrics.condMean μ s f + Econometrics.condMean μ s g := by sorry")

_ISSUE = {
    "number": 1,
    "title": "omitted-variable bias: the short regression's slope decomposes",
    "body": ("Task: state the omitted-variable-bias decomposition.\n"
             "Pointers: Econometrics/Identification/PotentialOutcomes.lean\n"),
    "area": "identification",
    "difficulty": "medium",
    "pointers": ["Econometrics/Identification/PotentialOutcomes.lean"],
}

_META = {
    "module_name": "OmittedVariableBias",
    "benchmark_id": "id-identification-ovb",
    "docstring": "The short regression's slope is the long slope plus the bias term.",
    "deferred": [],
}


def _emit() -> str:
    return autoformalize.emit_target_files(ECON, _ISSUE, _STUB, _META)[0]


def test_the_second_pack_loads_and_renders_every_placeholder():
    """An unrendered `{{namespace}}` would reach a model verbatim. The loader raises
    on an UNKNOWN placeholder, but a placeholder it knows and leaves in place would
    be silent, so check the rendered text directly."""
    rendered = ([ECON.prompt(k) for k in ECON.prompts]
                + list(ECON.gate_instructions.values())
                + [ECON.house_doctrine, ECON.statement_design_fallback])
    leftovers = [t[:120] for t in rendered if re.search(r"\{\{\w+\}\}", t)]
    assert not leftovers, f"unrendered placeholders reached the prompt: {leftovers}"


def test_the_two_packs_do_not_share_an_identity():
    """The failure this whole refactor exists to prevent: a second domain silently
    inheriting the first's namespace."""
    assert ECON.namespace == "Econometrics" != MATHFIN.namespace
    assert ECON.slug != MATHFIN.slug
    assert ECON.benchmark != MATHFIN.benchmark
    assert ECON.domain != MATHFIN.domain
    assert ECON.lean_lsp_container != MATHFIN.lean_lsp_container
    assert ECON.verify_image != MATHFIN.verify_image
    assert ECON.scratch_module != MATHFIN.scratch_module


def test_the_second_domain_has_no_flagship_prose_left_in_it():
    """The extraction tokenized names first and worked EXAMPLES only on a second
    pass. A pack telling an econometrician not to re-derive the bond price is the
    exact half-done state that pass was for."""
    blob = "\n".join([ECON.house_doctrine, ECON.statement_design_fallback]
                     + [ECON.prompt(k) for k in ECON.prompts]
                     + list(ECON.gate_instructions.values()))
    for term in ("MathFin", "bond", "zero-coupon", "Black-Scholes", "payoff", "CVaR",
                 "BrownianMotion", "mathematical-finance"):
        assert term not in blob, f"flagship prose survived into the econometrics pack: {term!r}"


def test_the_preamble_matches_the_real_library_not_the_flagship():
    """`opens_before_namespace` is the field runbook 02 guessed. Econometrics opens
    before `namespace` (PotentialOutcomes.lean:43-45); mathfin opens after
    (Girsanov.lean:50-51). Both orders must come out of the same code path."""
    econ = ECON.module_preamble()
    assert econ.index("open MeasureTheory") < econ.index("namespace Econometrics")

    mf = MATHFIN.module_preamble()
    assert mf.index("namespace MathFin") < mf.index("open MeasureTheory")

    # the decomposer's skeleton carries no opens, in either domain
    assert "open " not in ECON.module_preamble(opens=False)
    assert "open " not in MATHFIN.module_preamble(opens=False)


def test_the_splice_anchor_is_derived_so_emit_and_read_cannot_disagree():
    """The anchor is the last preamble line, not a stored field — which is the only
    way a pack cannot declare one shape and parse another. The round-trip is the
    proof: emit a module, read the body back, get the stub."""
    assert ECON.splice_anchor == "namespace Econometrics"
    assert MATHFIN.splice_anchor == "open scoped NNReal ENNReal"

    for pack in (ECON, MATHFIN):
        assert pack.splice_anchor == pack.module_preamble().splitlines()[-1]

    assert autoformalize._extract_core_stub(ECON, _emit()).strip() == _STUB.strip()


def test_the_emitted_module_is_shaped_like_the_target_library():
    lean = _emit()
    assert lean.startswith("/-\nCopyright (c) 2026 Raphael Coelho")
    assert "\nmodule\n" in lean
    assert "public import Econometrics.Identification.PotentialOutcomes" in lean
    assert "-- benchmark: benchmarks/identification.json" in lean
    assert "-- main-module: Econometrics/Identification/OmittedVariableBias.lean" in lean
    assert lean.rstrip().endswith("end Econometrics")
    assert "MathFin" not in lean


def test_the_gate_meta_blocks_name_the_second_namespace():
    """The five namespace-keyed GENERATED-code sites are the ones a prompt
    extraction would have left behind. If any still said `MathFin`, the gate would
    look up a declaration that does not exist and reject every draft."""
    lean = _emit()
    depth = af_gates.depth_probe(ECON, lean, "ovb_decomposition",
                                 ["Econometrics/Identification/PotentialOutcomes.lean"])
    assert "env.find? `Econometrics.ovb_decomposition" in depth
    assert "`Econometrics.Identification.PotentialOutcomes" in depth
    assert "MathFin" not in depth

    defs = af_gates.defs_probe(ECON, lean, "ovb_decomposition", ["shortSlope"])
    assert "`Econometrics.shortSlope" in defs
    assert "MathFin" not in defs


def test_the_issue_parsers_are_keyed_on_the_second_lake_root():
    assert autoformalize.extract_pointers(ECON, _ISSUE["body"]) == [
        "Econometrics/Identification/PotentialOutcomes.lean"]
    # a flagship path must NOT be picked up by the econometrics pack
    assert autoformalize.extract_pointers(ECON, "MathFin/FixedIncome/ZCB.lean") == []
    assert autoformalize.extract_location(
        ECON, "location: Econometrics/Identification/DiD.lean\n"
    ) == "Econometrics/Identification/DiD.lean"


def test_areas_are_not_pre_populated_with_empty_sections():
    """Runbook 06's kill criterion: the depth gate requires the candidate to USE a
    constant defined in its pointer modules. An area mapping to a directory that
    does not exist yet gives it nothing to consume, and the gate must not be
    weakened to make that pass."""
    assert set(ECON.areas) == {"identification"}
