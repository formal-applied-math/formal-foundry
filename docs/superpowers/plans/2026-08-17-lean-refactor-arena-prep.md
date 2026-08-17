# Lean Refactor Arena — Preparation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get formal-mathfin proofs into the Lean Refactor Arena benchmark (cheap, near-certain value), and decide on measured evidence whether to enter the competition itself (expensive, and the bar is a published method).

**Architecture:** Two independent lanes with different economics. Lane A contributes proofs to the benchmark — we already hold exactly what it is asking for. Lane B measures our refinery against the published SOTA on our own corpus before committing October to it, and is abandoned if the measurement says so.

**Tech Stack:** Lean v4.32.0, `lake build` timing, the `mathfin-verify` image, Python 3.12 stdlib.

**Spec:** `docs/superpowers/specs/2026-08-17-vericodegen-submission-design.md` §7

## Global Constraints

- **The rules are unverified.** `leanrefactor.github.io` and the HF contribution page both render client-side and returned nothing to automated fetch. Everything here derives from the workshop page and from search results. **Task 1 is verification, and no other task starts until it completes.**
- **Known SOTA:** Lean Refactor (arXiv:2605.20244) reports >70% token-level compression and up to 60% compile-time reduction via retrieval over a curated strategy database, steering a frozen agentic LLM. Any Lane B claim of ours is measured against that, not against unrefactored proofs.
- **Provisional numbers:** prize amounts and benchmark size may change before 2026-09-01. Re-check before relying on either.
- **Deadlines:** benchmark release 2026-10-01, submission 2026-11-08, winners 2026-11-22. Lane A should land before the benchmark freezes.
- **Zero API spend without a fresh decision from R.** Lane B's baseline uses the refinery, which is a Claude-driven loop; running it costs tokens. Task 5 stops for authorization before spending.

---

### Task 1: Verify the rules (blocking)

**Files:**
- Create: `docs/research/2026-08-17-lean-refactor-arena-rules.md`

- [ ] **Step 1: Read the pages a browser can see**

The two arena pages are client-rendered. Use the Playwright MCP browser rather than fetch:
navigate to `https://leanrefactor.github.io/` and to
`https://delta-lab-ai-lean-refactor-arena.hf.space/contribute`, snapshot each, and read
the rendered text.

- [ ] **Step 2: Record the answers to exactly these questions**

Write `docs/research/2026-08-17-lean-refactor-arena-rules.md` answering:
1. What artifact does a competitor submit — a model, an agent, a patch set, or per-problem outputs?
2. What are the two tracks' budgets, in concrete units (tokens? wall-clock? API calls?)
3. How is "portability across toolchain versions" scored, and against which versions?
4. What is the contribution format for donating proofs, and is there a deadline?
5. Is donating proofs compatible with competing, or does it disqualify?
6. Licensing: what license must a contributed proof carry? (formal-mathfin is Apache-2.0.)

Any question the pages do not answer is recorded as **unanswered** and, if it is 1, 2, or 5, emailed to the competition chairs (Pant, Frieder, Lu) before Lane B starts.

- [ ] **Step 3: Commit**

```bash
git add docs/research/2026-08-17-lean-refactor-arena-rules.md
git commit -m "docs(research): the arena's actual rules, read from the rendered pages"
```

---

### Task 2: Find our most donatable proofs

**Files:**
- Create: `probe/refactor_candidates.py`
- Test: `probe/test_refactor_candidates.py`

**Interfaces:**
- Produces: `rank_candidates(root: str, timings: dict[str, float]) -> list[dict]` — modules ranked by donation value, each `{module, lines, decls, longest_proof_lines, build_s, score}`.

**The arena wants** long proofs with room to shorten, expensive compilation, and cross-toolchain stability. We have those: the Itô tower is 62 modules and ~20,000 lines. This task finds which specific ones score highest, rather than donating by intuition.

- [ ] **Step 1: Write the failing test**

