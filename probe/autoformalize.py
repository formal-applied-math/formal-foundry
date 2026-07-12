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

import json
import re

from probe import run_target
from probe_lib import extract_lean_code

# theorem/lemma decl, line-anchored so prose "theorem ..." in a docstring never
# matches. Captures the declaration name.
_DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:theorem|lemma)\s+([A-Za-z0-9_'.]+)",
    re.MULTILINE,
)
_OPEN, _CLOSE = "([{", ")]}"


def _locate(text: str) -> tuple[str, int, int, int]:
    """Locate the theorem/lemma header: return `(name, bstart, sep, end)` — the
    decl name, the index where binders start (just after the name), the index of
    the type-separator `:`, and the index of the proof `:=`. The separator is the
    first `:` at bracket-depth 0 that is not part of `:=` (so a `∀ x : ℝ, …` colon
    in the conclusion, which comes later, and a `(x : T)` binder colon at depth > 0
    are both skipped). Raises on a malformed header."""
    m = _DECL_RE.search(text)
    if not m:
        raise ValueError("no theorem/lemma declaration found")
    name, n = m.group(1), len(text)

    depth, sep = 0, -1
    j = m.end()
    while j < n:
        c = text[j]
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == ":" and depth == 0 and not (j + 1 < n and text[j + 1] == "="):
            sep = j
            break
        j += 1
    if sep == -1:
        raise ValueError("no type separator ':' found")

    depth, end = 0, n
    j = sep + 1
    while j < n:
        c = text[j]
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == ":" and depth == 0 and j + 1 < n and text[j + 1] == "=":
            end = j
            break
        j += 1
    return name, m.end(), sep, end


def split_statement(stub: str) -> tuple[str, str, str]:
    """Split a Lean theorem stub into `(name, binders, concl)`. Robust to a full
    module scaffold around the theorem (finds the line-anchored decl)."""
    name, bstart, sep, end = _locate(stub)
    return name, stub[bstart:sep], stub[sep + 1:end]


def vacuity_goal(lean_text: str) -> str:
    """The hypothesis-rejection probe: the stub with its conclusion replaced by
    `False`, keeping imports + binders. A clean proof means the hypotheses are
    contradictory (the theorem is vacuously true) — retire the target."""
    _n, _b, sep, end = _locate(lean_text)
    return lean_text[:sep] + ": False " + lean_text[end:]


def disproof_goal(lean_text: str) -> str:
    """The disproof probe: the stub with its conclusion `C` replaced by `¬ (C)`,
    keeping imports + binders. A clean proof means the statement is false as
    written — retire the target."""
    _n, _b, sep, end = _locate(lean_text)
    concl = lean_text[sep + 1:end].strip()
    return lean_text[:sep] + ": ¬ (" + concl + ") " + lean_text[end:]


# --- magistral reply parsers -------------------------------------------------

_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def _extract_json(text: str) -> dict | None:
    """First a ```json fenced object, else the first `{…}` span; None if neither
    parses."""
    candidates = []
    m = _JSON_FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1))
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i:
        candidates.append(text[i:j + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            continue
    return None


def parse_draft(reply: str) -> tuple[str, dict] | None:
    """Parse a draft reply into `(stub, meta)` — the ```lean theorem block + a
    ```json `{module_name, benchmark_id, docstring}` block. None if the lean block
    is missing or the required naming metadata is absent."""
    stub = extract_lean_code(reply)
    if stub is None:
        return None
    meta = _extract_json(reply) or {}
    if not meta.get("module_name") or not meta.get("benchmark_id"):
        return None
    return stub, meta


def parse_verdict(reply: str) -> dict:
    """Parse a judge/roundtrip reply's JSON verdict. Fails CLOSED: an unparseable
    reply (or one lacking `faithful`) is treated as NOT faithful, so an unverified
    statement is never shipped."""
    v = _extract_json(reply)
    if not isinstance(v, dict) or "faithful" not in v:
        return {"faithful": False, "verdict": "unparseable judge reply", "issues": []}
    return v


# --- chat-mediated runners (magistral: draft, judge, roundtrip) ---------------

DRAFT_SYSTEM = (
    "You are an autoformalization assistant for MathFin, a Lean 4 library built on "
    "Mathlib. Given a GitHub issue describing a mathematical-finance result (its Task "
    "and Pointers), produce ONE Lean 4 theorem that faithfully formalizes it, ending "
    "in `:= by sorry` (state only — no proof). Requirements:\n"
    "- Output exactly one ```lean block: a single "
    "`theorem NAME <binders> : <conclusion> := by sorry`.\n"
    "- Then a ```json block: "
    '{"module_name": "<CamelCase>", "benchmark_id": "mf-<area>-<slug>", "docstring": "<one line>"}.\n'
    "- Use Mathlib conventions (ℝ, Real.exp, …). CONSUME the existing declarations "
    "shown below rather than reproving them.\n"
    "- State EXACTLY what the issue asks: no vacuity, no weaker restatement. Prefer a "
    "conjunction when the issue lists a small cluster of facts.\n"
    "- Take givens as hypotheses (positive reals, nonneg loadings, …)."
)

JUDGE_SYSTEM = (
    "You are a faithfulness judge for autoformalized Lean statements. Given an issue's "
    "prose (what SHOULD be formalized) and a candidate Lean theorem, decide whether the "
    "Lean statement FAITHFULLY formalizes the issue: every requested fact, correct "
    "hypotheses, no vacuity, no weaker or stronger restatement. Respond with ONLY a JSON "
    'object: {"faithful": true|false, "verdict": "<one line>", "issues": ["<gap>", ...]}.'
)

ROUNDTRIP_SYSTEM = (
    "You are checking an autoformalized Lean statement by round-trip. In your reasoning: "
    "(1) describe the given Lean theorem in plain mathematical prose; (2) from ONLY that "
    "prose, re-derive what the Lean statement should be; (3) judge whether your "
    "re-derivation matches the original (same hypotheses and conclusion, no drift), and "
    "whether it matches the issue's intent. Respond with ONLY a JSON object: "
    '{"faithful": true|false, "verdict": "<one line>", "issues": ["<drift>", ...]}.'
)


def _issue_prose(issue: dict) -> str:
    return f"{issue.get('title', '')}\n{issue.get('body', '')}"


def draft_messages(issue: dict, context_pack: str, pins: str) -> list[dict]:
    user = f"ISSUE #{issue.get('number')}: {_issue_prose(issue)}\n"
    if context_pack:
        user += "\n" + context_pack
    if pins:
        user += "\n" + pins
    return [{"role": "system", "content": DRAFT_SYSTEM},
            {"role": "user", "content": user}]


def judge_messages(issue: dict, stub: str) -> list[dict]:
    return [{"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user",
             "content": f"ISSUE:\n{_issue_prose(issue)}\n\nCANDIDATE:\n```lean\n{stub}\n```"}]


def roundtrip_messages(issue: dict, stub: str) -> list[dict]:
    return [{"role": "system", "content": ROUNDTRIP_SYSTEM},
            {"role": "user",
             "content": f"ISSUE:\n{_issue_prose(issue)}\n\nLEAN:\n```lean\n{stub}\n```"}]


def draft_stub(issue: dict, context_pack: str, pins: str, *, chat_fn) -> dict:
    """Draft a stub from the issue. Returns `{stub, meta, tokens}` (stub/meta None
    if the reply had no lean block or lacked naming metadata)."""
    content, tokens = chat_fn(draft_messages(issue, context_pack, pins))
    parsed = parse_draft(content)
    if parsed is None:
        return {"stub": None, "meta": None, "tokens": tokens}
    stub, meta = parsed
    return {"stub": stub, "meta": meta, "tokens": tokens}


def judge_faithfulness(issue: dict, stub: str, *, chat_fn) -> dict:
    """Semantic judge: does the stub say what the issue asks? Returns the verdict
    dict plus `tokens`."""
    content, tokens = chat_fn(judge_messages(issue, stub))
    v = parse_verdict(content)
    v["tokens"] = tokens
    return v


def roundtrip_check(issue: dict, stub: str, *, chat_fn) -> dict:
    """Round-trip check: informalize → re-formalize → agree? Returns the verdict
    dict plus `tokens` (`faithful=False` means the round-trip drifted)."""
    content, tokens = chat_fn(roundtrip_messages(issue, stub))
    v = parse_verdict(content)
    v["tokens"] = tokens
    return v


# --- kernel-grade faithfulness gates (labs-leanstral via run_target) ----------

_GATE_MAX_ROUNDS = 2


def _try_prove(goal: str, sorry_name: str, *, chat_fn, check_fn, budget: int,
               fanout: int = 2, repair_rounds: int = 1, system_prompt=None) -> tuple[bool, int]:
    """Short pass@k attempt to prove `goal`. Returns `(proved, tokens)` — `proved`
    is True only on an axioms-clean success (run_target's `pass`)."""
    target = {"id": "gate", "stream": "gate", "statement": goal, "sorry_name": sorry_name}
    res = run_target(target, budget=budget, max_rounds=_GATE_MAX_ROUNDS, chat_fn=chat_fn,
                     check_fn=check_fn, log_fn=lambda r: None, system_prompt=system_prompt,
                     fanout=fanout, repair_rounds=repair_rounds)
    return res["outcome"] == "pass", res["tokens"]


def hypothesis_rejection(lean_text: str, sorry_name: str, *, chat_fn, check_fn,
                         budget: int, system_prompt=None) -> dict:
    """Try to prove `⊢ False` from the stub's hypotheses. A clean proof ⇒ the
    hypotheses are contradictory ⇒ the theorem is vacuously true. Returns
    `{vacuous, tokens}`."""
    proved, tokens = _try_prove(vacuity_goal(lean_text), sorry_name, chat_fn=chat_fn,
                                check_fn=check_fn, budget=budget, system_prompt=system_prompt)
    return {"vacuous": proved, "tokens": tokens}


def disproof(lean_text: str, sorry_name: str, *, chat_fn, check_fn,
             budget: int, system_prompt=None) -> dict:
    """Try to prove `⊢ ¬ Concl` under the stub's hypotheses. A clean proof ⇒ the
    statement is false as written. Returns `{false, tokens}`."""
    proved, tokens = _try_prove(disproof_goal(lean_text), sorry_name, chat_fn=chat_fn,
                                check_fn=check_fn, budget=budget, system_prompt=system_prompt)
    return {"false": proved, "tokens": tokens}


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
