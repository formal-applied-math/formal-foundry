# Hands-off autoform → PR pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the foundry's Leanstral pipeline open ready-for-review PRs on `raphaelrrcoelho/formal-mathfin` that resolve the open autoformalization issues and expand the formalization program, with R as the sole merge gate.

**Architecture:** The 91 labeled issues on formal-mathfin ARE the backlog (each issue's *Task* = the statement, *Pointers* = the context-pack modules, and a merged PR *Closes* it). A tick selects the next tractable `status:ready` `type:proof` issue → a seeded faithful stub → the pass@k probe proves it against the lean-repl daemon → `assemble.py` places the proof in a real `MathFin/` module + a re-export benchmark entry → `open-pr.sh` validates+regenerates in the foundry CI runner and opens a green PR that closes the issue. All pure logic is TDD'd behind injected `chat_fn`/`check_fn` seams; all heavy Lean build runs in CI, never on the 10 GB box.

**Tech Stack:** Python 3.11+ stdlib only (no deps in probe code); `lean-interact` daemon over TCP :7878 (existing); `gh` CLI for issues/PRs; GitHub Actions (foundry cron); Docker `mathfin-verify` image (baked oleans) for CI build/regen.

## Global Constraints

- Python **stdlib only** in `probe/` (no third-party imports); Python **3.11+** (`tomllib`).
- **TDD**: every pure function gets a failing test first; tests use injected `chat_fn`/`check_fn`/temp trees — **no test may touch Lean, the Mistral API, or the network**.
- **One Lean process** on the local box; all `lake build`/regen runs in **foundry CI** (16 GB runner), never locally.
- **Values/slop gate** unchanged: candidate proofs must be axioms-clean `[propext, Classical.choice, Quot.sound]` and free of `sorry/admit/native_decide/polyrith/exact?/apply?/hint`.
- **No Claude attribution anywhere** (commits, PR bodies, branches) — no `Co-Authored-By`, no "Generated with".
- **Specific `git add` only** — never `git add -A` / `git add .`.
- **PR bodies in R's outbound style**: lowercase, "i", short sentences, no marketing, no em-dashes.
- Main repo `formal-mathfin` stays **independent**: the foundry reads it and contributes only via PRs its own CI judges. The foundry gains write access solely through `MAIN_PR_TOKEN` (revocable).
- Work in `~/code/mathfin-foundry`; main checkout path is `${MAIN_REPO:-/home/rapha/code/automated_proofs_quantfin}`.

---

## Phase 0 — Prover endpoint

### Task 0: Confirm the live Leanstral endpoint + model id

**Files:**
- Modify: `probe/probe.py:36` (`DEFAULT_MODEL`)
- Modify: `pipeline.toml` (add `model` if the endpoint changed)

- [ ] **Step 1:** WebFetch `https://docs.mistral.ai/getting-started/models/models_overview/` (and the Leanstral model card) and confirm the current free Lean endpoint id — the research flagged a possible `labs-leanstral-2603` alongside our `labs-leanstral-1-5`. Record the authoritative id.
- [ ] **Step 2:** If it differs, set `DEFAULT_MODEL` in `probe/probe.py` and add `model = "<id>"` under `[pipeline]` in `pipeline.toml`; otherwise leave a one-line comment noting `labs-leanstral-1-5` was reconfirmed on 2026-07-11.
- [ ] **Step 3:** Commit.

```bash
git add probe/probe.py pipeline.toml
git commit -m "chore(probe): reconfirm Leanstral endpoint id (2026-07-11)"
```

---

## Phase 1 — The functional issue → PR pipeline (the deliverable)

### Task 1: `issues.py` — select the next tractable issue

**Files:**
- Create: `probe/issues.py`
- Test: `probe/test_issues.py`

**Interfaces:**
- Produces: `select_issues(raw: list[dict], *, max_difficulty: str = "medium") -> list[dict]` returning issue dicts `{number, title, area, difficulty, pointers_hint}` filtered to `status:ready` + `type:proof`, excluding `status:blocked-*` and `type:research`, ordered good-first → small → medium; `parse_labels(issue) -> dict` mapping the label list to `{status, type, difficulty, area}`.

- [ ] **Step 1: Write the failing test** (`probe/test_issues.py`):

```python
from issues import select_issues, parse_labels

def _iss(n, labels, title="t"):
    return {"number": n, "title": title, "labels": [{"name": l} for l in labels]}

def test_parse_labels_extracts_dimensions():
    d = parse_labels(_iss(1, ["status:ready", "type:proof", "difficulty:small", "area:fx"]))
    assert d == {"status": "ready", "type": "proof", "difficulty": "small", "area": "fx"}

def test_select_filters_and_orders_by_difficulty():
    raw = [
        _iss(1, ["status:ready", "type:proof", "difficulty:medium", "area:risk"]),
        _iss(2, ["status:ready", "type:proof", "difficulty:small", "area:fx"]),
        _iss(3, ["status:ready", "type:proof", "good first issue", "area:fx"]),
        _iss(4, ["status:blocked-design", "type:proof", "difficulty:small"]),  # blocked -> out
        _iss(5, ["status:ready", "type:research", "difficulty:hard"]),          # research -> out
        _iss(6, ["status:ready", "type:proof", "difficulty:hard", "area:risk"]),# too hard -> out
    ]
    got = [i["number"] for i in select_issues(raw, max_difficulty="medium")]
    assert got == [3, 2, 1]  # good-first, then small, then medium; 4/5/6 excluded
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd probe && python3 -m pytest test_issues.py -q`
Expected: FAIL (`ModuleNotFoundError: issues`).

- [ ] **Step 3: Implement `probe/issues.py`**

```python
"""Select the next autoformalization target from the formal-mathfin issue backlog.

The 91 labeled issues ARE the curated backlog: each `type:proof` `status:ready`
issue carries the statement (its "Task") and the context modules (its "Pointers").
`scripts/*.sh` fetch them with `gh issue list ... --json number,title,labels,body`;
this module (pure, stdlib) does the label parsing + tractability ordering. The
prose body → stub authoring is a separate step (hand-seeded in Phase 1; the
autoformalize sub-probe automates it in the follow-on plan)."""
from __future__ import annotations

# difficulty rank: lower = attempt first. good-first/small/medium are in scope;
# hard is out until the harness proves out on easier targets.
_DIFF_RANK = {"good-first": 0, "small": 1, "medium": 2}
_GOOD_FIRST_TITLE = "good first issue"  # GitHub's built-in label


def parse_labels(issue: dict) -> dict:
    dims = {"status": None, "type": None, "difficulty": None, "area": None}
    for lab in issue.get("labels", []):
        name = lab["name"] if isinstance(lab, dict) else str(lab)
        if name == _GOOD_FIRST_TITLE:
            dims["difficulty"] = dims["difficulty"] or "good-first"
            continue
        for key in ("status", "type", "difficulty", "area"):
            prefix = key + ":"
            if name.startswith(prefix):
                dims[key] = name[len(prefix):]
    return dims


def _rank(issue: dict) -> int | None:
    d = parse_labels(issue)
    if d["status"] != "ready" or d["type"] != "proof":
        return None
    return _DIFF_RANK.get(d["difficulty"])


def select_issues(raw: list[dict], *, max_difficulty: str = "medium") -> list[dict]:
    cap = _DIFF_RANK.get(max_difficulty, 2)
    scored = []
    for issue in raw:
        r = _rank(issue)
        if r is None or r > cap:
            continue
        d = parse_labels(issue)
        scored.append((r, issue["number"], {
            "number": issue["number"], "title": issue.get("title", ""),
            "area": d["area"], "difficulty": d["difficulty"],
        }))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [s[2] for s in scored]
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd probe && python3 -m pytest test_issues.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add probe/issues.py probe/test_issues.py
git commit -m "feat(probe): issue-backlog selector (status:ready type:proof, difficulty-ordered)"
```

### Task 2: `build_manifest.py` — parse placement metadata

**Files:**
- Modify: `probe/build_manifest.py:25-32` (add metadata parsers) and `:66-72` (attach to target)
- Test: `probe/test_build_manifest.py` (extend)

**Interfaces:**
- Produces: `parse_meta(code: str) -> dict` reading the stub's comment header lines `-- main-module:`, `-- benchmark:`, `-- benchmark-id:`, `-- source-issue:` into `{main_module, benchmark, benchmark_id, source_issue}` (missing keys omitted). `parse_pointers` unchanged.

- [ ] **Step 1: Write the failing test** (append to `probe/test_build_manifest.py`):

```python
from build_manifest import parse_meta

def test_parse_meta_reads_placement_header():
    code = (
        "-- pointers: MathFin/BlackScholes/Forward.lean\n"
        "-- main-module: MathFin/FX/InterestRateParity.lean\n"
        "-- benchmark: benchmarks/mathematical_finance.json\n"
        "-- benchmark-id: mf-fx-interest-rate-parity\n"
        "-- source-issue: 108\n"
        "theorem t : True := by sorry\n"
    )
    assert parse_meta(code) == {
        "main_module": "MathFin/FX/InterestRateParity.lean",
        "benchmark": "benchmarks/mathematical_finance.json",
        "benchmark_id": "mf-fx-interest-rate-parity",
        "source_issue": 108,
    }

def test_parse_meta_empty_when_absent():
    assert parse_meta("theorem t : True := by sorry\n") == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd probe && python3 -m pytest test_build_manifest.py -q`
Expected: FAIL (`ImportError: cannot import name 'parse_meta'`).

- [ ] **Step 3: Implement** — add to `probe/build_manifest.py` after `parse_pointers`:

```python
_META_KEYS = {
    "main-module": ("main_module", str),
    "benchmark": ("benchmark", str),
    "benchmark-id": ("benchmark_id", str),
    "source-issue": ("source_issue", lambda s: int(s.lstrip("#"))),
}


def parse_meta(code: str) -> dict:
    """Placement metadata a stub declares via comment header lines:
        -- main-module: MathFin/FX/InterestRateParity.lean
        -- benchmark: benchmarks/mathematical_finance.json
        -- benchmark-id: mf-fx-interest-rate-parity
        -- source-issue: 108
    Returns only the keys present."""
    out: dict = {}
    for raw_key, (dest, cast) in _META_KEYS.items():
        m = re.search(rf"--\s*{re.escape(raw_key)}:\s*(.+)", code)
        if m:
            out[dest] = cast(m.group(1).strip())
    return out
```

Then in the target-assembly loop (where `pointers` is attached, ~line 68), add `**parse_meta(code)` into the target dict:

```python
        targets.append({
            "id": fname[:-5], "stream": STREAMS[m.group(1)], "kind": "prove",
            "sorry_name": decl.group(1), "file": fname,
            "pointers": parse_pointers(code),
            "input_hash": sha256_hex(code + toolchain),
            **parse_meta(code),
        })
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd probe && python3 -m pytest test_build_manifest.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add probe/build_manifest.py probe/test_build_manifest.py
git commit -m "feat(manifest): parse placement metadata (main-module/benchmark/source-issue)"
```

### Task 3: `assemble.py` — place proof + append benchmark entry (clean diff)

**Files:**
- Create: `probe/assemble.py`
- Test: `probe/test_assemble.py`

**Interfaces:**
- Consumes: target dict from Task 2 (`main_module`, `benchmark`, `benchmark_id`, `source_issue`), a `benchmark_entry` dict (authored in the seed manifest, Task 4).
- Produces: `apply_contribution(candidate_code, target, benchmark_entry, main_repo) -> list[str]` (list of repo-relative paths written); `append_entry(benchmark_text, entry) -> str` (pure string transform preserving the file's existing formatting + trailing-newline convention).

- [ ] **Step 1: Write the failing test** (`probe/test_assemble.py`):

```python
import json, os, tempfile
from assemble import apply_contribution, append_entry

def test_append_entry_preserves_formatting_and_trailing_newline():
    original = ('{\n  "description": "d",\n  "theorems": [\n'
                '    {\n      "id": "a"\n    }\n  ]\n}\n')  # trailing newline
    out = append_entry(original, {"id": "b", "name": "B"})
    data = json.loads(out)
    assert [t["id"] for t in data["theorems"]] == ["a", "b"]
    assert out.endswith("}\n")                      # newline preserved
    assert '    {\n      "id": "a"\n    },' in out    # existing entry byte-preserved (now comma'd)

def test_append_entry_no_trailing_newline_preserved():
    original = '{\n  "theorems": [\n    {\n      "id": "a"\n    }\n  ]\n}'  # NO trailing newline
    out = append_entry(original, {"id": "b"})
    assert not out.endswith("\n")

def test_apply_contribution_writes_module_and_entry():
    with tempfile.TemporaryDirectory() as main:
        os.makedirs(os.path.join(main, "benchmarks"))
        with open(os.path.join(main, "benchmarks", "x.json"), "w") as f:
            f.write('{\n  "theorems": [\n    {\n      "id": "old"\n    }\n  ]\n}\n')
        target = {"main_module": "MathFin/FX/IRP.lean",
                  "benchmark": "benchmarks/x.json", "benchmark_id": "mf-irp"}
        entry = {"id": "mf-irp", "name": "IRP", "code": {"lean": "..."}}
        written = apply_contribution("theorem foo : True := trivial\n",
                                     target, entry, main)
        assert "MathFin/FX/IRP.lean" in written
        assert open(os.path.join(main, "MathFin/FX/IRP.lean")).read().startswith("theorem foo")
        data = json.load(open(os.path.join(main, "benchmarks", "x.json")))
        assert [t["id"] for t in data["theorems"]] == ["old", "mf-irp"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd probe && python3 -m pytest test_assemble.py -q`
Expected: FAIL (`ModuleNotFoundError: assemble`).

- [ ] **Step 3: Implement `probe/assemble.py`**

```python
"""Turn a proven candidate into edits on a formal-mathfin checkout: the real
proof in a MathFin module + a re-export benchmark entry. Pure file ops (no git,
no network) so it is testable on a temp tree. `open-pr.sh` owns branch/commit/PR.

Benchmark JSON is appended with a minimal-diff string transform: the corpus is
indent=2 / ensure_ascii=False, so re-dumping keeps existing entries byte-stable;
we only add the trailing comma + the new object and preserve the file's own
trailing-newline convention (some files have it, some don't)."""
from __future__ import annotations

import json
import os


def append_entry(benchmark_text: str, entry: dict) -> str:
    had_newline = benchmark_text.endswith("\n")
    data = json.loads(benchmark_text)
    key = "theorems" if isinstance(data, dict) and "theorems" in data else None
    if key is None:  # bare-list file
        data = data if isinstance(data, list) else []
        data.append(entry)
        out = json.dumps(data, indent=2, ensure_ascii=False)
    else:
        data[key].append(entry)
        out = json.dumps(data, indent=2, ensure_ascii=False)
    return out + "\n" if had_newline else out


def apply_contribution(candidate_code: str, target: dict, benchmark_entry: dict,
                       main_repo: str) -> list[str]:
    written: list[str] = []
    mod_rel = target["main_module"]
    mod_abs = os.path.join(main_repo, mod_rel)
    os.makedirs(os.path.dirname(mod_abs), exist_ok=True)
    with open(mod_abs, "w", encoding="utf-8") as f:
        f.write(candidate_code if candidate_code.endswith("\n") else candidate_code + "\n")
    written.append(mod_rel)

    bench_rel = target["benchmark"]
    bench_abs = os.path.join(main_repo, bench_rel)
    text = open(bench_abs, encoding="utf-8").read()
    with open(bench_abs, "w", encoding="utf-8") as f:
        f.write(append_entry(text, benchmark_entry))
    written.append(bench_rel)
    return written
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd probe && python3 -m pytest test_assemble.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add probe/assemble.py probe/test_assemble.py
git commit -m "feat(assemble): place proof in MathFin module + append re-export benchmark entry (clean diff)"
```

### Task 4: Seed the first batch of issue-derived stubs

**Files:**
- Create: `targets/queue/cal-*.lean` (6–8 stubs) + `targets/queue/manifest.json` (via `build_manifest.py`)
- Reference: issue bodies via `gh issue view <n> --repo raphaelrrcoelho/formal-mathfin`

**Selection bar** (all must hold): label `status:ready` + `type:proof`; difficulty `good-first`/`small`; the *Task* is a closed-form/algebra/`Real.exp`-monotonicity statement; the *Pointers* name existing MathFin lemmas to consume. First batch (confirmed good-first, `status:ready`): **#109** Siegel's paradox (fx), **#108** interest-rate parity (fx), **#88** contango/backwardation (futures), **#85** premium principles (actuarial), **#53** knock-in/out parity (black-scholes), **#66** IR-swap value/par rate (fixed-income), **#67** FRA value/simple forward (fixed-income). Skip `type:research` and `status:blocked-*`.

**Per-issue procedure** (worked example below is real):

- [ ] **Step 1:** `gh issue view <n> --repo raphaelrrcoelho/formal-mathfin --json title,body`. Read the *Task* (the statement) and *Pointers* (context modules).
- [ ] **Step 2:** Author `targets/queue/cal-bk-<n>.lean` as a MathFin module stub: license header, `module`, `public import`s (Mathlib + the pointer modules), a `## Result` docstring, `@[expose] public section`, `namespace MathFin`, one faithful `theorem … := by sorry`, `end MathFin` — plus the placement header comment lines. Faithfulness is R's to confirm at merge; author conservatively (state exactly the issue's formula, no stronger).

Worked example — `targets/queue/cal-bk-109.lean` (Siegel's paradox, #109):

```lean
/-
Copyright (c) 2026 Raphael Coelho. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Raphael Coelho
-/
module

public import Mathlib
public import MathFin.BlackScholes.LognormalMoments
public import MathFin.BlackScholes.Forward

-- pointers: MathFin/BlackScholes/LognormalMoments.lean, MathFin/BlackScholes/Forward.lean
-- main-module: MathFin/FX/SiegelParadox.lean
-- benchmark: benchmarks/mathematical_finance.json
-- benchmark-id: mf-fx-siegel-paradox
-- source-issue: 109

/-!
# Siegel's paradox: the reciprocal-rate convexity correction

For lognormal terminal `S_T` under the domestic risk-neutral measure,
`E[S_T]·E[1/S_T] = e^{σ²T} > 1` for `σ²T > 0` — the strict Jensen gap between a
rate and its reciprocal. `E[1/S_T]` is the `p = -1` standard-normal MGF.
-/

@[expose] public section

namespace MathFin

/-- Siegel's paradox: the product of a lognormal rate's mean and its reciprocal's
mean is `e^{σ²T}`. -/
theorem siegel_paradox_product
    {S₀ r σ T : ℝ} (hσ : 0 < σ) (hT : 0 < T) :
    True := by      -- REPLACE with the faithful statement from #109 using
  sorry             -- nthMoment_terminal (p=1 and p=-1) and expected_terminal_eq_forward

end MathFin
```

  (Execution note: the `True` placeholder in the worked example is a scaffold — the real stub states `E_Q[S_T] * E_Q[1/S_T] = Real.exp (σ^2 * T)` in the codomain the pointer lemmas use. Confirm the exact `nthMoment_terminal` signature from the lean_scout index / the pointer file before finalizing.)

- [ ] **Step 3:** Bring the daemon up (main repo) and run `python3 build_manifest.py --main-repo "$MAIN_REPO"` — it validates each stub elaborates with exactly one `sorry` and writes `targets/queue/manifest.json` with pointers + placement metadata. (This is the one Phase-1 step that needs the daemon; it does not spend Leanstral tokens.)
- [ ] **Step 4:** Hand-author each target's `benchmark_entry` (the re-export snippet) into `targets/queue/manifest.json` under a `benchmark_entry` key — mirroring the `mf-bs-put-formula` shape: `code.lean` = `import MathFin.<main-module>\n…\ntheorem <id> … := MathFin.<name> …`, `metadata.formalization_status = "full"`, `formalization_scope` naming the module + "Re-export from MathFin. Axioms-clean."
- [ ] **Step 5: Commit**

```bash
git add targets/queue/
git commit -m "feat(queue): seed first issue-derived target batch (#109/#108/#88/#85/#53/#66/#67)"
```

### Task 5: `open-pr.sh` — assemble, validate+regen, open the PR

**Files:**
- Create: `scripts/open-pr.sh`

This runs in the foundry CI runner (16 GB, `mathfin-verify` image, main checked out with `MAIN_PR_TOKEN`). It never runs on the local box.

- [ ] **Step 1:** Write `scripts/open-pr.sh` taking `--id <target> --tag <run-tag>`:
  1. read the winning candidate `runs/$TAG-$ID.lean` + the target's placement metadata from `targets/queue/manifest.json`;
  2. in the main checkout: `git checkout -b autoform/$ID-$(date -u +%Y%m%d)`;
  3. `python3 -c "from assemble import apply_contribution; …"` to place the module + benchmark entry;
  4. **validate + regen** (green-or-abort): `lake build MathFin` → `python3 -m tools.verify.axiom_audit_gen --write` → `python3 -m tools.formalization_yaml --write` → `python3 -m tools.verify.ledger status`; if any fails, `gh issue create --label autoform-blocked` on the FOUNDRY repo (candidate needs manual placement) and `exit 0` WITHOUT opening a PR;
  5. **promotion honesty**: run the `#print axioms MathFin.<name>` + a definitional-`rfl` check (reject if the "full" proof is `rfl`-trivial — the test_values rfl-tripwire case);
  6. specific `git add` of exactly the written module + benchmark + regenerated `MathFin/AxiomAuditGen.lean` + `formalization.yaml` + `verification_ledger.json`; commit with a message that does **not** carry Claude attribution;
  7. `git push` the branch; `gh pr create --repo raphaelrrcoelho/formal-mathfin --label autoform --title "autoform: <issue title> (closes #<n>)" --body "$(pr_body)"`.
  8. `pr_body`: R's outbound style — what was proved, provenance (Leanstral + tokens + run tag), "closes #<n>", and an 8-lens review checklist. No em-dashes, lowercase, "i".
- [ ] **Step 2:** `chmod +x scripts/open-pr.sh`.
- [ ] **Step 3: Commit**

```bash
git add scripts/open-pr.sh
git commit -m "feat(pipeline): open-pr.sh — assemble + validate/regen + green-or-abort PR that closes the issue"
```

### Task 6: Wire the tick + workflow + credential

**Files:**
- Modify: `scripts/pipeline-tick.sh:67-83` (on `pass` → call `open-pr.sh` instead of only notifying)
- Modify: `.github/workflows/pipeline.yml` (checkout main with `MAIN_PR_TOKEN`; run the assemble+PR step in-container after a pass)
- Modify: `pipeline.toml` (add `main_repo_slug = "raphaelrrcoelho/formal-mathfin"`)

- [ ] **Step 1:** In `pipeline-tick.sh`, replace the notify-only block: on `OUTCOME = pass`, invoke `scripts/open-pr.sh --id "$ID" --tag "$TAG"` (guarded by `[ -n "${MAIN_PR_TOKEN:-}" ]`; if unset, fall back to the existing candidate-notify so local runs never try to PR).
- [ ] **Step 2:** In `pipeline.yml`: add a checkout of `raphaelrrcoelho/formal-mathfin` (path `main`, `token: ${{ secrets.MAIN_PR_TOKEN }}`, full history for `lake`), pass `MAIN_PR_TOKEN` + `MAIN_REPO=main` into the tick step, and ensure `gh` is authed with that token for the PR step.
- [ ] **Step 3:** Document the credential in `pipeline.yml` header + `docs/PROVER_SETUP.md`: R creates a fine-grained PAT on `formal-mathfin` (`contents: write`, `pull requests: write`); it is stored as the foundry secret `MAIN_PR_TOKEN`. (Setting the secret — `printf '%s' "$PAT" | gh secret set MAIN_PR_TOKEN --repo raphaelrrcoelho/mathfin-foundry` — is R's action / done via stdin when R hands over the PAT; never in the plan/logs.)
- [ ] **Step 4: Commit**

```bash
git add scripts/pipeline-tick.sh .github/workflows/pipeline.yml pipeline.toml docs/PROVER_SETUP.md
git commit -m "feat(pipeline): auto-open the PR on a pass (MAIN_PR_TOKEN-gated); local runs still notify-only"
```

---

## Phase 2 — Yield: pass@k fan-out + bounded repair

### Task 7: pass@k candidate fan-out in `probe.py`

**Files:**
- Modify: `probe/probe.py:101-165` (`run_target`)
- Modify: `probe/probe_lib.py` (add `best_failure`)
- Test: `probe/test_probe.py` (extend) + `probe/test_probe_lib.py`

**Interfaces:**
- Produces: `run_target(..., fanout: int = 1, repair_rounds: int = 2)` — each round samples `fanout` candidates (via `fanout` `chat_fn` calls), checks each, passes on the first axioms-clean success, else keeps the `best_failure` (fewest compiler errors) for the next repair round; total repair rounds capped at `repair_rounds`. `best_failure(results) -> int` returns the index of the candidate with the fewest errors.

- [ ] **Step 1: Write the failing test** — `test_probe_lib.py`:

```python
from probe_lib import best_failure

def test_best_failure_picks_fewest_errors():
    results = [{"errors": ["a", "b"]}, {"errors": ["c"]}, {"errors": ["d", "e", "f"]}]
    assert best_failure(results) == 1
```

and extend `test_probe.py` with a fan-out case (k=2, first candidate fails, second passes in the SAME round, so `rounds == 1`):

```python
def test_fanout_passes_when_one_of_k_succeeds_same_round():
    chat = make_chat([
        ("```lean\nimport Mathlib\ntheorem foo : 2+2=4 := by omega\n```", 400),  # cand 1 (fails)
        ("```lean\nimport Mathlib\ntheorem foo : 2+2=4 := by norm_num\n```", 400),# cand 2 (passes)
    ])
    check = make_check([
        {"success": False, "errors": ["e"], "warnings": [], "sorry_count": 0},   # cand 1
        {"success": True, "errors": [], "warnings": [], "sorry_count": 0},        # cand 2
        {"success": True, "errors": [], "warnings": [], "sorry_count": 0},        # axiom guard
    ])
    out = run_target(TARGET, budget=50000, max_rounds=3, fanout=2, repair_rounds=2,
                     chat_fn=chat, check_fn=check, log_fn=lambda r: None)
    assert out["outcome"] == "pass"
    assert out["rounds"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd probe && python3 -m pytest test_probe.py test_probe_lib.py -q`
Expected: FAIL (`best_failure` undefined; `run_target` has no `fanout` kwarg).

- [ ] **Step 3: Implement** — `best_failure` in `probe_lib.py`:

```python
def best_failure(results: list[dict]) -> int:
    """Index of the failing candidate with the fewest compiler errors (the most
    promising to repair). Ties resolve to the earliest."""
    return min(range(len(results)), key=lambda i: len(results[i].get("errors", [])))
```

Refactor `run_target` so each round: builds the current windowed messages once, calls `chat_fn` `fanout` times (collecting `(candidate, content, tokens)`), runs the slop gate + `check_fn` on each, passes on the first axioms-clean success; if none passes, selects `best_failure`, appends its assistant+repair messages, and counts the round toward `repair_rounds`. Keep the token ledger charging every sampled candidate. (Preserve the existing single-sample behavior at `fanout=1`, `repair_rounds=max_rounds` so current tests stay green.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd probe && python3 -m pytest test_probe.py test_probe_lib.py -q`
Expected: PASS (all, including the pre-existing single-sample tests).

- [ ] **Step 5: Commit**

```bash
git add probe/probe.py probe/probe_lib.py probe/test_probe.py probe/test_probe_lib.py
git commit -m "feat(probe): pass@k fan-out + best-failure repair selection (Kimina knee, Goedel repair)"
```

### Task 8: Rebudget knobs (fewer, bigger attempts)

**Files:**
- Modify: `probe/pipeline_lib.py:34-42` (`PipelineConfig`)
- Modify: `probe/pipeline.py:60-63` (emit `fanout`/`repair_rounds` in the plan)
- Modify: `probe/probe.py` CLI (`--fanout`, `--repair-rounds`, wire `max_tokens`)
- Modify: `pipeline.toml`
- Test: `probe/test_pipeline_lib.py` (extend)

**Interfaces:**
- Produces: `PipelineConfig` gains `fanout: int = 8`, `repair_rounds: int = 2`, `tokens_per_attempt: int = 60000` (Leanstral's lever is tokens-per-attempt; the 500k cap = ~8 attempts × ~60k). `plan` emits them so the tick passes them to `probe.py`.

- [ ] **Step 1: Write the failing test** (extend `test_pipeline_lib.py`):

```python
def test_config_has_fanout_and_repair_defaults():
    cfg = PipelineConfig()
    assert cfg.fanout == 8 and cfg.repair_rounds == 2 and cfg.tokens_per_attempt == 60000
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd probe && python3 -m pytest test_pipeline_lib.py -q`
Expected: FAIL (`AttributeError: fanout`).

- [ ] **Step 3: Implement** — add the three fields to `PipelineConfig`; in `pipeline.py:cmd_plan` add `"fanout": cfg.fanout, "repair_rounds": cfg.repair_rounds` to the `run` decision; add `--fanout`/`--repair-rounds` args to `probe.py` `prove` (thread into `run_target`) and set `max_tokens=cfg.tokens_per_attempt` on the chat call; add the three keys to `pipeline.toml` under `[pipeline]`; have `pipeline-tick.sh` read `fanout`/`repair_rounds` from the plan JSON and pass them to `probe.py`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd probe && python3 -m pytest test_pipeline_lib.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add probe/pipeline_lib.py probe/pipeline.py probe/probe.py pipeline.toml scripts/pipeline-tick.sh probe/test_pipeline_lib.py
git commit -m "feat(pipeline): fanout/repair/tokens-per-attempt knobs (spend the cap as fewer, bigger attempts)"
```

---

## Phase 3 — Yield: richer context packs

### Task 9: dependency-closure signatures in `scout_index.py`

**Files:**
- Modify: `probe/scout_index.py:111-116` (add `dependency_closure`)
- Test: `probe/test_scout_index.py` (extend)

**Interfaces:**
- Produces: `ScoutIndex.dependency_closure(names: list[str], depth: int = 2) -> list[str]` — BFS over `const_dep` from `names` up to `depth`, returning the reachable constant names (excluding the seeds), deterministic order.

- [ ] **Step 1: Write the failing test** (extend `test_scout_index.py`):

```python
def test_dependency_closure_bfs(tmp_path):
    import json, os
    os.makedirs(tmp_path / "index")
    with open(tmp_path / "index" / "types.jsonl", "w") as f:  # makes .available True
        f.write("")
    with open(tmp_path / "index" / "const_dep.jsonl", "w") as f:
        for rec in [{"name": "A", "deps": ["B", "C"]},
                    {"name": "B", "deps": ["D"]}, {"name": "C", "deps": []}]:
            f.write(json.dumps(rec) + "\n")
    from scout_index import ScoutIndex
    idx = ScoutIndex(str(tmp_path / "index"))
    assert set(idx.dependency_closure(["A"], depth=1)) == {"B", "C"}
    assert set(idx.dependency_closure(["A"], depth=2)) == {"B", "C", "D"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd probe && python3 -m pytest test_scout_index.py -q`
Expected: FAIL (`AttributeError: dependency_closure`).

- [ ] **Step 3: Implement** in `scout_index.py`:

```python
    def dependency_closure(self, names: list[str], depth: int = 2) -> list[str]:
        """BFS over const_dep from `names` up to `depth` hops; reachable
        constants excluding the seeds, in discovery order."""
        dep_of = {r.get("name"): list(r.get("deps") or []) for r in self._cd()}
        seen, frontier, order = set(names), list(names), []
        for _ in range(depth):
            nxt = []
            for n in frontier:
                for d in dep_of.get(n, []):
                    if d not in seen:
                        seen.add(d); order.append(d); nxt.append(d)
            frontier = nxt
            if not frontier:
                break
        return order
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd probe && python3 -m pytest test_scout_index.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add probe/scout_index.py probe/test_scout_index.py
git commit -m "feat(scout): dependency_closure BFS over const_dep (cross-file premise reach)"
```

### Task 10: inject closure + preceding-file source in `house_context.py`

**Files:**
- Modify: `probe/house_context.py:161-188` (`_index_pack`)
- Test: `probe/test_house_context.py` (extend)

**Interfaces:**
- Consumes: `ScoutIndex.dependency_closure` (Task 9), `ScoutIndex.signatures`.
- Produces: `_index_pack` additionally emits a "DEPENDENCY-CLOSURE SIGNATURES" block (exact signatures of constants the pointer modules' decls transitively use) so the agent sees the cross-file premises it must consume, not just the pointer modules' own decls. `extract_signatures` gains `closure_depth: int = 2`.

- [ ] **Step 1: Write the failing test** (extend `test_house_context.py`) — index with a pointer module decl whose `const_dep` names a constant defined in another module; assert that other constant's signature appears in the pack under the closure block.

```python
def test_context_pack_includes_dependency_closure(tmp_path):
    import json, os
    idx = tmp_path / "index"; os.makedirs(idx)
    with open(idx / "types.jsonl", "w") as f:
        f.write(json.dumps({"name": "MathFin.foo", "module": "MathFin.A",
                            "type": "ℝ → ℝ", "docString": None}) + "\n")
        f.write(json.dumps({"name": "MathFin.helper", "module": "MathFin.B",
                            "type": "ℝ → Prop", "docString": "the helper"}) + "\n")
    with open(idx / "const_dep.jsonl", "w") as f:
        f.write(json.dumps({"name": "MathFin.foo", "deps": ["MathFin.helper"]}) + "\n")
    from house_context import extract_signatures
    pack = extract_signatures(str(tmp_path), ["MathFin/A.lean"], index_dir=str(idx))
    assert "foo : ℝ → ℝ" in pack                 # pointer-module decl
    assert "helper : ℝ → Prop" in pack            # its cross-module dependency
    assert "DEPENDENCY-CLOSURE" in pack
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd probe && python3 -m pytest test_house_context.py -q`
Expected: FAIL (closure block absent).

- [ ] **Step 3: Implement** — in `_index_pack`, after the pointer-module blocks, collect the pointer modules' decl names, `dependency_closure(names, closure_depth)`, look up each closure constant's `(module, type, doc)` from the types index, and emit a `── DEPENDENCY-CLOSURE SIGNATURES (cross-file premises to consume) ──` block. Thread `closure_depth` through `extract_signatures`. Keep the regex fallback unchanged.

- [ ] **Step 4: Run to verify it passes**

Run: `cd probe && python3 -m pytest test_house_context.py -q`
Expected: PASS (all, including the existing index/regex tests).

- [ ] **Step 5: Commit**

```bash
git add probe/house_context.py probe/test_house_context.py
git commit -m "feat(context): inject dependency-closure signatures into the pack (miniCTX cross-file premises)"
```

---

## Phase 4 — Follow-on plan (out of scope here)

The full-autonomy issue-consumption layer is a **separate plan** (its own spec→plan→build), because the functional pipeline above already produces green PRs on its own and this layer is an independent subsystem:

- **Autoformalize sub-probe** — read an issue body (Task/Pointers) → generate the stub via Leanstral → so the tick attempts *unseeded* issues, closing the loop from issue to PR with no hand-authoring.
- **Faithfulness gates** (`probe/faithfulness.py`) — k statement variants, hypothesis-rejection (prove `False` from the hypotheses → discard vacuous), roundtrip formalize→informalize→re-formalize equivalence; the honesty gate for auto-generated statements.
- **Subgoal decomposition** (`probe/decompose.py`) + **variant warm-up** for stuck (hard) issues.
- **Self-host search env** in `leanstral-vibe.sh` (`LOOGLE_URL` / `LEAN_STATE_SEARCH_URL`; prefer local ripgrep + scout packs given the 10 GB box).

---

## Self-Review

**Spec coverage:** Phase 0 = item 9 endpoint ✓. Phase 1 = the functional pipeline (seed from the *issues* — corrected from reduced_core after recon showed 0 tractable gaps — + assemble + open-pr + wiring + credential) ✓. Phase 2 = items 1+2 pass@k/repair/rebudget ✓. Phase 3 = item 4 richer context ✓. Items 3/6/7/8 + MathFin-Bench (item 5, deferred by R) → Phase-4 follow-on plan / dropped, as the spec's "staged"/non-goals sections state ✓. The scout-not-author reversal (auto-PR) is realized in Task 5/6 with the human-merge + CI-gate + revocable-token envelope from the spec ✓.

**Placeholder scan:** the one `True := by sorry` in Task 4's worked example is explicitly flagged as a scaffold with the execution note naming the real statement + the lemmas to use; every code step otherwise carries complete content.

**Type consistency:** `run_target` gains `fanout`/`repair_rounds` (Task 7) consumed by `pipeline.py`/`probe.py` CLI (Task 8) sourced from `PipelineConfig.fanout/repair_rounds` (Task 8); `parse_meta` keys (Task 2) are exactly the target keys `assemble.apply_contribution` reads (Task 3); `dependency_closure` (Task 9) is consumed by `_index_pack` (Task 10). Consistent.

**Deviation from spec (surfaced):** the spec said "seed from tractable reduced_core→full gaps"; live recon (292 full / 18 wrapper / 14 reduced_core, all research-hard; 0 placeholder) showed no such pool, and R's own direction ("the issues guide the autoformalization") points at the 91-issue backlog. Seed source corrected to the issues accordingly — a faithfulness *upgrade* (the statement is the issue's, R-curated), not a scope change.