```python
# probe/test_refactor_candidates.py
import refactor_candidates as rc

SRC = '''theorem short : True := trivial

theorem long (n : Nat) : n + 0 = n := by
  induction n with
  | zero => simp
  | succ k ih =>
    rw [Nat.add_zero]
'''


def test_measures_the_longest_proof_in_a_module(tmp_path):
    (tmp_path / "M.lean").write_text(SRC, encoding="utf-8")
    got = rc.rank_candidates(str(tmp_path), timings={"M": 12.0})
    assert len(got) == 1
    row = got[0]
    assert row["module"] == "M"
    assert row["decls"] == 2
    assert row["longest_proof_lines"] == 5      # `long` spans five lines
    assert row["build_s"] == 12.0


def test_ranking_prefers_long_and_expensive(tmp_path):
    (tmp_path / "Cheap.lean").write_text("theorem a : True := trivial\n", encoding="utf-8")
    (tmp_path / "Dear.lean").write_text(SRC, encoding="utf-8")
    got = rc.rank_candidates(str(tmp_path), timings={"Cheap": 1.0, "Dear": 60.0})
    assert [r["module"] for r in got] == ["Dear", "Cheap"]


def test_module_with_no_timing_scores_but_does_not_crash(tmp_path):
    (tmp_path / "M.lean").write_text(SRC, encoding="utf-8")
    got = rc.rank_candidates(str(tmp_path), timings={})
    assert got[0]["build_s"] is None and got[0]["score"] >= 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd probe && python3 -m pytest test_refactor_candidates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'refactor_candidates'`

- [ ] **Step 3: Implement**

```python
# probe/refactor_candidates.py
"""Rank our own modules by how much the Lean Refactor Arena would want them.

The arena's stated criteria are long proofs (room to shorten), expensive compilation,
and stability across toolchains. The first two are measurable from the sources and a
build; the third is a property of this library generally, since every module is pinned
and re-elaborated in CI on every commit.
"""
from __future__ import annotations

import os
import re

__all__ = ["rank_candidates", "proof_spans"]

_DECL = re.compile(r"^(?:@\[[^\]]*\]\s*\n)?(?:private\s+|protected\s+|nonrec\s+)?"
                   r"(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)", re.M)


def proof_spans(src: str) -> list[tuple[str, int]]:
    """(name, line count) for each top-level theorem/lemma, where the span runs to the
    next top-level declaration."""
    starts = [(m.start(), m.group(1)) for m in _DECL.finditer(src)]
    out = []
    for i, (pos, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(src)
        out.append((name, src[pos:end].rstrip().count("\n") + 1))
    return out


def rank_candidates(root: str, timings: dict[str, float]) -> list[dict]:
    """Modules under `root` ranked by donation value.

    Score is the product of the longest proof's length and the build cost, because the
    arena wants both at once: a long proof that compiles instantly has little to win, and
    an expensive module of one-liners has nothing to shorten. A module with no timing
    scores on length alone rather than being dropped."""
    rows = []
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
            spans = proof_spans(src)
            longest = max((n for _name, n in spans), default=0)
            build_s = timings.get(module)
            rows.append({"module": module, "lines": src.count("\n") + 1,
                         "decls": len(spans), "longest_proof_lines": longest,
                         "build_s": build_s,
                         "score": longest * (build_s if build_s is not None else 1.0)})
    rows.sort(key=lambda r: (-r["score"], r["module"]))
    return rows
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd probe && python3 -m pytest test_refactor_candidates.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add probe/refactor_candidates.py probe/test_refactor_candidates.py
git commit -m "feat(arena): rank our modules by what the benchmark says it wants"
```

---

### Task 3: Measure real build costs and pick the donation set

**Files:**
- Create: `runs/arena/module-timings.json`
- Create: `runs/arena/donation-set.md`

**Precondition:** the Lean slot is free (no `mathfin-verify` build container, no REPL daemon).

- [ ] **Step 1: Time a clean build per module**

Run, from the formal-mathfin checkout:
```bash
cd /mnt/c/Users/rapha/Documents/Code/formal-mathfin
lake clean
/usr/bin/time -v lake build MathFin 2>&1 | tee /tmp/build.log
lake build --no-build MathFin -v 2>&1 | tee /tmp/modules.log
```
If Lake does not expose per-module timings directly, time the modules of interest individually with `lake build MathFin.Ito.Integral` after a `lake clean`, and record each.

- [ ] **Step 2: Write the timings and rank**

```bash
cd /mnt/c/Users/rapha/Documents/Code/mathfin-foundry/probe && python3 -c "
import json, refactor_candidates as rc
timings = json.load(open('../runs/arena/module-timings.json'))
rows = rc.rank_candidates('/mnt/c/Users/rapha/Documents/Code/formal-mathfin/MathFin', timings)
for r in rows[:20]:
    print(f\"{r['module']:52s} lines={r['lines']:5d} longest={r['longest_proof_lines']:4d} build={r['build_s']} score={r['score']:.0f}\")
"
```

