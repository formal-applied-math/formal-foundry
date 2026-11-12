"""The map gate on REAL Lean, on the live lean-repl daemon.

`af_gates.map_probe` is a Lean meta block built as a Python string. Its verdict is read
from one marked error, so a block that no longer elaborates (a core API renamed at a pin
bump, a slip in the string) reports no marked error at all, and the gate would pass every
statement in silence. This file is what stands between that and a tick: it elaborates the
probe against the library and checks both directions, and that nothing else errors.

The stubs are stated through the pack's canary fixture (`domains/<name>/smoke/`), which
names a module of the library and a program object in it.

Runs against the daemon on 127.0.0.1:7878 and skips when it is not serving, like
`test_instance_probe_daemon.py`; the tick runs both before any token is spent.
"""
from __future__ import annotations

import json
import os
import re

import pytest

import af_gates
import domain_pack
from probe import daemon_check

PACK = domain_pack.load(os.environ.get("DOMAIN", "mathfin"))
_SMOKE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "domains", PACK.name, "smoke")


def _fixture() -> tuple[str, str]:
    """(module to import, the program object its theorem is stated through)."""
    with open(os.path.join(_SMOKE, "smoke.json"), encoding="utf-8") as f:
        body = json.load(f)["issue"]["body"]
    pointer = PACK.pointer_re.search(body).group(0)
    with open(os.path.join(_SMOKE, "body.lean"), encoding="utf-8") as f:
        obj = re.search(r"theorem\s+(\w+?)_", f.read()).group(1)
    return pointer[:-len(".lean")].replace("/", "."), obj


def _daemon_up() -> bool:
    return daemon_check("example : True := trivial", timeout=60).get("success") is True


pytestmark = pytest.mark.skipif(
    not _daemon_up(), reason="lean-repl daemon not serving on 127.0.0.1:7878")

_INFRA = ("timed out", "repl killed", "timeout")


def _errors(code: str) -> list[str]:
    r = daemon_check(code, timeout=360)
    if r.get("error"):
        pytest.skip(f"daemon check did not complete (infra): {r['error']}")
    errs = [str(e) for e in (r.get("errors") or [])]
    if any(m in " ".join(errs).lower() for m in _INFRA):
        pytest.skip(f"daemon elaboration timed out (infra): {errs}")
    return errs


def _stub(decls: str) -> str:
    module, _ = _fixture()
    return f"import {module}\n\nnamespace {PACK.namespace}\n\n{decls}\nend {PACK.namespace}\n"


def test_a_statement_through_a_program_object_is_on_the_map():
    _, obj = _fixture()
    stub = _stub(f"theorem t_on (x : ℝ) : {obj} x = {obj} x := by sorry\n")
    errs = _errors(af_gates.map_probe(PACK, stub, "t_on", [PACK.qualified(obj)]))
    assert errs == [], errs            # no marked error, and no OTHER error either


def test_a_statement_over_raw_numbers_is_off_the_map():
    _, obj = _fixture()
    stub = _stub("theorem t_off (x : ℝ) : x + 0 = x := by sorry\n")
    errs = _errors(af_gates.map_probe(PACK, stub, "t_off", [PACK.qualified(obj)]))
    assert errs and all(af_gates._MAP_MARKER in e for e in errs), errs


def test_an_upstream_declaration_is_never_an_anchor():
    """The library counts dependence on its own constants only. A statement that touches
    the map at an upstream declaration alone is off it, whatever the anchor list says."""
    stub = _stub("theorem t_up (x : ℝ) : Real.exp x = Real.exp x := by sorry\n")
    errs = _errors(af_gates.map_probe(PACK, stub, "t_up", ["Real.exp"]))
    assert errs and all(af_gates._MAP_MARKER in e for e in errs), errs


def test_a_theorem_on_the_way_counts_through_its_proof():
    """`viaProof` is a number; it reaches the program object only through the PROOF of the
    lemma its body cites. The library's index draws that edge, so the gate does too."""
    _, obj = _fixture()
    stub = _stub(
        f"theorem usesObj : True := by\n  have _h : {obj} 0 = {obj} 0 := rfl\n  trivial\n\n"
        "def viaProof : ℕ := (fun (_ : True) => 0) usesObj\n\n"
        "theorem t_via : viaProof = viaProof := by sorry\n")
    errs = _errors(af_gates.map_probe(PACK, stub, "t_via", [PACK.qualified(obj)]))
    assert errs == [], errs


def test_a_statement_through_an_unfounded_price_is_off_the_trunk():
    """The fixture's object, listed as a price with a functional it does not reach: the
    walk reports the trunk marker alone (the statement is on the map)."""
    _, obj = _fixture()
    stub = _stub(f"theorem t_price (x : ℝ) : {obj} x = {obj} x := by sorry\n")
    trunk = {"trunk": [f"{PACK.namespace}.Market.price"], "prices": [PACK.qualified(obj)]}
    errs = _errors(af_gates.map_probe(PACK, stub, "t_price", [PACK.qualified(obj)], trunk))
    assert errs and all(af_gates._TRUNK_MARKER in e for e in errs), errs


def test_a_price_founded_on_the_functional_passes():
    _, obj = _fixture()
    stub = _stub(f"theorem t_founded (x : ℝ) : {obj} x = {obj} x := by sorry\n")
    trunk = {"trunk": [PACK.qualified(obj)], "prices": [PACK.qualified(obj)]}
    errs = _errors(af_gates.map_probe(PACK, stub, "t_founded", [PACK.qualified(obj)], trunk))
    assert errs == [], errs


def test_a_proof_through_an_unfounded_price_is_caught_through_the_proof():
    """The statement names no price; the proof does. The statement walk passes it, the
    proof walk (`trunk_rejection`) refuses it — the library counts an entry by its proof."""
    _, obj = _fixture()
    stub = _stub(f"theorem t_pf : True := by\n  have _h : {obj} 0 = {obj} 0 := rfl\n  trivial\n")
    trunk = {"trunk": [f"{PACK.namespace}.Market.price"], "prices": [PACK.qualified(obj)]}
    assert _errors(af_gates.map_probe(PACK, stub, "t_pf", [], trunk)) == []
    errs = _errors(af_gates.map_probe(PACK, stub, "t_pf", [], trunk, through_proof=True))
    assert errs and all(af_gates._TRUNK_MARKER in e for e in errs), errs
