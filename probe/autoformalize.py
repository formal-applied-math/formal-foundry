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

import argparse
import glob
import json
import os
import re
import subprocess
import sys

from house_context import build_system_prompt, extract_signatures, read_pins
from issues import select_issues
from pipeline_lib import AutoformalizeConfig
from probe import daemon_check, mistral_chat, run_target
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
    "- Use ASCII-parseable Lean operators: `^` for powers (write `σ^2`, NEVER the "
    "Unicode superscript `σ²`); `*` for products; `Real.exp`/`Real.log`/`Real.sqrt`.\n"
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


def draft_with_repair(issue: dict, context_pack: str, pins: str, *, chat_fn, check_fn,
                      emit_fn, rounds: int = 2) -> dict:
    """Draft a stub and repair it against the elaborator (compiler feedback, the
    lever that makes autoformalization work): draft → emit → elaborate; on a parse
    failure or an elaboration error, feed the reason back — with a `^`-not-`²`
    syntax hint — and re-draft, up to `rounds` times. Returns
    `{ok, stub, meta, lean_text, entry, tokens}`."""
    messages = draft_messages(issue, context_pack, pins)
    tokens = 0
    for _ in range(max(1, rounds)):
        content, tk = chat_fn(messages)
        tokens += tk
        parsed = parse_draft(content)
        if parsed is None:
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content":
                 "Output exactly one ```lean block with a single "
                 "`theorem NAME <binders> : <conclusion> := by sorry`, then one ```json "
                 '{"module_name", "benchmark_id", "docstring"} block.'}]
            continue
        stub, meta = parsed
        try:
            lean_text, entry, _placement = emit_fn(issue, stub, meta)
        except Exception as e:  # noqa: BLE001 — surface the assembly failure to the model
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content":
                 f"The theorem could not be assembled ({e}). Re-output a single "
                 "well-formed `theorem … := by sorry`."}]
            continue
        elab = check_fn(lean_text)
        if elab.get("success") and elab.get("sorry_count", 0) == 1:
            return {"ok": True, "stub": stub, "meta": meta,
                    "lean_text": lean_text, "entry": entry, "tokens": tokens}
        errs = "\n".join(str(e) for e in elab.get("errors", [])[:8])
        messages += [
            {"role": "assistant", "content": content},
            {"role": "user", "content":
             f"That statement does not elaborate in Lean:\n```\n{errs}\n```\n"
             "Fix ONLY the statement, keeping it faithful to the issue and still "
             "ending in `:= by sorry`. Use `^` for powers (write `x^2`, never the "
             "Unicode superscript `x²`); use `Real.exp`/`Real.log`/`Real.sqrt`. "
             "Re-output the ```lean and ```json blocks."}]
    return {"ok": False, "stub": None, "meta": None,
            "lean_text": None, "entry": None, "tokens": tokens}


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


# --- issue preparation --------------------------------------------------------

_POINTER_RE = re.compile(r"MathFin/[\w/]+\.lean")