- [ ] **Step 3: Write the donation set**

`runs/arena/donation-set.md` names the modules we propose to donate, with their measured
lines / longest proof / build seconds, and one sentence each on why the arena would want
it. Cross-check against Task 1's answer to question 6 (licensing) before proposing any.

- [ ] **Step 4: Commit**

```bash
git add runs/arena/
git commit -m "measure(arena): per-module build cost, and the donation set it implies"
```

---

### Task 4: Submit the donation

- [ ] **Step 1: Package per Task 1's answer to question 4**

Format follows whatever the contribution guide specifies. If it wants a PR to a repo,
open one from a branch; if it wants a form, fill it.

- [ ] **Step 2: Confirm the Apache-2.0 terms are compatible and attribution is right**

The library is Apache-2.0 with a `CITATION.cff` and a Zenodo DOI. The donation must carry
attribution consistent with those, and must not be donated under terms the license does
not permit.

- [ ] **Step 3: Record the submission**

Append to `runs/arena/donation-set.md`: what was submitted, when, to where, and any
tracking id.

- [ ] **Step 4: Commit**

```bash
git add runs/arena/donation-set.md
git commit -m "chore(arena): donation submitted"
```

---

### Task 5: Lane B go/no-go — does our refinery beat the published baseline?

**Files:**
- Create: `runs/arena/refinery-baseline.md`

**STOP FOR AUTHORIZATION.** The refinery is a Claude-driven loop and running it spends
tokens. The Global Constraints forbid that without a fresh decision from R. Present the
expected cost from Task 3's module sizes and wait.

- [ ] **Step 1: Pick ten proofs spanning the difficulty range**

From Task 3's ranking: the three highest-scoring, four mid-range, three short. Spanning
the range matters because the published baseline reports a single aggregate compression
number, and an unrepresentative sample of ours would not be comparable to it.

- [ ] **Step 2: Record the before state**

For each: token count of the proof body, `lake build` wall-clock for its module from a
clean state, and the Lean version it is pinned to.

- [ ] **Step 3: Run the refinery on each and record the after state**

Same three measurements. The refinery must preserve the statement exactly — a "refactor"
that changes what is proved is not a refactor. Verify with `#print axioms` unchanged and
the statement string identical.

- [ ] **Step 4: Compare against the published numbers, honestly**

Lean Refactor reports >70% token compression and up to 60% compile-time reduction.
Write `runs/arena/refinery-baseline.md` with our measured medians beside theirs.

**Decision rule, fixed now:**
- Our median token compression **≥ 50%** with statements provably unchanged → enter, and
  the tech report is about what the house-idiom bar contributes beyond raw compression.
- Between 25% and 50% → enter only if Task 1 established that the tracks score something
  other than compression alone (portability, or elaboration time) where we do better.
- **< 25% → do not enter.** Write the negative result into `runs/arena/refinery-baseline.md`
  and spend November on the library instead. A losing entry costs the same October as a
  winning one.

- [ ] **Step 5: Commit**

```bash
git add runs/arena/refinery-baseline.md
git commit -m "measure(arena): the refinery against the published baseline, and the entry decision"
```

---

## Self-review against the spec

- §7 "confirm the rules and register for the warm-up" → Task 1, made blocking. Covered.
- §7 "build a measurement harness over our own corpus for the three metrics" → Tasks 2–3
  cover proof length and build cost. **Cross-toolchain portability is not directly
  measured**; it is argued from the library's pinned-CI property. If Task 1's answer to
  question 3 shows portability is scored heavily, a fourth task is needed to measure it
  by building against a second toolchain.
- §7 "baseline: run the refinery and record the deltas" → Task 5. Covered.
- §7 gate ("if the baseline shows no reliable improvement, we do not enter") → Task 5's
  decision rule, with the threshold fixed before the measurement rather than after.
- **New, not in the spec:** donating proofs to the benchmark (Tasks 2–4). Search turned up
  that the arena is actively soliciting exactly the kind of proof this library holds, and
  it is cheap and independent of whether we compete. Spec §7 should be amended to include
  Lane A.
