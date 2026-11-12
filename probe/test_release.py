"""release.py: a target the library refused to assemble is retried, told why, never stranded."""
from __future__ import annotations

import json

import release


class _Notebook:
    def __init__(self):
        self.recorded, self.forgotten = [], []

    def record(self, target, attempt, **_):
        self.recorded.append((target, attempt))

    def forget(self, target):
        self.forgotten.append(target)


def _setup(tmp_path, tag="pipeline-1"):
    runs, queue = tmp_path / "runs", tmp_path / "queue"
    runs.mkdir()
    queue.mkdir()
    (queue / "cal-bk-318.lean").write_text("stub")
    (queue / "cal-bk-318.entry.json").write_text("{}")
    (queue / "manifest.json").write_text(json.dumps({"targets": [{"id": "cal-bk-318"}]}))
    (runs / f"{tag}-cal-bk-318.blocked.json").write_text(json.dumps(
        {"reason": "in-image build/regen failed rc=1",
         "ratchets": ["duplicate_statements: 1 > baseline 0"]}))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"attempted_issues": ["cal-bk-318"], "voided": [],
                                 "history": [{"id": "cal-bk-318", "outcome": "fail_assembly",
                                              "tokens": 9}]}))
    return runs, queue, state


def _fail_again(state, tag="pipeline-1"):
    s = json.loads(state.read_text())
    s["history"].append({"id": "cal-bk-318", "outcome": "fail_assembly", "tokens": 1})
    s["attempted_issues"].append("cal-bk-318")
    state.write_text(json.dumps(s))


def test_a_refused_assembly_is_reproved_then_redrafted_then_parked(tmp_path):
    runs, queue, state = _setup(tmp_path)
    nb = _Notebook()
    kw = dict(state_path=str(state), queue_dir=str(queue), runs_dir=str(runs),
              target_id="cal-bk-318", tag="pipeline-1", experience=nb, routing_arch="arch")

    r = release.apply(**kw)
    s = json.loads(state.read_text())
    assert r["action"] == "reprove" and "duplicate_statements" in r["reason"]
    assert "cal-bk-318" not in s["attempted_issues"] and not s["history"]
    assert release.RELEASE_MARK in s["voided"][0]["voided"]
    assert (queue / "cal-bk-318.lean").exists()                  # the same stub, re-proved
    assert nb.recorded and "duplicate_statements" in nb.recorded[0][1]["reason"]
    assert (runs / "pipeline-1-cal-bk-318.refused").exists()     # never reused

    _fail_again(state)
    r = release.apply(**kw)
    assert r["action"] == "redraft"
    assert not (queue / "cal-bk-318.lean").exists()
    assert json.loads((queue / "manifest.json").read_text())["targets"] == []
    assert nb.forgotten == ["cal-bk-318"]
    rec = json.loads((runs / "refill-history.jsonl").read_text().splitlines()[-1])
    assert rec["issue"] == 318 and rec["outcome"] == release.REDRAFT
    assert "duplicate_statements" in rec["history"][0]["detail"]

    _fail_again(state)
    assert release.apply(**kw)["action"] == "park"
    rec = json.loads((runs / "refill-history.jsonl").read_text().splitlines()[-1])
    assert rec["outcome"] == release.PARKED


def test_the_lesson_reaches_the_drafter_and_a_parked_issue_goes_last(tmp_path):
    from af_routing import (ROUTING_ARCH, _DEMOTED_FAMILIES, classify_refill,
                            load_prior_lessons)
    assert classify_refill({"outcome": release.REDRAFT}) not in _DEMOTED_FAMILIES
    assert classify_refill({"outcome": release.PARKED}) in _DEMOTED_FAMILIES
    hist = tmp_path / "h.jsonl"
    hist.write_text(json.dumps({"issue": 318, "outcome": release.REDRAFT, "arch": ROUTING_ARCH,
                                "history": [{"gate": "assembly", "detail": "refused: x"}]}) + "\n")
    lesson = load_prior_lessons(str(hist))[318]
    assert lesson["last_gate"] == "assembly" and "refused: x" in lesson["last_detail"]


def test_a_refused_proof_is_never_adopted(tmp_path):
    import prove_session
    tag = "pipeline-1"
    (tmp_path / f"{tag}-summary.jsonl").write_text(json.dumps(
        {"target": "cal-bk-1", "harness": "agentic", "outcome": "pass"}) + "\n")
    (tmp_path / f"{tag}-cal-bk-1.candidate").write_text("theorem x : True := trivial\n")
    (tmp_path / f"{tag}-cal-bk-1.session.json").write_text(json.dumps({"tokens": 5}))
    stub = "theorem x : True := by sorry\n"
    assert prove_session.adopt_verified(str(tmp_path), "cal-bk-1", "t", stub=stub,
                                        sorry_name="x") == tag
    (tmp_path / f"{tag}-cal-bk-1.refused").write_text("refused")
    assert prove_session.adopt_verified(str(tmp_path), "cal-bk-1", "t2", stub=stub,
                                        sorry_name="x") is None


def test_the_prove_gate_asks_the_library_checks_in_order():
    import domain_pack
    import prove_session
    pack = domain_pack.load("mathfin")
    tab = {"trunk": ["MathFin.Market.price"], "prices": ["MathFin.bsV"]}
    clean = lambda c: {"errors": []}                                          # noqa: E731
    assert prove_session.library_refusal(pack, "theorem t : True := trivial\n", "t", tab,
                                         check_fn=clean) is None
    shape = lambda c: {"errors": ["shape-gate: t states what MathFin.u states"]}  # noqa: E731
    assert prove_session.library_refusal(pack, "theorem t : True := trivial\n", "t", tab,
                                         check_fn=shape).startswith("shape:")
    trunk = lambda c: {"errors": ["trunk-gate: the price MathFin.bsV"]   # noqa: E731
                       if "trunk-gate" in c and "shape-gate" not in c else []}
    assert prove_session.library_refusal(pack, "theorem t : True := trivial\n", "t", tab,
                                         check_fn=trunk).startswith("off_trunk:")