def extract_pointers(body: str) -> list[str]:
    """Repo-relative `MathFin/…/X.lean` paths named in an issue body (its Pointers
    section), de-duplicated in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for p in _POINTER_RE.findall(body or ""):
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def prepare_issues(raw: list[dict], *, max_difficulty: str = "medium") -> list[dict]:
    """Filter+order the raw `gh issue list` output to the tractable
    `status:ready`+`type:proof` queue (via `issues.select_issues`) and enrich each
    with its `body` + extracted `pointers` for drafting."""
    by_num = {r.get("number"): r for r in raw}
    out = []
    for s in select_issues(raw, max_difficulty=max_difficulty):
        body = by_num.get(s["number"], {}).get("body") or ""
        out.append({**s, "body": body, "pointers": extract_pointers(body)})
    return out


# --- the refill orchestrator --------------------------------------------------

def _write_target(queue_dir: str, n: int, lean_text: str, entry: dict) -> list[str]:
    """Write the stub + its `.entry.json` sidecar into the queue dir. Returns the
    two paths written."""
    os.makedirs(queue_dir, exist_ok=True)
    stub_path = os.path.join(queue_dir, f"cal-bk-{n}.lean")
    entry_path = os.path.join(queue_dir, f"cal-bk-{n}.entry.json")
    with open(stub_path, "w", encoding="utf-8") as f:
        f.write(lean_text)
    with open(entry_path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return [stub_path, entry_path]


def refill(issues: list[dict], *, reason_fn, prove_fn, check_fn, context_fn,
           queue_dir: str, budget: int, pins: str = "", max_issues: int = 1,
           max_attempt_issues: int = 3, gate_budget: int = 20_000, draft_rounds: int = 2,
           system_prompt=None, log=lambda m: None) -> dict:
    """Draft + gate + stage up to `max_issues` targets from `issues`.

    For each candidate (up to `max_attempt_issues`), the gate cascade runs
    cheapest-first: draft-with-repair (magistral `reason_fn`, elaboration-checked
    with compiler feedback) → hypothesis-rejection + disproof (leanstral `prove_fn`)
    → semantic judge + roundtrip (magistral). Any reject logs the reason and skips
    to the next issue (the issue stays `status:ready` — never auto-closed). A passing
    target's stub + `.entry.json` are written to `queue_dir`. Returns
    `{seeded, tokens}`."""
    seeded, spent = [], 0
    for issue in issues[:max_attempt_issues]:
        if len(seeded) >= max_issues or spent >= budget:
            break
        n = issue.get("number")
        # A transient error on ONE issue (e.g. an HTTP 429 that exhausts retries,
        # a daemon hiccup, a malformed draft) must not kill the tick — log it and
        # move to the next candidate.
        try:
            dr = draft_with_repair(issue, context_fn(issue), pins, chat_fn=reason_fn,
                                   check_fn=check_fn, emit_fn=emit_target_files, rounds=draft_rounds)
            spent += dr["tokens"]
            if not dr["ok"]:
                log(f"#{n}: no elaborating draft after {draft_rounds} rounds"); continue
            stub, lean_text, entry = dr["stub"], dr["lean_text"], dr["entry"]
            name = split_statement(stub)[0]

            vac = hypothesis_rejection(lean_text, name, chat_fn=prove_fn, check_fn=check_fn,
                                       budget=gate_budget, system_prompt=system_prompt)
            spent += vac["tokens"]
            if vac["vacuous"]:
                log(f"#{n}: retired — vacuous (hypotheses contradictory)"); continue

            dis = disproof(lean_text, name, chat_fn=prove_fn, check_fn=check_fn,
                           budget=gate_budget, system_prompt=system_prompt)
            spent += dis["tokens"]
            if dis["false"]:
                log(f"#{n}: retired — false as written"); continue

            j = judge_faithfulness(issue, stub, chat_fn=reason_fn)
            spent += j["tokens"]
            if not j.get("faithful"):
                log(f"#{n}: unfaithful — {j.get('verdict', '')}"); continue

            rt = roundtrip_check(issue, stub, chat_fn=reason_fn)
            spent += rt["tokens"]
            if not rt.get("faithful"):
                log(f"#{n}: roundtrip drift — {rt.get('verdict', '')}"); continue

            paths = _write_target(queue_dir, n, lean_text, entry)
            seeded.append({"id": f"cal-bk-{n}", "issue": n, "paths": paths})
            log(f"#{n}: staged cal-bk-{n}")
        except Exception as e:  # noqa: BLE001 — resilience: skip the issue, not the tick
            log(f"#{n}: error ({type(e).__name__}: {e}) — skipping")
            continue
    return {"seeded": seeded, "tokens": spent}


# --- CLI (the refill entrypoint pipeline-tick.sh calls) -----------------------

def _fetch_issues(slug: str) -> list[dict]:
    out = subprocess.run(
        ["gh", "issue", "list", "--repo", slug, "--state", "open", "--limit", "100",
         "--json", "number,title,labels,body"],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _already_seeded(queue_dir: str) -> set[int]:
    """Issue numbers already staged as `cal-*-<N>.lean` in the queue."""
    nums: set[int] = set()
    for p in glob.glob(os.path.join(queue_dir, "cal-*.lean")):
        m = re.search(r"cal-\w+-(\d+)\.lean$", os.path.basename(p))
        if m:
            nums.add(int(m.group(1)))
    return nums


def _foundry_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("refill", help="draft+gate+stage the next ready issue")
    p.add_argument("--main-repo", required=True)
    p.add_argument("--config", default=None,
                   help="pipeline.toml for [autoformalize] defaults (default: <foundry>/pipeline.toml)")
    p.add_argument("--slug", default="raphaelrrcoelho/formal-mathfin")
    p.add_argument("--queue-dir", default=None, help="default: <foundry>/targets/queue")
    p.add_argument("--only", type=int, default=None, help="attempt only this issue number")
    # the rest override the [autoformalize] config only when given (default: None)
    p.add_argument("--budget", type=int, default=None)
    p.add_argument("--max-issues", type=int, default=None)
    p.add_argument("--max-attempt-issues", type=int, default=None)
    p.add_argument("--gate-budget", type=int, default=None)
    p.add_argument("--draft-model", default=None)
    p.add_argument("--prover-model", default=None)
    p.add_argument("--draft-max-tokens", type=int, default=None)
    p.add_argument("--draft-rounds", type=int, default=None)
    args = ap.parse_args()

    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("MISTRAL_API_KEY not set", file=sys.stderr)
        return 2

    # pipeline.toml [autoformalize] is authoritative; CLI flags override per-field.
    cfg = AutoformalizeConfig.load(args.config or os.path.join(_foundry_root(), "pipeline.toml"))
    pick = lambda a, c: a if a is not None else c
    budget = pick(args.budget, cfg.budget)
    max_issues = pick(args.max_issues, cfg.max_issues)
    max_attempt = pick(args.max_attempt_issues, cfg.max_attempt_issues)
    gate_budget = pick(args.gate_budget, cfg.gate_budget)
    draft_model = pick(args.draft_model, cfg.draft_model)
    prover_model = pick(args.prover_model, cfg.prover_model)
    draft_max_tokens = pick(args.draft_max_tokens, cfg.draft_max_tokens)
    draft_rounds = pick(args.draft_rounds, cfg.draft_rounds)

    queue_dir = args.queue_dir or os.path.join(_foundry_root(), "targets", "queue")

    seeded_nums = _already_seeded(queue_dir)
    issues = [i for i in prepare_issues(_fetch_issues(args.slug))
              if i["number"] not in seeded_nums
              and (args.only is None or i["number"] == args.only)]
    if not issues:
        print(json.dumps({"seeded": [], "tokens": 0, "reason": "no unseeded ready issues"}))
        return 0

    pins_d = read_pins(args.main_repo)
    pins = (f"── PINS ──\nLean {pins_d['toolchain']}, Mathlib @ {pins_d['mathlib']}, "
            f"BrownianMotion @ {pins_d['brownianmotion']}. Target this API surface exactly.")
    prove_system = build_system_prompt(args.main_repo)   # the leaf-prover gate doctrine

    def reason_fn(msgs):
        return mistral_chat(msgs, api_key=api_key, model=draft_model,
                            max_tokens=draft_max_tokens)

    def prove_fn(msgs):
        return mistral_chat(msgs, api_key=api_key, model=prover_model,
                            reasoning_effort="high")

    def context_fn(issue):
        ptrs = issue.get("pointers", [])
        return extract_signatures(args.main_repo, ptrs) if ptrs else ""

    res = refill(issues, reason_fn=reason_fn, prove_fn=prove_fn, check_fn=daemon_check,
                 context_fn=context_fn, queue_dir=queue_dir, budget=budget, pins=pins,
                 max_issues=max_issues, max_attempt_issues=max_attempt, gate_budget=gate_budget,
                 draft_rounds=draft_rounds, system_prompt=prove_system,
                 log=lambda m: print(f"[refill] {m}", file=sys.stderr))
    print(json.dumps(res))
    return 0


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


if __name__ == "__main__":
    sys.exit(main())
