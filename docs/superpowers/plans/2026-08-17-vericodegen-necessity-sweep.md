# VeriCodeGen Necessity Sweep — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure, with kernel-certified positives, how often a theorem in a mature Lean library carries an explicit hypothesis it does not need — across two arms (formal-mathfin, Mathlib) — and write the VeriCodeGen 2026 paper on the result.

**Architecture:** A standalone driver (`probe/necessity_sweep.py`) reuses the already-built, already-traced prober in `probe/strengthen.py` by injecting `probe.daemon_check` as `check_fn` and `strengthen.tactic_sweep_prover` as `prove_fn`. No pipeline, no model calls, no API keys. The driver adds three things the prober does not have: a corpus loader, a sound syntactic pre-filter that halves the daemon workload, and a per-theorem power control. Output is append-only JSONL under `runs/necessity-sweep/`, so a multi-hour run is resumable.

**Tech Stack:** Python 3.12 (stdlib only — the repo's `probe/` has no third-party runtime deps), pytest, the `mathfin-verify` Docker image serving the Lean REPL daemon on 127.0.0.1:7878, Lean v4.32.0 / Mathlib `81a5d257`.

**Spec:** `docs/superpowers/specs/2026-08-17-vericodegen-submission-design.md`

## Global Constraints

- **Zero API spend.** No `mistral_chat`, no Claude calls, no new pipeline ticks. R's decision 2026-08-17. Every prover call in this plan is the fixed tactic sweep against the daemon.
- **One Lean-loaded process at a time.** ~10 GB box, ~4–5 GB per Mathlib env. REPL daemon XOR lean-lsp XOR a `lake build`. Before starting the daemon, confirm no `mathfin-verify` build container is running (`docker ps | grep verify`).
- **Fail-open, always.** A daemon error is never a verdict. Any socket timeout, malformed reply, or unparseable header records `daemon_error` and moves on; it never counts as "hypothesis necessary" and never counts as "unnecessary".
- **Positives are certified; negatives are not.** A binder is reported unnecessary only when the reduced statement was closed and re-gated. Everything else is "not shown unnecessary". This asymmetry is load-bearing for the paper and must survive into the record schema.
- **Daemon-free tests.** All 36 existing `probe/test_*.py` modules run without a daemon; these must too. Inject fakes.
- **Deadlines.** Abstract 2026-09-11, paper 2026-09-13. Tasks 1–7 must complete by 2026-09-06 to leave a week for writing.

---

### Task 0: Make the daemon survive consecutive checks (BLOCKING)

**Files:**
- Create: `runs/necessity-sweep/daemon-stability.md`
- Possibly modify (host, not repo): `/mnt/c/Users/rapha/.wslconfig`, `formal-mathfin/docker/docker-compose.yml`

**Measured 2026-08-17, and the reason this task exists.** With the daemon up and READY:

| check | wall-clock |
|---|---|
| `example : 2+2 = 4 := by rfl` (no import) | 3.2 s |
| `import MathFin` + the same trivial example | 196 s |
| three consecutive `import MathFin` checks | 110 s, 70 s, 258 s |
| `import MathFin.Performance.RatiosExtended` (single module) | 215 s |

The daemon's own docstring promises 5-30 s per check, because the Mathlib load is meant to
be paid once per daemon lifetime. The container log says why it is not:

```
Lean REPL died (attempt 1/2): The Lean server closed unexpectedly.
- Not enough memory and/or compute available
Lean server respawned (fresh REPL against prebuilt project)
```

The REPL is OOM-killed inside its cgroup on nearly every check and respawns cold, so every
probe re-pays the full import. Narrowing the import does not help — a single MathFin module
still pulls Mathlib transitively. At the observed ~150 s median the 943-call MathFin census
is **~39 hours**, against this plan's own 12-hour kill threshold.

**The envelope:** Windows physical RAM 15.7 GB; `.wslconfig` `memory=10GB`; `lean-repl`
`mem_limit: 6g`; `LEAN_NUM_THREADS=1` already. Mathlib + BrownianMotion + MathFin resident
is ~4-5 GB, leaving under 2 GB of elaboration headroom inside a 6 GiB cap.

- [ ] **Step 1: Raise the ceiling (requires R — it restarts WSL)**

Edit `/mnt/c/Users/rapha/.wslconfig`: `memory=10GB` -> `memory=12GB` (Windows keeps ~3.7 GB).
Edit `formal-mathfin/docker/docker-compose.yml`, `lean-repl` service: `mem_limit: 6g` -> `8g`.
Then, from Windows: `wsl --shutdown`, reopen the shell, and restart the daemon.

- [x] **Step 2: Verify the REPL now survives**

```bash
cd probe && python3 -c "
import sys, time; sys.path.insert(0,'.')
from probe import daemon_check
for i in range(5):
    t=time.monotonic(); r=daemon_check(f'import MathFin\nexample : ({i}:Nat) + 0 = {i} := by simp\n')
    print(f'call {i+1}: {time.monotonic()-t:6.1f}s errors={len(r.get(\"errors\") or [])}', flush=True)
"
docker logs --tail 20 docker-lean-repl-1 2>&1 | grep -c "respawned"
```

**Pass:** calls 2-5 land in the 5-30 s band and the respawn count is 0. **Fail:** any respawn,
or a median above 30 s.

- [x] **Step 3: Record the outcome and pick the population accordingly**

Write `runs/necessity-sweep/daemon-stability.md` with the before/after timings and the
respawn count.

**Decision rule, fixed in advance:**
- **Stable (median <= 30 s)** -> run the full census: 943 calls is 1.3-8 h. Tasks 6 and 7 as written.
- **Not stable, or R declines the memory change** -> **switch from census to a stratified
  random sample** and say so in the paper. At the degraded ~150 s, a 200-binder sample is
  ~8 h. Sampling is not a retreat: it lets the paper report a rate with a confidence
  interval rather than a point estimate from one library's census, which is the more
  defensible claim anyway. Sample proportionally by domain, seed `20260913`, and report
  the seed and the per-domain draw.

- [x] **Step 4: Commit**

```bash
git add runs/necessity-sweep/daemon-stability.md
git commit -m "measure(sweep): the daemon OOMs on every check — the ceiling, and what it costs"
```

---

### Task 1: Corpus loader and the sound binder pre-filter

**Files:**
- Create: `probe/necessity_sweep.py`
- Test: `probe/test_necessity_sweep.py`

**Interfaces:**
- Consumes: `autoformalize._locate_named`, `autoformalize._binder_groups` (existing, used by `strengthen.py` the same way).
- Produces:
  - `PRIMARY_DECL: re.Pattern` — matches `theorem`/`lemma` declarations.
  - `primary_decl(code: str) -> str | None` — the last top-level declaration name, or None.
  - `Entry` — a `dataclass` with fields `arm: str, entry_id: str, domain: str, thm: str, status: str, provenance: str, code: str`.
  - `load_mathfin_entries(bench_glob: str) -> list[Entry]`
  - `probe_worthy_binders(code: str, thm: str) -> list[str]` — explicit binders whose name does NOT occur free in the rest of the signature or in the conclusion.

- [x] **Step 1: Write the failing tests**

```python
# probe/test_necessity_sweep.py
"""Daemon-free tests for the necessity sweep driver."""
import necessity_sweep as ns

WRAPPER = '''import MathFin.Performance.RatiosExtended

open MathFin

theorem mf_performance_gain_to_pain {ι : Type*} (s : Finset ι) (r : ι → ℝ) :
    0 ≤ gainToPain s r :=
  MathFin.gainToPain_nonneg s r
'''

GUARDED = '''import MathFin.Performance.RatiosExtended

theorem gainToPain_nonneg_of_denom_pos {S : Type*} (finset_S : Finset S) (r : S → ℝ)
    (h : 0 < ∑ s ∈ finset_S, max (-r s) 0) : 0 ≤ gainToPain S finset_S r := by
  positivity
'''


def test_primary_decl_reads_the_last_declaration():
    assert ns.primary_decl(WRAPPER) == "mf_performance_gain_to_pain"
    assert ns.primary_decl(GUARDED) == "gainToPain_nonneg_of_denom_pos"


def test_primary_decl_is_none_when_there_is_no_theorem():
    assert ns.primary_decl("def f : Nat := 3\n") is None


def test_data_binders_are_pre_filtered_because_dropping_them_cannot_elaborate():
    # `s` and `r` both occur in the conclusion `0 ≤ gainToPain s r`, so dropping
    # either is a certain elaboration failure and must not cost a daemon call.
    assert ns.probe_worthy_binders(WRAPPER, "mf_performance_gain_to_pain") == []


def test_a_hypothesis_binder_survives_the_pre_filter():
    # `h` appears nowhere in the remaining signature or the conclusion.
    assert ns.probe_worthy_binders(GUARDED, "gainToPain_nonneg_of_denom_pos") == ["h"]


def test_pre_filter_is_name_boundary_aware():
    code = '''theorem t (h : True) (hs : Nat) : hs = hs := by rfl\n'''
    # `h` is not used by `hs` — a substring match would wrongly pre-filter it.
    assert "h" in ns.probe_worthy_binders(code, "t")


def test_loader_partitions_by_provenance_and_status(tmp_path):
    bench = tmp_path / "d.json"
    bench.write_text(__import__("json").dumps({
        "description": "d",
        "theorems": [
            {"id": "a", "code": {"lean": GUARDED},
             "metadata": {"formalization_status": "full"}},
            {"id": "b", "code": {"lean": WRAPPER},
             "metadata": {"formalization_status": "library_wrapper",
                          "provenance": {"source": "leanstral-autoform"}}},
        ]}), encoding="utf-8")
    entries = ns.load_mathfin_entries(str(tmp_path / "*.json"))
    assert [(e.entry_id, e.status, e.provenance) for e in entries] == [
        ("a", "full", "human"), ("b", "library_wrapper", "leanstral-autoform")]
    assert entries[0].domain == "d"
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `cd probe && python3 -m pytest test_necessity_sweep.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'necessity_sweep'`

- [x] **Step 3: Write the implementation**

```python
# probe/necessity_sweep.py
"""necessity_sweep — drive the necessity prober (backlog item R) over a whole corpus.

`strengthen.py` answers "is this hypothesis needed by this theorem?" for one candidate
inside the pipeline's gate phase. This module asks the same question of an entire
library, offline, with no pipeline and no model calls: it loads catalogued entries,
triages their binders, and records one verdict per binder.

Positives are certified — the reduced statement was proved and re-gated. Negatives are
not: the prover here is a fixed eight-tactic sweep, so "not closed" means "not shown
unnecessary", never "necessary". Every consumer of this data must preserve that.
"""
from __future__ import annotations

import glob as _glob
import json
import re
from dataclasses import dataclass

__all__ = ["PRIMARY_DECL", "Entry", "primary_decl", "probe_worthy_binders",
           "load_mathfin_entries"]

PRIMARY_DECL = re.compile(
    r"^(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+|nonrec\s+)?"
    r"(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)", re.M)


@dataclass(frozen=True)
class Entry:
    arm: str
    entry_id: str
    domain: str
    thm: str
    status: str
    provenance: str
    code: str


def primary_decl(code: str) -> str | None:
    """The name of the entry's primary declaration — the LAST top-level theorem/lemma.
    Catalogued entries put helper lemmas first and the headline result last."""
    names = PRIMARY_DECL.findall(code)
    return names[-1] if names else None


def _occurs_free(name: str, text: str) -> bool:
    """Whether `name` occurs in `text` as a whole Lean identifier. Boundary-aware, so
    `h` does not match inside `hs` — a substring test would silently pre-filter real
    hypotheses and understate the result."""
    return re.search(r"(?<![A-Za-z0-9_'])" + re.escape(name) + r"(?![A-Za-z0-9_'])",
                     text) is not None


def probe_worthy_binders(code: str, thm: str) -> list[str]:
    """Explicit binders worth spending a daemon call on, in signature order.

    SOUND PRE-FILTER: if a binder's name occurs free in the rest of the signature or in
    the conclusion, dropping it cannot elaborate, so the free filter would reject it and
    the call is wasted. Skipping those removes only certain-failures — never a possible
    positive. Measured 2026-08-17 on formal-mathfin: 1,465 explicit binders -> 689.
    """
    from autoformalize import _binder_groups, _locate_named
    try:
        bstart, sep, end = _locate_named(code, thm)
    except ValueError:
        return []
    sig, concl = code[bstart:sep], code[sep:end]
    groups = list(_binder_groups(sig))
    out: list[str] = []
    for i, (_s, _e, opener, names) in enumerate(groups):
        if opener != "(":            # implicit/instance binders are not hypotheses
            continue
        rest = "".join(sig[gs:ge] for j, (gs, ge, _o, _n) in enumerate(groups) if j != i)
        for nm in names:
            if not _occurs_free(nm, rest) and not _occurs_free(nm, concl):
                out.append(nm)
    return out


def load_mathfin_entries(bench_glob: str) -> list[Entry]:
    """Catalogued entries from a `benchmarks/*.json` glob, one per primary declaration.
    Entries with no theorem declaration (definition-only catalogue rows) are skipped."""
    out: list[Entry] = []
    for path in sorted(_glob.glob(bench_glob)):
        domain = path.rsplit("/", 1)[-1][:-len(".json")]
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        for e in payload.get("theorems", []):
            code = (e.get("code") or {}).get("lean") or ""
            thm = primary_decl(code)
            if not thm:
                continue
            md = e.get("metadata") or {}
            prov = ((md.get("provenance") or {}).get("source")) or "human"
            out.append(Entry(arm="mathfin", entry_id=e.get("id", ""), domain=domain,
                             thm=thm, status=md.get("formalization_status") or "(none)",
                             provenance=prov, code=code))
    return out
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `cd probe && python3 -m pytest test_necessity_sweep.py -v`
Expected: PASS, 6 tests.

- [x] **Step 5: Verify against the real corpus (still daemon-free)**

Run:
```bash
cd probe && python3 -c "
import necessity_sweep as ns
E = ns.load_mathfin_entries('../../formal-mathfin/benchmarks/*.json')
full = [e for e in E if e.status == 'full']
n = sum(len(ns.probe_worthy_binders(e.code, e.thm)) for e in full)
print(f'entries={len(E)} full={len(full)} probe-worthy binders={n}')
"
```
Expected: `entries=357 full=330 probe-worthy binders=700`, against `formal-mathfin`
`benchmarks/` at corpus commit `48cb004`. If the binder count differs, the corpus moved or
the pre-filter changed meaning — stop and reconcile before proceeding, because the spec
quotes this number.

**Reconciled 2026-08-27.** This step first read `entries=357 full=330 worthy=700` against a
plan that pinned `348 / 320 / 689`. Replaying the loader over `benchmarks/` at each commit
that touched it shows the shipped filter returns exactly `348 / 689` at `8e52f446` — the
snapshot the plan was measured against — and `357 / 700` from `c419f0f0` onward, nine
entries added later the same day by the reified-payoff-language feature. The pre-filter's
meaning did not move; the population did. (`full=320` in the original was a transcription
slip: that snapshot measures 321.) The numbers above and in spec §2 are now pinned to a
named corpus commit, and the sweep records the commit it actually ran against.

- [x] **Step 6: Commit**

```bash
git add probe/necessity_sweep.py probe/test_necessity_sweep.py
git commit -m "feat(sweep): corpus loader + the sound binder pre-filter (1465 -> 689)"
```

---

### Task 2: The power control — can the sweep prove the theorem at all?

**Files:**
- Modify: `probe/necessity_sweep.py`
- Test: `probe/test_necessity_sweep.py`

**Interfaces:**
- Consumes: `Entry` from Task 1; `strengthen.necessity_probe`, `strengthen.tactic_sweep_prover`.
- Produces: `sweep_can_prove(code: str, thm: str, *, prove_fn) -> bool` — whether the fixed sweep closes the theorem with ALL hypotheses present.

**Why this task exists:** without it the result is uninterpretable. If the eight-tactic sweep cannot prove a theorem even with every hypothesis in place, then its failure to prove a hypothesis-reduced version carries no information — the instrument is blind on that theorem, not the hypothesis load-bearing. Theorems that fail this control are excluded from the rate's denominator and reported as the instrument's blind fraction.

- [x] **Step 1: Write the failing tests**

```python
def test_power_control_true_when_the_sweep_closes_the_original():
    calls = []

    def fake_prove(probe):
        calls.append(probe)
        return {"lean_text": probe.replace("sorry", "positivity"), "tokens": 0}

    assert ns.sweep_can_prove(GUARDED, "gainToPain_nonneg_of_denom_pos",
                              prove_fn=fake_prove) is True
    # the control probes the ORIGINAL signature — no binder was dropped
    assert "(h : 0 < " in calls[0]
    assert "sorry" in calls[0]


def test_power_control_false_when_the_sweep_returns_the_probe_untouched():
    def fake_prove(probe):
        return {"lean_text": probe, "tokens": 0}    # unchanged == not closed

    assert ns.sweep_can_prove(GUARDED, "gainToPain_nonneg_of_denom_pos",
                              prove_fn=fake_prove) is False


def test_power_control_false_when_the_declaration_cannot_be_located():
    def fake_prove(probe):
        raise AssertionError("must not be called")

    assert ns.sweep_can_prove(GUARDED, "no_such_theorem", prove_fn=fake_prove) is False
```

- [x] **Step 2: Run to verify they fail**

Run: `cd probe && python3 -m pytest test_necessity_sweep.py -k power_control -v`
Expected: FAIL — `AttributeError: module 'necessity_sweep' has no attribute 'sweep_can_prove'`

- [x] **Step 3: Implement**

```python
def _statement_only(code: str, thm: str) -> str | None:
    """`code` with the theorem's proof replaced by `sorry` and NO binder dropped —
    the power-control probe."""
    from autoformalize import _locate_named
    try:
        bstart, sep, end = _locate_named(code, thm)
    except ValueError:
        return None
    return code[:bstart] + code[bstart:sep] + code[sep:end] + ":= by sorry\n"


def sweep_can_prove(code: str, thm: str, *, prove_fn) -> bool:
    """Whether the fixed tactic sweep closes this theorem with ALL hypotheses present.

    The power control. A theorem that fails it is one the instrument cannot speak about:
    the sweep's failure on a REDUCED statement then says nothing about the dropped
    hypothesis. Such theorems are excluded from the rate denominator and reported as the
    blind fraction. Fails closed — any trouble reads as "cannot prove", which only ever
    shrinks the population we make claims about."""
    probe = _statement_only(code, thm)
    if probe is None:
        return False
    try:
        got = prove_fn(probe)
    except Exception:
        return False
    text = (got or {}).get("lean_text") or ""
    return bool(text) and "sorry" not in text
```

Add `"sweep_can_prove"` to `__all__`.

- [x] **Step 4: Run to verify they pass**

Run: `cd probe && python3 -m pytest test_necessity_sweep.py -v`
Expected: PASS, 9 tests.

- [x] **Step 5: Commit**

```bash
git add probe/necessity_sweep.py probe/test_necessity_sweep.py
git commit -m "feat(sweep): the power control — exclude theorems the sweep cannot prove at all"
```

---

### Task 3: Per-binder verdicts and the record schema

**Files:**
- Modify: `probe/necessity_sweep.py`
- Test: `probe/test_necessity_sweep.py`

**Interfaces:**
- Consumes: `Entry`, `probe_worthy_binders`, `sweep_can_prove`.
- Produces: `sweep_entry(entry: Entry, *, check_fn, prove_fn, regate_fn) -> list[dict]` — one record per probed binder, plus exactly one `power_control` record per entry.

Record schema (every key always present):

```python
{"arm": str, "entry_id": str, "domain": str, "thm": str, "status": str,
 "provenance": str, "binder": str | None,
 "verdict": "certified_unnecessary" | "not_shown_unnecessary"
            | "free_filter_rejected" | "daemon_error" | "power_control",
 "sweep_proves_original": bool, "closing_tactic": str | None, "elapsed_s": float}
```

- [x] **Step 1: Write the failing tests**

**Fixture corrected during execution.** As written below, `_fakes` returns the probe
untouched whenever `(h :` is still present — and the power-control probe keeps every
binder, so the fake reports the instrument blind on a theorem whose real proof is
`positivity`, the sweep's own first tactic. That contradicts the same test's
`sweep_proves_original is True`. The shipped fixture instead closes a probe when nothing
was dropped, and closes a reduced probe exactly when every binder it dropped is named in
`closes`. The assertion was right; the fake was wrong.

```python
def _fakes(closes: set[str]):
    """check_fn accepts everything; prove_fn closes exactly the probes whose dropped
    binder is named in `closes`."""
    def check_fn(code):
        return {"errors": [], "sorry_count": 1 if "sorry" in code else 0}

    def prove_fn(probe):
        for nm in closes:
            if f"({nm} :" not in probe:          # that binder was the one dropped
                return {"lean_text": probe.replace("sorry", "positivity"), "tokens": 0}
        return {"lean_text": probe, "tokens": 0}
    return check_fn, prove_fn


def test_a_removable_hypothesis_is_certified_unnecessary():
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("mathfin", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True})
    binder = [r for r in recs if r["binder"] == "h"][0]
    assert binder["verdict"] == "certified_unnecessary"
    assert binder["closing_tactic"] == "positivity"
    assert binder["sweep_proves_original"] is True


def test_every_entry_emits_exactly_one_power_control_record():
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("mathfin", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True})
    assert sum(1 for r in recs if r["verdict"] == "power_control") == 1


def test_a_red_regate_is_not_a_positive():
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("mathfin", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": False, "reason": "axioms"})
    binder = [r for r in recs if r["binder"] == "h"][0]
    assert binder["verdict"] == "not_shown_unnecessary"


def test_daemon_trouble_records_an_error_and_never_a_verdict():
    def check_fn(code):
        return {"error": "connection refused", "errors": ["connection refused"]}

    def prove_fn(probe):
        return {"lean_text": probe, "tokens": 0}

    e = ns.Entry("mathfin", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True})
    verdicts = {r["verdict"] for r in recs if r["binder"] is not None}
    assert verdicts <= {"daemon_error"}
    assert "certified_unnecessary" not in verdicts
```

- [x] **Step 2: Run to verify they fail**

Run: `cd probe && python3 -m pytest test_necessity_sweep.py -k "certified or power_control_record or regate or daemon_trouble" -v`
Expected: FAIL — `AttributeError: ... 'sweep_entry'`

- [x] **Step 3: Implement**

```python
def _closing_tactic(probe: str, proved: str) -> str | None:
    """The tactic the sweep substituted for `sorry`, recovered by diffing the probe
    against what came back. Recorded so a reader can see which sweep slot did the work."""
    i = probe.find("sorry")
    if i < 0 or not proved:
        return None
    tail = len(probe) - (i + len("sorry"))
    return proved[i:len(proved) - tail].strip() or None


def sweep_entry(entry: "Entry", *, check_fn, prove_fn, regate_fn) -> list[dict]:
    """Probe every probe-worthy binder of one entry. Returns one record per binder plus
    exactly one `power_control` record. Never raises: infrastructure trouble becomes a
    `daemon_error` record, which is excluded from every rate."""
    import time
    from strengthen import necessity_probe

    def rec(binder, verdict, proves, tactic, elapsed):
        return {"arm": entry.arm, "entry_id": entry.entry_id, "domain": entry.domain,
                "thm": entry.thm, "status": entry.status, "provenance": entry.provenance,
                "binder": binder, "verdict": verdict, "sweep_proves_original": proves,
                "closing_tactic": tactic, "elapsed_s": round(elapsed, 3)}

    t0 = time.monotonic()
    proves_original = sweep_can_prove(entry.code, entry.thm, prove_fn=prove_fn)
    out = [rec(None, "power_control", proves_original, None, time.monotonic() - t0)]

    for nm in probe_worthy_binders(entry.code, entry.thm):
        t1 = time.monotonic()
        probe = necessity_probe(entry.code, entry.thm, {nm})
        if probe is None:
            out.append(rec(nm, "free_filter_rejected", proves_original, None,
                           time.monotonic() - t1))
            continue
        res = check_fn(probe)
        if res.get("error"):
            out.append(rec(nm, "daemon_error", proves_original, None,
                           time.monotonic() - t1))
            continue
        if res.get("errors"):
            out.append(rec(nm, "free_filter_rejected", proves_original, None,
                           time.monotonic() - t1))
            continue
        try:
            attempt = prove_fn(probe)
        except Exception:
            out.append(rec(nm, "daemon_error", proves_original, None,
                           time.monotonic() - t1))
            continue
        proved = (attempt or {}).get("lean_text") or ""
        if not proved or "sorry" in proved:
            out.append(rec(nm, "not_shown_unnecessary", proves_original, None,
                           time.monotonic() - t1))
            continue
        if not regate_fn(proved).get("passed"):
            out.append(rec(nm, "not_shown_unnecessary", proves_original, None,
                           time.monotonic() - t1))
            continue
        out.append(rec(nm, "certified_unnecessary", proves_original,
                       _closing_tactic(probe, proved), time.monotonic() - t1))
    return out
```

Add `"sweep_entry"` to `__all__`.

- [x] **Step 4: Run to verify they pass**

Run: `cd probe && python3 -m pytest test_necessity_sweep.py -v`
Expected: PASS, 13 tests.

- [x] **Step 5: Commit**

```bash
git add probe/necessity_sweep.py probe/test_necessity_sweep.py
git commit -m "feat(sweep): per-binder verdicts, with daemon trouble excluded from every rate"
```

---

### Task 4: Resumable JSONL persistence and the CLI

**Files:**
- Modify: `probe/necessity_sweep.py`
- Create: `scripts/necessity-sweep.sh`
- Test: `probe/test_necessity_sweep.py`

**Interfaces:**
- Consumes: `sweep_entry`.
- Produces:
  - `done_keys(path: str) -> set[tuple[str, str]]` — `(arm, entry_id)` pairs already written.
  - `run_sweep(entries, out_path, *, check_fn, prove_fn, regate_fn, log=print) -> dict` — appends records, skips finished entries, returns `{"entries": n, "records": m, "skipped": k}`.
  - `main(argv)` — CLI: `python3 necessity_sweep.py --arm mathfin --out ../runs/necessity-sweep/<stamp>-mathfin.jsonl [--limit N]`.

**Why resumable:** the MathFin arm is 254 entries and ~943 daemon calls (689 binders + 254 power controls). At the latency measured in Task 5 this is hours, and the box is shared with whatever else wants the Lean slot. Losing a run to a wedged daemon must cost the last entry, not the run.

- [x] **Step 1: Write the failing tests**

```python
def test_done_keys_reads_back_what_was_written(tmp_path):
    p = tmp_path / "out.jsonl"
    import json as _j
    p.write_text("\n".join(_j.dumps({"arm": "mathfin", "entry_id": x})
                           for x in ("a", "b")) + "\n", encoding="utf-8")
    assert ns.done_keys(str(p)) == {("mathfin", "a"), ("mathfin", "b")}


def test_done_keys_is_empty_when_the_file_does_not_exist(tmp_path):
    assert ns.done_keys(str(tmp_path / "nope.jsonl")) == set()


def test_done_keys_tolerates_a_truncated_final_line(tmp_path):
    p = tmp_path / "out.jsonl"
    p.write_text('{"arm": "mathfin", "entry_id": "a"}\n{"arm": "mathfin", "ent',
                 encoding="utf-8")
    assert ns.done_keys(str(p)) == {("mathfin", "a")}


def test_run_sweep_skips_entries_already_recorded(tmp_path):
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("mathfin", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    out = str(tmp_path / "out.jsonl")
    first = ns.run_sweep([e], out, check_fn=check_fn, prove_fn=prove_fn,
                         regate_fn=lambda c: {"passed": True}, log=lambda m: None)
    second = ns.run_sweep([e], out, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True}, log=lambda m: None)
    assert first["entries"] == 1 and first["skipped"] == 0
    assert second["entries"] == 0 and second["skipped"] == 1
```

- [x] **Step 2: Run to verify they fail**

Run: `cd probe && python3 -m pytest test_necessity_sweep.py -k "done_keys or run_sweep" -v`
Expected: FAIL — `AttributeError: ... 'done_keys'`

- [x] **Step 3: Implement**

```python
def done_keys(path: str) -> set[tuple[str, str]]:
    """`(arm, entry_id)` pairs already present in an output file. A run killed mid-write
    can leave a truncated final line; that line is dropped rather than raising, so a
    resume never needs the file repaired by hand."""
    out: set[tuple[str, str]] = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                out.add((d.get("arm", ""), d.get("entry_id", "")))
    except FileNotFoundError:
        return set()
    return out


def run_sweep(entries, out_path: str, *, check_fn, prove_fn, regate_fn,
              log=print) -> dict:
    """Sweep `entries`, appending records to `out_path` and skipping entries already
    there. Flushes after every entry so a kill costs one entry, not the run."""
    import os
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    seen = done_keys(out_path)
    stats = {"entries": 0, "records": 0, "skipped": 0}
    with open(out_path, "a", encoding="utf-8") as f:
        for e in entries:
            if (e.arm, e.entry_id) in seen:
                stats["skipped"] += 1
                continue
            recs = sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                              regate_fn=regate_fn)
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
            stats["entries"] += 1
            stats["records"] += len(recs)
            hits = sum(1 for r in recs if r["verdict"] == "certified_unnecessary")
            log(f"[sweep] {e.entry_id}: {len(recs)} records, {hits} certified")
    return stats


def main(argv=None) -> int:
    import argparse
    import sys
    from probe import daemon_check
    from strengthen import tactic_sweep_prover

    ap = argparse.ArgumentParser(description="necessity sweep over a Lean corpus")
    ap.add_argument("--arm", choices=("mathfin", "mathlib"), default="mathfin")
    ap.add_argument("--bench", default="../../formal-mathfin/benchmarks/*.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--status", default="full",
                    help="only sweep entries with this formalization_status; 'all' for every one")
    ap.add_argument("--limit", type=int, default=0, help="stop after N entries (0 = all)")
    args = ap.parse_args(argv)

    entries = load_mathfin_entries(args.bench)
    if args.status != "all":
        entries = [e for e in entries if e.status == args.status]
    if args.limit:
        entries = entries[:args.limit]

    prove_fn = tactic_sweep_prover(daemon_check)

    def regate_fn(code):
        res = daemon_check(code)
        if res.get("error"):
            return {"passed": False, "reason": res["error"]}
        return {"passed": not res.get("errors") and res.get("sorry_count", 0) == 0}

    stats = run_sweep(entries, args.out, check_fn=daemon_check, prove_fn=prove_fn,
                      regate_fn=regate_fn)
    print(f"[sweep] done: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 4: Run to verify they pass**

Run: `cd probe && python3 -m pytest test_necessity_sweep.py -v`
Expected: PASS, 17 tests.

- [x] **Step 5: Write the shell entrypoint**

```bash
# scripts/necessity-sweep.sh
#!/usr/bin/env bash
# Sweep a corpus for hypotheses its theorems do not need. Daemon-only, zero tokens.
# Refuses to run while a Lean build holds the slot — one Lean-loaded process at a time.
set -euo pipefail
cd "$(dirname "$0")/.."

if docker ps --format '{{.Image}}' | grep -q 'mathfin-verify'; then
  if ! docker ps --format '{{.Names}}' | grep -q 'lean-repl'; then
    echo "refusing: a mathfin-verify build is holding the Lean slot" >&2
    exit 1
  fi
fi

STAMP="$(date -u +%Y%m%d-%H%M%S)"
ARM="${ARM:-mathfin}"
OUT="runs/necessity-sweep/${STAMP}-${ARM}.jsonl"
mkdir -p runs/necessity-sweep
echo "[sweep] arm=${ARM} out=${OUT}"
( cd probe && python3 necessity_sweep.py --arm "${ARM}" --out "../${OUT}" "$@" )
```

Run: `chmod +x scripts/necessity-sweep.sh`

- [x] **Step 6: Commit**

```bash
git add probe/necessity_sweep.py probe/test_necessity_sweep.py scripts/necessity-sweep.sh
git commit -m "feat(sweep): resumable JSONL output + CLI, refusing to fight the build for the Lean slot"
```

- [x] **Step 7 (added during execution): fill the sweep's `{defs}`/`{unfold}` slots**

Step 3 above wires `prove_fn = tactic_sweep_prover(daemon_check)`. Passed no `def_names`,
`tactic_sweep_prover` **skips every tactic carrying a `{defs}` or `{unfold}` slot** — six
of the eight — so the shipped instrument is `positivity` + `grind`, not the 8-tactic
sweep spec §2 describes. That is not a small loss: all 331 `full` entries import a
MathFin module, and `strengthen.py`'s own trace records that on #161 bare `positivity`
FAILS where `unfold gainToPain; positivity` closes. The power control would have failed
almost everywhere and the rate's denominator collapsed — the paper would have measured
its own wiring. R's decision 2026-08-27: derive the defs per entry.

`module_defs(code, mathfin_root)` reads each `import MathFin.X.Y` from the entry, collects
that module's own `def`/`abbrev` names, and keeps the ones the statement actually names —
splicing an unnamed def into `unfold` only makes the tactic fail to elaborate. Missing
modules are skipped, not raised on. `run_sweep` grew a `prove_for(entry) -> prove_fn`
factory beside `prove_fn`, since the defs differ entry to entry, and `main` passes
`--mathfin-root` (default `../../formal-mathfin`).

**Measured coverage:** 175 of 331 `full` entries (53%) name at least one definition from
their own imported module and so get all eight tactics; the remaining 156 name none —
their statements are about Mathlib entities or `structure`s, where `unfold` has nothing
to unfold and two tactics is the honest instrument. Report this split beside the blind
fraction: a theorem swept with two tactics and one swept with eight are not equally
looked at, and the paper must not average them silently.

Test count after this step: 22 in `test_necessity_sweep.py` (the plan's later steps
quote 17 and 19, both written before this step existed).

---

### Task 5: Latency measurement and the go/no-go on the Mathlib arm

**Files:**
- Create: `runs/necessity-sweep/latency.md`

**Interfaces:**
- Consumes: the CLI from Task 4.
- Produces: a measured per-call latency and a decided Mathlib sample size, both written down.

**Blocking precondition:** the Lean slot must be free. Check `docker ps | grep verify` shows no build container, then start the daemon with `docker compose -f /mnt/c/Users/rapha/Documents/Code/formal-mathfin/docker/docker-compose.yml up -d lean-repl` and confirm `ss -ltn | grep 7878`.

- [ ] **Step 1: Confirm the daemon answers**

Run:
```bash
cd probe && python3 -c "
from probe import daemon_check
print(daemon_check('import MathFin\nexample : (2:Nat) + 2 = 4 := by rfl\n'))
"
```
Expected: a dict with no `error` key and `errors == []`. If it says `Connection refused`, the daemon is not up — do not proceed.

- [ ] **Step 2: Time a 10-entry pilot**

Run:
```bash
mkdir -p runs/necessity-sweep
cd probe && time python3 necessity_sweep.py --arm mathfin \
  --out ../runs/necessity-sweep/pilot.jsonl --limit 10
```

- [ ] **Step 3: Compute the projections and write them down**

Run:
```bash
python3 - <<'EOF'
import json, statistics
recs = [json.loads(l) for l in open("runs/necessity-sweep/pilot.jsonl")]
per = [r["elapsed_s"] for r in recs]
med = statistics.median(per)
print(f"records={len(recs)} median={med:.1f}s mean={statistics.mean(per):.1f}s")
print(f"MathFin full arm (943 calls): {943*med/3600:.1f}h")
for n in (100, 250, 500, 1000):
    print(f"  Mathlib sample n={n}: ~{n*2*med/3600:.1f}h")
EOF
```

Write the numbers into `runs/necessity-sweep/latency.md` together with the decision.

**Decision rule, fixed in advance so the result cannot bend it.** Task 0 already decided
census-vs-sample; this task sizes the Mathlib arm within that decision:
- Mathlib sample: take the largest of {1000, 500, 250, 100} whose projection is **≤ 10 h**. If even n=100 exceeds 10 h, the Mathlib arm is **dropped**, and §2.2 of the spec is amended to record that it was dropped on measured cost rather than quietly omitted.
- If Task 0 landed on sampling, the MathFin arm is a proportional stratified draw of 200 binders under seed `20260913`, not the 689-binder census, and every rate in the paper carries a Wilson confidence interval.

- [ ] **Step 4: Commit**

```bash
git add runs/necessity-sweep/latency.md
git commit -m "measure(sweep): daemon latency, and the Mathlib sample size it affords"
```

---

### Task 6: Run the MathFin arm

**Files:**
- Create: `runs/necessity-sweep/<stamp>-mathfin.jsonl`

- [ ] **Step 1: Launch the full arm in the background**

Run: `ARM=mathfin scripts/necessity-sweep.sh --status full`

- [ ] **Step 2: While it runs, confirm resumability once**

Interrupt it after a few entries (Ctrl-C), re-run the same command, and confirm the log reports `skipped` > 0 and no entry is recorded twice:

```bash
python3 -c "
import json, collections
p='runs/necessity-sweep/'  # newest mathfin file
import glob; f=sorted(glob.glob(p+'*-mathfin.jsonl'))[-1]
keys=[(json.loads(l)['entry_id'], json.loads(l)['binder']) for l in open(f)]
dupes=[k for k,c in collections.Counter(keys).items() if c>1]
print('records:', len(keys), 'duplicates:', dupes[:5])
"
```
Expected: `duplicates: []`

- [ ] **Step 3: Let it finish, then sanity-check the two known entries**

The four autoform entries are post-refinery and carry no spurious hypothesis, so they must NOT show `certified_unnecessary`. If they do, the instrument disagrees with a human review that already ran — investigate before trusting anything else.

**Two of the four print nothing, by construction.** `mf-performance-gain_to_pain` and
`mf-performance-upside_capture` have no probe-worthy binders left — the refinery already
dropped the guards that made them cases #161 and #162 — so the check actually exercises
`mf-fixedincome-swap` (`hδ`, `hs`) and `mf-insurance-premium-principles` (`hμ`, `hσ2`,
`hσ`, `hθ`, `hα`, `hβ`): eight binders, not four entries. Expect eight lines, all
`not_shown_unnecessary`. Silence from the other two is the pre-filter working, not the
check passing.

```bash
python3 -c "
import json, glob
f=sorted(glob.glob('runs/necessity-sweep/*-mathfin.jsonl'))[-1]
for l in open(f):
    r=json.loads(l)
    if r['provenance']=='leanstral-autoform' and r['binder']:
        print(r['entry_id'], r['binder'], r['verdict'])
"
```

- [ ] **Step 4: Commit the telemetry**

```bash
git add runs/necessity-sweep/
git commit -m "measure(sweep): the MathFin arm — residual unnecessary-hypothesis rate"
```

---

### Task 7: The Mathlib arm

**Files:**
- Modify: `probe/necessity_sweep.py`
- Test: `probe/test_necessity_sweep.py`

**Interfaces:**
- Produces: `load_mathlib_entries(root: str, sample: int, seed: int) -> list[Entry]` — theorem declarations extracted from Mathlib sources, each wrapped as a self-contained probe importing only its own module.

**Skip this task entirely if Task 5's decision rule dropped the arm.**

- [ ] **Step 1: Write the failing tests**

```python
MATHLIB_SRC = '''/-- doc -/
theorem foo_bar {α : Type*} (s : Finset α) (h : s.Nonempty) : 0 < s.card := by
  simpa using h.card_pos

theorem baz (n : Nat) : n + 0 = n := by simp
'''


def test_mathlib_extraction_builds_a_self_contained_probe(tmp_path):
    d = tmp_path / "Mathlib" / "Order"
    d.mkdir(parents=True)
    (d / "Basic.lean").write_text(MATHLIB_SRC, encoding="utf-8")
    entries = ns.load_mathlib_entries(str(tmp_path), sample=0, seed=1)
    got = {e.thm for e in entries}
    assert got == {"foo_bar", "baz"}
    e = [x for x in entries if x.thm == "foo_bar"][0]
    assert e.code.startswith("import Mathlib.Order.Basic")
    assert e.arm == "mathlib" and e.domain == "Mathlib.Order.Basic"
    # the extracted entry carries ONE declaration, so primary_decl is unambiguous
    assert ns.primary_decl(e.code) == "foo_bar"


def test_mathlib_sampling_is_deterministic_under_a_seed(tmp_path):
    d = tmp_path / "Mathlib"
    d.mkdir(parents=True)
    (d / "A.lean").write_text(MATHLIB_SRC, encoding="utf-8")
    a = [e.thm for e in ns.load_mathlib_entries(str(tmp_path), sample=1, seed=7)]
    b = [e.thm for e in ns.load_mathlib_entries(str(tmp_path), sample=1, seed=7)]
    assert a == b and len(a) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd probe && python3 -m pytest test_necessity_sweep.py -k mathlib -v`
Expected: FAIL — `AttributeError: ... 'load_mathlib_entries'`

- [ ] **Step 3: Implement**

```python
_DECL_START = re.compile(
    r"^(?:@\[[^\]]*\]\s*\n)?(?:private\s+|protected\s+|nonrec\s+)?"
    r"(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)", re.M)


def _split_declarations(src: str):
    """(name, text) for each top-level theorem/lemma. A declaration runs to the start of
    the next top-level declaration — Lean's layout rule means a line starting in column
    zero with a keyword ends the previous one."""
    starts = [(m.start(), m.group(1)) for m in _DECL_START.finditer(src)]
    for i, (pos, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(src)
        yield name, src[pos:end].rstrip() + "\n"


def load_mathlib_entries(root: str, sample: int, seed: int) -> list[Entry]:
    """Theorem declarations from Mathlib sources under `root`, each wrapped as a
    self-contained probe that imports only its own module.

    Importing the single hosting module rather than all of Mathlib keeps each
    elaboration affordable; it is also the honest environment, since that is what the
    declaration itself was elaborated against. `sample=0` takes everything; otherwise a
    seeded uniform sample over declarations, so the run is reproducible from the seed
    alone."""
    import os
    import random
    out: list[Entry] = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if not fn.endswith(".lean"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, root)[:-len(".lean")]
            module = rel.replace(os.sep, ".")
            try:
                with open(path, encoding="utf-8") as f:
                    src = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            for name, text in _split_declarations(src):
                out.append(Entry(arm="mathlib", entry_id=f"{module}.{name}",
                                 domain=module, thm=name, status="full",
                                 provenance="mathlib",
                                 code=f"import {module}\n\n{text}"))
    out.sort(key=lambda e: e.entry_id)
    if sample and sample < len(out):
        out = random.Random(seed).sample(out, sample)
        out.sort(key=lambda e: e.entry_id)
    return out
```

Wire it into `main`: when `--arm mathlib`, call `load_mathlib_entries(args.mathlib_root, args.sample, args.seed)` instead of `load_mathfin_entries`; add `--mathlib-root`, `--sample`, `--seed` arguments (`--seed` default `20260913`). Add `"load_mathlib_entries"` to `__all__`.

- [ ] **Step 4: Run to verify they pass**

Run: `cd probe && python3 -m pytest test_necessity_sweep.py -v`
Expected: PASS, 19 tests.

- [ ] **Step 5: Locate Mathlib's sources and run the arm**

Run:
```bash
find /mnt/c/Users/rapha/Documents/Code/formal-mathfin/.lake -maxdepth 4 -type d -name Mathlib | head
ARM=mathlib scripts/necessity-sweep.sh \
  --mathlib-root <the path found above> --sample <n from Task 5> --seed 20260913
```

- [ ] **Step 6: Commit**

```bash
git add probe/necessity_sweep.py probe/test_necessity_sweep.py runs/necessity-sweep/
git commit -m "feat(sweep): the Mathlib arm — same instrument, the most-reviewed corpus there is"
```

---

### Task 8: Analysis — rates, reach, and the refined-defect table

**Files:**
- Create: `probe/sweep_report.py`
- Test: `probe/test_sweep_report.py`
- Create: `runs/necessity-sweep/report.md`

**Interfaces:**
- Consumes: the JSONL from Tasks 6–7; `formal-mathfin/benchmarks/*.json` for `provenance.refined`.
- Produces:
  - `rates(records) -> dict` keyed `(arm, domain, status)` with `{"probed", "certified", "blind_entries", "reachable_entries", "rate"}`.
  - `refined_defects(bench_glob) -> list[dict]` — `{entry_id, issue, refined}` for every entry whose provenance records a refinery change.
  - `render_report(rates, defects) -> str` — the markdown tables.

**The rate's denominator is the whole point:** count a binder only when its entry passed the power control (`sweep_proves_original`), and never count `daemon_error`. Report alongside every rate the blind fraction — entries the sweep could not prove at all — so a low rate cannot be read as a clean bill of health when it is really instrument blindness.

- [x] **Step 1: Write the failing tests**

```python
# probe/test_sweep_report.py
import sweep_report as sr


def _rec(**kw):
    base = {"arm": "mathfin", "entry_id": "e", "domain": "d", "thm": "t",
            "status": "full", "provenance": "human", "binder": "h",
            "verdict": "not_shown_unnecessary", "sweep_proves_original": True,
            "closing_tactic": None, "elapsed_s": 0.0}
    base.update(kw)
    return base


def test_rate_counts_only_binders_whose_entry_passed_the_power_control():
    recs = [
        _rec(entry_id="a", verdict="power_control", binder=None),
        _rec(entry_id="a", verdict="certified_unnecessary"),
        _rec(entry_id="b", verdict="power_control", binder=None,
             sweep_proves_original=False),
        _rec(entry_id="b", verdict="not_shown_unnecessary",
             sweep_proves_original=False),
    ]
    r = sr.rates(recs)[("mathfin", "d", "full")]
    assert r["probed"] == 1 and r["certified"] == 1 and r["rate"] == 1.0
    assert r["blind_entries"] == 1 and r["reachable_entries"] == 1


def test_daemon_errors_are_in_no_denominator():
    recs = [
        _rec(entry_id="a", verdict="power_control", binder=None),
        _rec(entry_id="a", verdict="daemon_error"),
        _rec(entry_id="a", binder="h2", verdict="certified_unnecessary"),
    ]
    r = sr.rates(recs)[("mathfin", "d", "full")]
    assert r["probed"] == 1 and r["certified"] == 1


def test_rate_is_none_rather_than_zero_when_nothing_was_probed():
    recs = [_rec(entry_id="a", verdict="power_control", binder=None,
                 sweep_proves_original=False)]
    r = sr.rates(recs)[("mathfin", "d", "full")]
    assert r["probed"] == 0 and r["rate"] is None


def test_refined_defects_are_pulled_from_provenance(tmp_path):
    import json
    (tmp_path / "d.json").write_text(json.dumps({"description": "d", "theorems": [
        {"id": "x", "code": {"lean": "theorem t : True := trivial"},
         "metadata": {"provenance": {"source": "leanstral-autoform", "issue": 161,
                                     "refined": "spurious guard dropped"}}},
        {"id": "y", "code": {"lean": "theorem u : True := trivial"}, "metadata": {}},
    ]}), encoding="utf-8")
    got = sr.refined_defects(str(tmp_path / "*.json"))
    assert got == [{"entry_id": "x", "issue": 161, "refined": "spurious guard dropped"}]
```

- [x] **Step 2: Run to verify they fail**

Run: `cd probe && python3 -m pytest test_sweep_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sweep_report'`

- [x] **Step 3: Implement**

```python
# probe/sweep_report.py
"""Aggregate necessity-sweep records into the tables the paper reports.

Two rules the numbers depend on, both enforced here rather than in the prose:
a binder counts only when its entry passed the power control, and a daemon error
counts nowhere. A rate over an unreachable population would be an artifact of the
instrument's blindness, which is exactly the misreading the paper has to prevent.
"""
from __future__ import annotations

import collections
import glob as _glob
import json

__all__ = ["rates", "refined_defects", "render_report", "load_records"]


def load_records(path_glob: str) -> list[dict]:
    out = []
    for p in sorted(_glob.glob(path_glob)):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def rates(records) -> dict:
    """Keyed by (arm, domain, status). `rate` is None when nothing was probed —
    distinct from 0.0, which means probed and nothing found."""
    reachable: dict[tuple, set] = collections.defaultdict(set)
    blind: dict[tuple, set] = collections.defaultdict(set)
    probed: collections.Counter = collections.Counter()
    certified: collections.Counter = collections.Counter()
    for r in records:
        key = (r["arm"], r["domain"], r["status"])
        if r["verdict"] == "power_control":
            (reachable if r["sweep_proves_original"] else blind)[key].add(r["entry_id"])
            continue
        if r["verdict"] == "daemon_error" or not r["sweep_proves_original"]:
            continue
        if r["verdict"] == "free_filter_rejected":
            continue
        probed[key] += 1
        if r["verdict"] == "certified_unnecessary":
            certified[key] += 1
    out = {}
    for key in set(reachable) | set(blind) | set(probed):
        n, c = probed[key], certified[key]
        out[key] = {"probed": n, "certified": c,
                    "reachable_entries": len(reachable[key]),
                    "blind_entries": len(blind[key]),
                    "rate": (c / n) if n else None}
    return out


def refined_defects(bench_glob: str) -> list[dict]:
    """Entries whose provenance records what human review changed about the machine's
    statement — the labelled set of defects the automated gates passed."""
    out = []
    for p in sorted(_glob.glob(bench_glob)):
        with open(p, encoding="utf-8") as f:
            payload = json.load(f)
        for e in payload.get("theorems", []):
            prov = ((e.get("metadata") or {}).get("provenance")) or {}
            if prov.get("refined"):
                out.append({"entry_id": e.get("id", ""), "issue": prov.get("issue"),
                            "refined": prov["refined"]})
    return out


def render_report(rate_table: dict, defects: list[dict]) -> str:
    lines = ["# Necessity sweep — results", "",
             "`rate` = certified-unnecessary / probed, over entries the sweep can prove",
             "at all. `blind` entries are excluded from the rate and reported so a low",
             "rate is not mistaken for a clean library.", "",
             "| arm | domain | status | reachable | blind | probed | certified | rate |",
             "|---|---|---|---|---|---|---|---|"]
    for (arm, domain, status), v in sorted(rate_table.items()):
        rate = "n/a" if v["rate"] is None else f"{100*v['rate']:.1f}%"
        lines.append(f"| {arm} | {domain} | {status} | {v['reachable_entries']} | "
                     f"{v['blind_entries']} | {v['probed']} | {v['certified']} | {rate} |")
    lines += ["", "## Defects human review caught that every gate passed", "",
              "| entry | issue | what review changed |", "|---|---|---|"]
    for d in defects:
        lines.append(f"| `{d['entry_id']}` | {d['issue']} | {d['refined']} |")
    return "\n".join(lines) + "\n"
```

- [x] **Step 4: Run to verify they pass**

Run: `cd probe && python3 -m pytest test_sweep_report.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Generate the report**

Run:
```bash
cd probe && python3 -c "
import sweep_report as sr
recs = sr.load_records('../runs/necessity-sweep/*.jsonl')
d = sr.refined_defects('../../formal-mathfin/benchmarks/*.json')
open('../runs/necessity-sweep/report.md','w').write(sr.render_report(sr.rates(recs), d))
print('wrote report.md over', len(recs), 'records')
"
```

- [ ] **Step 6: Run the whole probe suite to confirm nothing regressed**

Run: `cd probe && python3 -m pytest -q`
Expected: all pre-existing tests still pass, plus the new ones.

- [ ] **Step 7: Commit**

```bash
git add probe/sweep_report.py probe/test_sweep_report.py runs/necessity-sweep/report.md
git commit -m "feat(report): rates with the blind fraction beside them, and the refined-defect table"
```

---

### Task 9: The paper

**Files:**
- Create: `docs/superpowers/papers/vericodegen-necessity.tex`

**Interfaces:**
- Consumes: `runs/necessity-sweep/report.md`, `runs/obstructions-report.md`, `runs/refill-history.jsonl`, and the spec's §4.

- [ ] **Step 1: Fetch the workshop template**

Download `neurips_2026_vericode_workshop.tex` and `neurips_2026_vericode.sty` from the Overleaf link in the CFP (`https://www.overleaf.com/read/fhmwmtrgwkmc#4d5413`) into `docs/superpowers/papers/`. Match the existing convention in that directory: `\pdfoutput=1` on line 1, inline `thebibliography`, no `.bib` pass.

- [ ] **Step 2: Draft the sections against the measured numbers**

Follow spec §5's structure and page budget. Every number comes from `report.md` — none typed by hand. Do not write the results section before Task 8 has produced the report; a placeholder number here is the single most likely way this paper goes out wrong.

- [ ] **Step 3: Anonymize**

Per spec §6: third-person self-citation throughout, no repo URLs in the body, artifact referenced as an anonymized mirror, no acknowledgements. Verify with:

```bash
grep -niE "coelho|raphael|formal-applied-math|github.com/|orcid|our previous" \
  docs/superpowers/papers/vericodegen-necessity.tex
```
Expected: no output.

- [ ] **Step 4: Compile clean**

Run: `cd docs/superpowers/papers && pdflatex vericodegen-necessity.tex && pdflatex vericodegen-necessity.tex`
Expected: 0 undefined references, 0 undefined citations, main text within 4–8 pages.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/papers/vericodegen-necessity.tex
git commit -m "docs(paper): the VeriCodeGen submission, written against the measured sweep"
```

---

## Self-review against the spec

- §1 (the claim) → Task 8's `refined_defects` supplies the labelled evidence; Tasks 6–7 supply the base rates. Covered.
- §2 (instrument, population, pre-filter) → Tasks 1–4. The measured population (348/320/689) is pinned as an assertion in Task 1 Step 5. Covered.
- §2 stratification by faithfulness status → carried on every record and keyed into `rates`. Covered.
- §2 lower-bound semantics → encoded as the `not_shown_unnecessary` verdict name and enforced in `rates`. Covered.
- §2 per-domain reporting and "reach" → superseded by the stronger power control (Task 2), reported as `blind_entries`. **The spec says "reach"; the plan implements a per-theorem power control. Amend spec §2 to match this plan's definition before execution.**
- §2.1 (two populations kept separate) → the draft population is `runs/*.candidate` plus `refined_defects`; the corpus population is Tasks 6–7. Covered, but note the drafts are only 16 files, so the draft-side claim rests on the four `refined` records.
- §2.2 (Mathlib arm) → Task 7, gated by Task 5's decision rule. Covered.
- §3 (limitations) → prose, Task 9.
- §4 (supporting sections) → prose, Task 9, from already-recorded telemetry. No new runs needed.
- §6 (anonymization) → Task 9 Step 3, with a mechanical grep check. Covered.
- §7 (arena) → **not in this plan by design.** Separate plan: `2026-08-17-lean-refactor-arena-prep.md`.
