"""Issue -> stub autoformalizer sub-probe (the self-feeding refill phase).

Turns the next `status:ready`+`type:proof` GitHub issue into a *validated* queue
target (stub `.lean` + `.entry.json` + manifest row) so the existing prover always
has something to prove. Two engines: a Mistral general reasoner (magistral) drafts
the statement + judges faithfulness + roundtrips; the Leanstral leaf-prover runs
the kernel gates (hypothesis-rejection, disproof) and the proof itself.

Design of record: docs/superpowers/specs/2026-07-12-issue-to-stub-autoformalizer-design.md.
Pure logic here is unit-tested with injected chat_fn/check_fn (no Lean/API/network).
Stdlib only.
"""

from __future__ import annotations

import re

# theorem/lemma decl, line-anchored so prose "theorem ..." in a docstring never
# matches. Captures the declaration name.
_DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:theorem|lemma)\s+([A-Za-z0-9_'.]+)",
    re.MULTILINE,
)
_OPEN, _CLOSE = "([{", ")]}"


def split_statement(stub: str) -> tuple[str, str, str]:
    """Split a Lean theorem stub into `(name, binders, concl)`.

    Robust to a full module scaffold around the theorem (finds the line-anchored
    decl). `binders` is everything between the name and the type separator; `concl`
    is the type, up to the proof `:=`. The separator is the first `:` at
    bracket-depth 0 that is not part of `:=` — so a `∀ x : ℝ, …` colon inside the
    conclusion (which comes after it) stays in `concl`, and a `(x : T)` binder
    colon (depth > 0) is skipped.
    """
    m = _DECL_RE.search(stub)
    if not m:
        raise ValueError("no theorem/lemma declaration found")
    name = m.group(1)
    n = len(stub)

    depth, sep = 0, -1
    j = m.end()
    while j < n:
        c = stub[j]
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == ":" and depth == 0 and not (j + 1 < n and stub[j + 1] == "="):
            sep = j
            break
        j += 1
    if sep == -1:
        raise ValueError("no type separator ':' found")
    binders = stub[m.end():sep]

    depth, end = 0, n
    j = sep + 1
    while j < n:
        c = stub[j]
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == ":" and depth == 0 and j + 1 < n and stub[j + 1] == "=":
            end = j
            break
        j += 1
    concl = stub[sep + 1:end]
    return name, binders, concl


def explicit_arg_names(binders: str) -> list[str]:
    """Names of the EXPLICIT `(…)` binders, in order — the arguments a re-export
    passes to the module lemma. `{…}` implicit and `[…]` instance binders are
    inferred, so they are omitted."""
    names: list[str] = []
    depth, start = 0, -1
    for i, c in enumerate(binders):
        if c == "(":
            if depth == 0:
                start = i + 1
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0 and start != -1:
                group = binders[start:i]
                colon = group.find(":")
                head = group[:colon] if colon != -1 else group
                names.extend(head.split())
                start = -1
    return names


# --- placement + mechanical emit ---------------------------------------------

# issue area label -> MathFin subdirectory. Areas without a directory yet (fx)
# map to a new one the umbrella import + lake build absorb; anything unmapped
# falls back to a CamelCase of the area.
_AREA_TO_SECTION = {
    "fixed-income": "FixedIncome", "actuarial": "Actuarial", "fx": "FX",
    "black-scholes": "BlackScholes", "futures": "Futures", "binomial": "Binomial",
    "portfolio": "Portfolio", "performance": "Performance", "risk": "RiskMeasures",
    "defi": "DeFi", "credit": "FixedIncome", "execution": "Portfolio",
}

_LICENSE = (
    "/-\n"
    "Copyright (c) 2026 Raphael Coelho. All rights reserved.\n"
    "Released under Apache 2.0 license as described in the file LICENSE.\n"
    "Authors: Raphael Coelho\n"
    "-/"
)
_BENCHMARK = "benchmarks/mathematical_finance.json"
_DOMAIN = "mathematical_finance"


def section_for_area(area: str) -> str:
    """Map an issue's `area:` label to a `MathFin/<Section>/` subdirectory."""
    if area in _AREA_TO_SECTION:
        return _AREA_TO_SECTION[area]
    return "".join(p.capitalize() for p in re.split(r"[-_ ]+", area or "") if p)


def emit_target_files(issue: dict, stub: str, meta: dict) -> tuple[str, dict, dict]:
    """Assemble a queue target from a drafted stub — MECHANICAL, no model call.

    Returns `(stub_lean_text, entry_json, placement)`:
    - `stub_lean_text`: the full `cal-bk-<N>.lean` module (license, module header,
      `public import Mathlib`, placement comment headers `build_manifest` reads, the
      `meta.docstring` as a `/-! -/` doc, `@[expose] public section`, `namespace
      MathFin`, the drafted theorem with its `sorry`, `end MathFin`);
    - `entry_json`: the re-export benchmark entry (`import` the module + apply the
      lemma, carrying `metadata.provenance`);
    - `placement`: `{main_module, benchmark, benchmark_id, source_issue}`.
    """
    n = issue["number"]
    section = section_for_area(issue.get("area") or "")
    module_name = meta["module_name"]
    main_module = f"MathFin/{section}/{module_name}.lean"
    benchmark_id = meta["benchmark_id"]
    docstring = (meta.get("docstring") or "").strip()
    pointers = issue.get("pointers", [])
    name, binders, concl = split_statement(stub)

    headers = "\n".join([
        f"-- pointers: {', '.join(pointers)}",
        f"-- main-module: {main_module}",
        f"-- benchmark: {_BENCHMARK}",
        f"-- benchmark-id: {benchmark_id}",
        f"-- source-issue: {n}",
    ])
    # coherence-first: import the pointer modules so the drafted statement can
    # consume existing MathFin defs (a path 'MathFin/FixedIncome/ZCB.lean' becomes
    # 'public import MathFin.FixedIncome.ZCB'); an unused import is harmless.
    imports = "\n".join(
        ["public import Mathlib"]
        + [f"public import {p[:-5].replace('/', '.')}"
           for p in pointers if p.endswith(".lean")]
    )
    lean_text = (
        f"{_LICENSE}\n"
        "module\n\n"
        f"{imports}\n\n"
        f"{headers}\n\n"
        f"/-!\n{docstring}\n-/\n\n"
        "@[expose] public section\n\n"
        "namespace MathFin\n\n"
        f"{stub.strip()}\n\n"
        "end MathFin\n"
    )

    mf_name = benchmark_id.replace("-", "_")
    app = f"MathFin.{name} {' '.join(explicit_arg_names(binders))}".rstrip()
    reexport = (
        f"import MathFin.{section}.{module_name}\n\n"
        "open MathFin\n\n"
        f"/-- {docstring} -/\n"
        f"theorem {mf_name} {binders.strip()} :{concl.rstrip()} :=\n"
        f"  {app}\n"
    )
    entry = {
        "id": benchmark_id,
        "name": issue.get("title", benchmark_id),
        "description": docstring,
        "domain": _DOMAIN,
        "code": {"lean": reexport},
        "metadata": {
            "chapter": 0,
            "reference": issue.get("title", ""),
            "difficulty": issue.get("difficulty", "medium"),
            "formalization_status": "full",
            "formalization_scope": (
                f"Full formal proof in {main_module} (magistral-drafted statement, "
                "leanstral proof). Re-export from MathFin. Axioms-clean."
            ),
            "provenance": {
                "statement_source": "magistral-autoform",
                "statement_model": "magistral-medium",
                "source": "leanstral-autoform",
                "model": "labs-leanstral-1-5",
                "issue": n,
            },
        },
    }
    placement = {
        "main_module": main_module,
        "benchmark": _BENCHMARK,
        "benchmark_id": benchmark_id,
        "source_issue": n,
    }
    return lean_text, entry, placement
