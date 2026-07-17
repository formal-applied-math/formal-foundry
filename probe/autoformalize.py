"""Issue -> stub autoformalizer sub-probe (the self-feeding refill phase).

Turns the next `status:ready`+`type:proof` GitHub issue into a *validated* queue
target (stub `.lean` + `.entry.json` + manifest row) so the existing prover always
has something to prove. Two engines: a Mistral general reasoner (magistral) drafts
the statement + judges faithfulness + roundtrips; the Leanstral leaf-prover runs
the kernel gates (hypothesis-rejection, disproof) and the proof itself.

Design of record: docs/superpowers/specs/2026-07-12-issue-to-stub-autoformalizer-design.md,
extended by 2026-07-17-semantic-repair-cascade-design.md (semantic gate rejections feed a
bounded re-draft loop instead of terminally skipping the issue; triviality gate; obstruction
telemetry). Pure logic here is unit-tested with injected chat_fn/check_fn (no Lean/API/
network). Stdlib only.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

import embed as _embed
from house_context import build_system_prompt, extract_signatures
from issues import select_issues
from pipeline_lib import AutoformalizeConfig
from probe import daemon_check, mistral_chat, run_target
from probe_lib import append_jsonl, extract_lean_code

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


def parse_verdict(reply: str) -> dict:
    """Parse a judge/roundtrip reply's JSON verdict. Fails CLOSED: an unparseable
    reply (or one lacking `faithful`) is treated as NOT faithful, so an unverified
    statement is never shipped."""
    v = _extract_json(reply)
    if not isinstance(v, dict) or "faithful" not in v:
        return {"faithful": False, "verdict": "unparseable judge reply", "issues": []}
    return v


# --- chat-mediated runners (magistral: judge) ---------------------------------

JUDGE_SYSTEM = (
    "You are a faithfulness judge for autoformalized Lean statements — a SAFETY NET "
    "that catches GROSS failures, not a maximal-formality checker (a human makes the "
    "final call at merge). Given an issue's prose and a candidate Lean theorem, mark it "
    "faithful UNLESS it has a gross failure IN WHAT IT STATES: a fact it states is wrong, "
    "vacuous, or materially weaker than asked, a hypothesis or an inequality direction "
    "is wrong, or it silently drops part of a fact it claims to state. A candidate MAY "
    "faithfully formalize a SUBSET of a multi-part issue: when the drafter has DECLARED "
    "the omitted facts (they are listed below as 'declared deferred'), covering fewer "
    "facts than the issue lists is NOT a gross failure — judge the theorem as stated "
    "against the subset it claims, and let the deferred facts become follow-up issues. "
    "Only an UNDECLARED missing fact — a silent gap, absent from the deferred list — "
    "counts against faithfulness. ACCEPT reasonable abstractions: a real parameter standing for "
    "E[X], Var[X], a price, or a discount factor is fine (no measure-theoretic "
    "construction required), and a named quantity's definition MAY be inlined into its "
    "stated property (e.g. `(1+θ)*μ ≥ μ` faithfully renders 'the premium π = (1+θ)·μ "
    "satisfies π ≥ μ'). Do NOT fault a statement for omitting a hypothesis that is PROVABLE "
    "from the definitions it consumes: a zero-coupon-bond price built from `Real.exp` is "
    "automatically positive, so an explicit `0 < P` positivity hypothesis is unnecessary — its "
    "absence is not a gap. (A genuinely-needed side condition, like a denominator `≠ 0` that is "
    "NOT provable from the consumed defs, is still required.) "
    "Respond with ONLY a JSON object: "
    '{"faithful": true|false, "verdict": "<one line>", "issues": ["<gross gap>", ...]}.'
)

def _issue_prose(issue: dict) -> str:
    return f"{issue.get('title', '')}\n{issue.get('body', '')}"


def judge_messages(issue: dict, stub: str, deferred: list[str] | None = None) -> list[dict]:
    declared = ""
    if deferred:
        bullets = "\n".join(f"- {d}" for d in deferred)
        declared = ("\n\nDECLARED DEFERRED (the drafter intentionally left these for "
                    "follow-up issues; do NOT fail the subset for omitting them):\n"
                    f"{bullets}")
    return [{"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user",
             "content": f"ISSUE:\n{_issue_prose(issue)}\n\nCANDIDATE:\n```lean\n{stub}\n```{declared}"}]


def judge_faithfulness(issue: dict, stub: str, *, chat_fn,
                       deferred: list[str] | None = None) -> dict:
    """Semantic judge: does the stub say what the issue asks? A declared-subset
    (`deferred` — the facts the drafter intentionally left for follow-up issues) is
    judged against the subset it claims, not dinged for the omission. Returns the
    verdict dict plus `tokens`."""
    content, tokens = chat_fn(judge_messages(issue, stub, deferred))
    v = parse_verdict(content)
    v["tokens"] = tokens
    return v


def _assistant(content: str) -> dict:
    """An assistant turn safe to re-send. Mistral 400s on an empty-content assistant message
    ("Assistant message must have either content or tool_calls, but not none") — which a
    free-tier empty reply produces once threaded into a repair round — so substitute a
    placeholder (caught #61 in the 2026-07-15 forced tick)."""
    return {"role": "assistant", "content": content or "(no output)"}


# --- two-stage draft: intent (magistral) + formalize (leanstral) --------------
#
# Draft failures are LEAN failures (unknown identifiers, `let`-scoping, coercions), not math
# failures — Leanstral's home turf, magistral's weak spot. So split the draft: magistral
# SPECIFIES the intended statement in precise prose (its strength, no Lean), Leanstral
# FORMALIZES it into elaborating Lean (its strength). See
# docs/superpowers/specs/2026-07-14-leanstral-drafter-two-stage-design.md.

INTENT_SYSTEM = (
    "You are a mathematical-finance specifier for MathFin (a Lean 4 library on Mathlib). "
    "Given a GitHub issue (Task + Pointers), produce a PRECISE natural-language statement of the "
    "ONE theorem to formalize — every hypothesis and the full conclusion, unambiguous enough that "
    "a Lean expert could formalize it without seeing the issue. Do NOT write Lean. Name the exact "
    "MathFin/Mathlib objects it should be built from (e.g. `MathFin.zcb`), drawn from the "
    "declarations shown. Respond with ONLY a JSON object: "
    '{"statement": "<precise prose>", "objects": ["MathFin.zcb", ...], "module_name": "<CamelCase>", '
    '"benchmark_id": "mf-<area>-<slug>", "docstring": "<one line>", "deferred": ["<fact left out>", ...]}. '
    "`deferred` is [] when the statement covers the whole issue; when you specify a faithful SUBSET, "
    "list the omitted facts (each a short phrase) so they become follow-up issues. Never weaken or "
    "silently drop a fact you state."
)

FORMALIZE_SYSTEM = (
    "You are an autoformalization model for MathFin (Lean 4 on Mathlib). Given a precise INTENDED "
    "STATEMENT in prose plus the available declarations, produce ONE Lean 4 theorem that formalizes "
    "it EXACTLY, ending in `:= by sorry` (state only — no proof). Requirements:\n"
    "- Output exactly one ```lean block: a single `theorem NAME <binders> : <conclusion> := by sorry`.\n"
    "- CONSUME the named MathFin/Mathlib declarations rather than reproving or inlining them (e.g. use "
    "`MathFin.zcb r t T`, do not re-derive the bond price).\n"
    "- ASCII-parseable operators: `^` for powers (write `σ^2`, never the Unicode superscript `σ²`); "
    "`*` for products; `Real.exp`/`Real.log`/`Real.sqrt`.\n"
    "- Render every hypothesis and the full conclusion of the intended statement — do not weaken, "
    "drop a hypothesis, or flip an inequality."
)

FIDELITY_SYSTEM = (
    "You are given an INTENDED STATEMENT in prose and a candidate Lean 4 theorem meant to formalize "
    "it. Decide whether the Lean faithfully renders the intent: same hypotheses, same conclusion, "
    "nothing weakened, dropped, or flipped. This is a SAFETY NET for gross formalization failures, "
    "not a maximal-formality check (a human makes the final call at merge); accept reasonable "
    "abstractions. A hypothesis the intent lists as an ASSUMPTION but that becomes PROVABLE once "
    "the Lean realizes the quantity with a concrete definition is faithfully OMITTED, not dropped: "
    "e.g. the intent assumes `0 < P` for a discount factor, but the Lean uses `MathFin.zcb` (a "
    "`Real.exp`, automatically positive), so leaving out `0 < P` is a correct refinement, not a "
    "weakening. (A side condition NOT provable from the concrete defs is still required.) "
    "Respond with ONLY a JSON object: "
    '{"faithful": true|false, "verdict": "<one line>", "issues": ["<gross divergence>", ...]}.'
)


# the defs-route addendum (F1): the issue's primitives don't exist yet, so the
# intent must also SPECIFY the 1-3 definitions the module introduces.
INTENT_DEFS_ADDENDUM = (
    "\n\nTHIS ISSUE NEEDS NEW DEFINITIONS: the library does not yet provide the primitives "
    "the statement should be expressed through. In the same JSON, also emit "
    '"definitions": [{"name": "<camelCase>", "signature": "<Lean type>", '
    '"meaning": "<one line>", "built_from": ["<existing Mathlib/MathFin constants>"]}, ...] '
    "— 1 to 3 definitions, each buildable from EXISTING constants (never a free-floating "
    "wrapper), and specify the statement so the theorem is EXPRESSED THROUGH these new "
    "definitions."
)


def intent_messages(issue: dict, context_pack: str, feedback: str | None = None,
                    route: str = "theorem",
                    prior_unknowns: list[str] | None = None) -> list[dict]:
    user = f"ISSUE #{issue.get('number')}: {_issue_prose(issue)}\n"
    if context_pack:
        user += "\nAvailable declarations you may reference:\n" + context_pack
    if route == "defs":
        user += INTENT_DEFS_ADDENDUM
        if prior_unknowns:
            user += ("\nPrior attempts guessed these MISSING declarations — define "
                     "equivalents where sensible: " + ", ".join(prior_unknowns))
    if feedback:
        user += ("\n\n" + feedback
                 + "\nProduce a REVISED intent that fixes this; respond with the same JSON shape.")
    return [{"role": "system", "content": INTENT_SYSTEM}, {"role": "user", "content": user}]


def parse_intent(reply: str) -> dict | None:
    """Parse a magistral intent reply into a dict. None unless it carries a `statement` plus the
    naming meta (`module_name`, `benchmark_id`) the mechanical emit needs."""
    v = _extract_json(reply)
    if not isinstance(v, dict) or not v.get("statement") or not v.get("module_name") \
            or not v.get("benchmark_id"):
        return None
    v.setdefault("objects", [])
    v.setdefault("docstring", "")
    v.setdefault("deferred", [])
    v.setdefault("definitions", [])
    return v


def draft_intent(issue: dict, context_pack: str, *, chat_fn, feedback: str | None = None,
                 route: str = "theorem", prior_unknowns: list[str] | None = None) -> dict:
    """Stage 1: magistral SPECIFIES the intended statement (prose + objects + naming meta) from the
    issue. No Lean. `feedback` (a `render_gate_feedback` block from a rejected previous attempt)
    turns this into a REVISION round; `route="defs"` adds the new-definitions contract, with
    `prior_unknowns` (declarations earlier drafts guessed at) as hints. Returns
    `{ok, intent, tokens}`."""
    content, tokens = chat_fn(intent_messages(issue, context_pack, feedback,
                                              route=route, prior_unknowns=prior_unknowns))
    intent = parse_intent(content)
    return {"ok": intent is not None, "intent": intent, "tokens": tokens}


def formalize_messages(intent: dict, grounding: str, revision_note: str = "") -> list[dict]:
    objs = ", ".join(intent.get("objects") or []) or "(none named)"
    user = (f"INTENDED STATEMENT:\n{intent['statement']}\n\n"
            f"CONSUME THESE DECLARATIONS: {objs}\n")
    defs = [d for d in (intent.get("definitions") or []) if isinstance(d, dict)]
    if defs:
        spec = "\n".join(
            f"- {d.get('name')} : {d.get('signature', '?')} — {d.get('meaning', '')}"
            + (f" (built from: {', '.join(d.get('built_from') or [])})"
               if d.get("built_from") else "")
            for d in defs)
        user += ("\nNEW DEFINITIONS TO INTRODUCE (this module defines them):\n" + spec
                 + "\nEmit ONE ```lean block containing these definitions (complete — no "
                 "sorry; each built from existing constants) followed by the single theorem "
                 "stated THROUGH them, ending `:= by sorry`.\n")
    if grounding:
        user += "\nAVAILABLE SIGNATURES:\n" + grounding
    if revision_note:
        user += "\n\n" + revision_note
    return [{"role": "system", "content": FORMALIZE_SYSTEM}, {"role": "user", "content": user}]


# the elaborator emits BOTH spellings across versions: `Unknown identifier
# \`X\`` (capital U, backticks — the live 2026-07-17 format) and `unknown
# constant 'X'`. The original straight-quote-only pattern silently never fired
# in production (pinned by test_unknown_identifiers_match_live_elaborator_format).
_UNKNOWN_RE = re.compile(r"[Uu]nknown (?:identifier|constant) [`']([^`']+)[`']")


def _unknown_identifiers(errors) -> list[str]:
    """The `X` in `unknown identifier 'X'` / `unknown constant 'X'` elaboration errors, deduped —
    the names to retrieve real candidates for during repair."""
    out, seen = [], set()
    for e in errors:
        for m in _UNKNOWN_RE.findall(str(e)):
            if m not in seen:
                seen.add(m)
                out.append(m)
    return out


def loogle_candidates(name: str, *, main_repo: str, run_fn=None) -> str:
    """Loogle hits for `name` via `scripts/loogle.sh` — UNVERIFIED candidates (the public index
    tracks a newer Mathlib than our pin; the elaborator gates bad ones). Returns candidate text or
    ''. `run_fn` injectable for tests."""
    if run_fn is None:
        def run_fn(nm):
            try:
                out = subprocess.run([os.path.join(main_repo, "scripts", "loogle.sh"), nm],
                                     capture_output=True, text=True, timeout=20)
                return out.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                return ""
    return run_fn(name)


def _repair_hint(errors) -> str:
    """Targeted repair hints for RECURRING Leanstral formalization errors (like the `^`-not-`²`
    hint), keyed off the elaboration error text — the generic feedback alone couldn't fix these
    (e.g. #67 hit the same `HMul ℝ (ℝ → ℝ → ℝ)` under-application 3 rounds running). '' if none
    match."""
    blob = "\n".join(str(e) for e in errors)
    hints = []
    if re.search(r"type class\s+H(?:Mul|Add|Sub|Div|Pow).*→", blob, re.DOTALL):
        hints.append("A term is a PARTIALLY-APPLIED function (a `… → …` where a value is "
                     "expected): apply every MathFin/Mathlib definition to ALL its arguments "
                     "(e.g. `MathFin.zcb r t T`, never `MathFin.zcb r`).")
    if "invalid 'import' command" in blob:
        hints.append("Do NOT write any `import` inside the theorem — the module already imports "
                     "Mathlib and the pointer modules.")
    return ("\n" + "\n".join(hints)) if hints else ""


def formalize_with_repair(intent: dict, grounding: str, *, issue: dict, chat_fn, check_fn,
                          emit_fn, rounds: int = 3, retrieve_fn=None,
                          token_budget: int = 40_000, proactive_premises: str = "",
                          revision_note: str = "", log=lambda m: None) -> dict:
    """Stage 2: Leanstral FORMALIZES `intent` into an elaborating stub, repairing against the
    elaborator. On an error the compiler message is fed back to Leanstral; for `unknown identifier
    X`, `retrieve_fn(X)` (loogle) candidates are appended. The naming meta rides from `intent`.
    Early-aborts once cumulative tokens exceed `token_budget` so a doomed draft can't burn every
    round (a hard issue like #61 else spends ~77k/draw). `revision_note` (a `render_gate_feedback`
    block) rides the opening message on semantic-repair rounds — the formalizer must see the gate
    verdict too (the #67 shallow drafts inlined `let`s even when the INTENT named `MathFin.zcb`).
    `log` receives a one-line diagnostic per round (reply size, lean-block?, elab error) so a
    failure is not opaque. Returns `{ok, stub, meta, lean_text, entry, tokens}`."""
    if proactive_premises:
        grounding = (grounding + "\n\n── LIKELY-RELEVANT PREMISES (rank by cosine; "
                     "verify they elaborate under our pin) ──\n" + proactive_premises)
    meta = {"module_name": intent["module_name"], "benchmark_id": intent["benchmark_id"],
            "docstring": intent.get("docstring", ""), "deferred": intent.get("deferred", [])}
    messages = formalize_messages(intent, grounding, revision_note)
    tokens = 0
    unknowns: list[str] = []   # every `Unknown identifier X` guessed across rounds —
    #                            the defs the model thinks SHOULD exist (routing evidence)
    for i in range(max(1, rounds)):
        if tokens >= token_budget:
            log(f"round {i + 1}: aborted — token budget reached ({tokens} >= {token_budget})")
            break   # doomed draft — stop before another expensive, likely-futile round
        content, tk = chat_fn(messages)
        tokens += tk
        stub = extract_lean_code(content)
        if stub is None:
            log(f"round {i + 1}: no ```lean block (reply {len(content or '')}c, {tk} tok)")
            messages += [
                _assistant(content),
                {"role": "user", "content":
                 "Output exactly one ```lean block: a single "
                 "`theorem NAME <binders> : <conclusion> := by sorry`."}]
            continue
        # the ACTUAL defs this round's stub introduces (empty on a plain theorem
        # stub) ride the meta so emit discloses them (`-- new-defs:` header).
        round_meta = {**meta, "definitions": drafted_def_names(stub)}
        try:
            lean_text, entry, _placement = emit_fn(issue, stub, round_meta)
        except Exception as e:  # noqa: BLE001 — surface the assembly failure to the model
            log(f"round {i + 1}: assembly failed ({e})")
            messages += [
                _assistant(content),
                {"role": "user", "content":
                 f"The theorem could not be assembled ({e}). Re-output a single "
                 "well-formed `theorem … := by sorry`."}]
            continue
        elab = check_fn(lean_text)
        if not elab.get("errors") and elab.get("sorry_count", 0) == 1:
            log(f"round {i + 1}: elaborates ✓ ({tokens} tok total)")
            return {"ok": True, "stub": stub, "meta": round_meta, "lean_text": lean_text,
                    "entry": entry, "tokens": tokens, "unknowns": unknowns}
        errs = elab.get("errors", [])
        log(f"round {i + 1}: {len(errs)} elab error(s); first: {str(errs[0])[:180] if errs else '?'}")
        feedback = ("That statement does not elaborate in Lean:\n```\n"
                    + "\n".join(str(e) for e in errs[:8]) + "\n```\n"
                    "Fix ONLY the statement, keep it faithful to the intended statement and still "
                    "ending in `:= by sorry`. Use `^` for powers (never Unicode `²`); "
                    "`Real.exp`/`Real.log`/`Real.sqrt`.")
        feedback += _repair_hint(errs)
        for nm in _unknown_identifiers(errs):
            if nm not in unknowns:
                unknowns.append(nm)
            if retrieve_fn:
                cand = retrieve_fn(nm)
                if cand:
                    feedback += f"\n\nCandidates for `{nm}` (verify they elaborate under our pin):\n{cand}"
        messages += [_assistant(content),
                     {"role": "user", "content": feedback}]
    return {"ok": False, "stub": None, "meta": None,
            "lean_text": None, "entry": None, "tokens": tokens, "unknowns": unknowns}


def fidelity_messages(intent: dict, stub: str) -> list[dict]:
    return [{"role": "system", "content": FIDELITY_SYSTEM},
            {"role": "user",
             "content": f"INTENDED STATEMENT:\n{intent['statement']}\n\nLEAN:\n```lean\n{stub}\n```"}]


def intent_fidelity_check(intent: dict, stub: str, *, reason_fn) -> dict:
    """The folded roundtrip: does Leanstral's Lean faithfully render magistral's intent? Magistral
    compares the stub against its own step-1 intended statement. Soft + lenient — reject ONLY on an
    explicit `faithful: false`. Returns `{faithful, verdict, tokens}`."""
    content, tokens = reason_fn(fidelity_messages(intent, stub))
    v = _extract_json(content) or {}
    return {"faithful": v.get("faithful") is not False,
            "verdict": v.get("verdict", ""), "tokens": tokens}


# --- kernel-grade faithfulness gates (labs-leanstral via run_target) ----------

# Lightened gates. Each faithfulness-gate attempt is a Lean daemon elaboration, and
# the two gates were the bulk of an issue's daemon load (fanout 2 x 2 rounds each ≈ 8
# checks) — the load that let one spinning candidate wedge the daemon. The gate is a
# cheapest-first SAFETY NET (catch a gross vacuity / falsity), not a proof to
# maximize: one reasoned pass@1 attempt catches a blatant contradiction, and a subtle
# one is left to the semantic judge + the human merge. Default to pass@1 / single
# round (1 check per gate); tunable per-call for a deeper sweep.
_GATE_FANOUT = 1
_GATE_ROUNDS = 1


def _try_prove(goal: str, sorry_name: str, *, chat_fn, check_fn, budget: int,
               fanout: int = _GATE_FANOUT, rounds: int = _GATE_ROUNDS,
               system_prompt=None) -> tuple[bool, int]:
    """Short pass@k attempt to prove `goal`. Returns `(proved, tokens)` — `proved`
    is True only on an axioms-clean success (run_target's `pass`). `rounds` bounds
    both the sampling rounds and the compiler-feedback repairs (`rounds - 1`)."""
    target = {"id": "gate", "stream": "gate", "statement": goal, "sorry_name": sorry_name}
    res = run_target(target, budget=budget, max_rounds=rounds, chat_fn=chat_fn,
                     check_fn=check_fn, log_fn=lambda r: None, system_prompt=system_prompt,
                     fanout=fanout, repair_rounds=max(0, rounds - 1))
    return res["outcome"] == "pass", res["tokens"]


def hypothesis_rejection(lean_text: str, sorry_name: str, *, chat_fn, check_fn,
                         budget: int, fanout: int = _GATE_FANOUT, rounds: int = _GATE_ROUNDS,
                         system_prompt=None) -> dict:
    """Try to prove `⊢ False` from the stub's hypotheses. A clean proof ⇒ the
    hypotheses are contradictory ⇒ the theorem is vacuously true. Returns
    `{vacuous, tokens}`."""
    proved, tokens = _try_prove(vacuity_goal(lean_text), sorry_name, chat_fn=chat_fn,
                                check_fn=check_fn, budget=budget, fanout=fanout, rounds=rounds,
                                system_prompt=system_prompt)
    return {"vacuous": proved, "tokens": tokens}


def disproof(lean_text: str, sorry_name: str, *, chat_fn, check_fn,
             budget: int, fanout: int = _GATE_FANOUT, rounds: int = _GATE_ROUNDS,
             system_prompt=None) -> dict:
    """Try to prove `⊢ ¬ Concl` under the stub's hypotheses. A clean proof ⇒ the
    statement is false as written. Returns `{false, tokens}`."""
    proved, tokens = _try_prove(disproof_goal(lean_text), sorry_name, chat_fn=chat_fn,
                                check_fn=check_fn, budget=budget, fanout=fanout, rounds=rounds,
                                system_prompt=system_prompt)
    return {"false": proved, "tokens": tokens}


# --- pointers-scoped depth gate (option B) -----------------------------------
#
# The kernel gates catch a FALSE or vacuous statement; they do NOT catch a
# TRUE-but-shallow one — a Mathlib identity in domain clothing (cal-bk-53 reduced to
# `integral_add_compl`; cal-bk-67 inlined the forward-rate formula as `let`s over raw
# reals instead of consuming `MathFin.zcb`). The depth gate is a structural,
# ELABORATOR-grounded check (not an LLM judge, per the rigorous-vs-soft rule): elaborate
# the stub, then a `run_cmd` meta block inspects the theorem's TYPE and requires it to
# USE at least one constant DEFINED in one of the issue's `-- pointers:` MathFin modules.
# If none, the meta throwErrors — surfacing as a daemon error the gate keys on by its
# `depth-gate:` marker. With no pointers there is nothing to scope to, so it falls back
# to requiring any `MathFin.*` constant (namespace fallback).

_DEPTH_MARKER = "depth-gate:"


def _mod_name(pointer: str) -> str:
    """`MathFin/FixedIncome/ZCB.lean` -> the Lean module name `MathFin.FixedIncome.ZCB`."""
    stem = pointer[:-5] if pointer.endswith(".lean") else pointer
    return stem.replace("/", ".")


def depth_probe(lean_text: str, name: str, pointers: list[str]) -> str:
    """The stub + a `run_cmd` meta block that FAILS elaboration unless the theorem's
    TYPE uses a constant DEFINED in one of its pointer modules (pointers-scoped).
    `name` is the decl name (under `namespace MathFin`); `pointers` are repo-relative
    `MathFin/…/X.lean` paths (assumed non-empty — `depth_rejection` skips otherwise)."""
    mods = [_mod_name(p) for p in pointers if p.endswith(".lean")]
    ptr_list = ", ".join(f"`{m}" for m in mods)
    meta = (
        "\nopen Lean in\n"
        "run_cmd do\n"
        "  let env ← getEnv\n"
        f"  let some ci := env.find? `MathFin.{name}\n"
        f'    | throwError "{_DEPTH_MARKER} declaration {name} not found"\n'
        "  let mods := env.header.moduleNames\n"
        f"  let ptr : List Name := [{ptr_list}]\n"
        "  let used := ci.type.getUsedConstants\n"
        "  let hit := used.any fun c =>\n"
        "    match env.getModuleIdxFor? c with\n"
        "    | some i => ptr.contains mods[i.toNat]!\n"
        "    | none => false\n"
        "  unless hit do\n"
        f'    throwError "{_DEPTH_MARKER} statement type consumes no def from pointer modules {{ptr}}"\n'
    )
    return lean_text.rstrip() + "\n" + meta


def depth_rejection(lean_text: str, name: str, pointers: list[str], *, check_fn) -> dict:
    """Elaborate the depth probe via `check_fn` (the daemon). `shallow=True` iff the meta
    block reported a `depth-gate:` error (the type consumes no pointer-module def). With
    NO pointers the gate is inapplicable — it SKIPS (a missing Pointers section is a
    metadata gap, not a shallowness verdict; the stub carries no MathFin import to consume
    anyway). Fails OPEN too: a daemon-communication error is NOT a depth verdict, so an
    infra hiccup never rejects a good target (like the prover gates). No prover call ⇒
    `tokens=0`."""
    mods = [p for p in pointers if p.endswith(".lean")]
    if not mods:
        return {"shallow": False, "tokens": 0, "verdict": "no pointers — depth gate skipped"}
    res = check_fn(depth_probe(lean_text, name, mods))
    depth_errs = [str(e) for e in (res.get("errors") or []) if _DEPTH_MARKER in str(e)]
    return {"shallow": bool(depth_errs), "tokens": 0, "verdict": "; ".join(depth_errs[:2])}


# --- triviality gate (the #67 class) ------------------------------------------
#
# The depth gate checks WHAT the type consumes; it does not check whether the
# statement SAYS anything: cal-bk-67's type referenced `zcb` through `let`-bound
# definitions and still proved by `rfl`. This gate catches that class at DRAFT
# time (open-pr's rfl guard stays as defense in depth, but by then the prove
# compute is already spent): splice the stub's `sorry` into `first | rfl | simp`
# and elaborate — a clean close means the statement is a definitional/simp
# restatement with no mathematical content. The boundary is deliberate: bare
# `rfl` + goal-only `simp` (no `simp_all`, no `grind`), so easy-but-REAL content
# is not over-filtered. Zero prover tokens; fail-open like the depth gate.

_TRIV_TACTIC = "by first | rfl | simp"
_SORRY_RE = re.compile(r":=\s*(?:by\s+)?sorry\b")


def triviality_goal(lean_text: str) -> str | None:
    """The stub with its `sorry` proof spliced to `first | rfl | simp`. None when
    no `:= [by] sorry` is present to splice (malformed stub — fail open)."""
    new, n = _SORRY_RE.subn(":= " + _TRIV_TACTIC, lean_text, count=1)
    return new if n else None


def triviality_rejection(lean_text: str, *, check_fn) -> dict:
    """Elaborate the triviality goal via `check_fn` (the daemon). `trivial=True`
    iff the splice closes CLEAN (no errors, no sorry left) — the statement holds
    definitionally / by the vanilla simp set alone. The tactic FAILING (the
    healthy case) and daemon errors both leave errors non-empty ⇒ not a verdict.
    No prover call ⇒ `tokens=0`."""
    goal = triviality_goal(lean_text)
    if goal is None:
        return {"trivial": False, "tokens": 0, "verdict": "no sorry to splice — skipped"}
    res = check_fn(goal)
    trivial = not res.get("errors") and res.get("sorry_count", 0) == 0
    verdict = ("closed by `first | rfl | simp` — definitionally/simp-trivial, no content"
               if trivial else "")
    return {"trivial": trivial, "tokens": 0, "verdict": verdict}


# --- primitives-aware routing (F3+F2 of the composed design) ------------------
#
# One measured property routes every issue: does the library already have the
# primitives its statement needs? Static measurement (pointer modules export
# consumables) + runtime evidence (a prior depth-exhausted attempt) pick between
# the theorem-stub path and the definitions path. Design:
# docs/superpowers/specs/2026-07-17-primitives-aware-routing-design.md.

_DEF_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:noncomputable\s+)?(?:def|abbrev|structure)\s+([A-Za-z0-9_'.]+)",
    re.MULTILINE,
)


def count_pointer_defs(main_repo: str, pointers: list[str]) -> int:
    """Consumable exports (`def`/`abbrev`/`structure`) in the issue's pointer
    modules — the routing measurement. 0 ⇒ a theorem-only stub's TYPE has nothing
    to consume ⇒ the definitions path. Missing files count 0 (fail toward defs)."""
    n = 0
    for p in pointers:
        if not p.endswith(".lean"):
            continue
        try:
            with open(os.path.join(main_repo, p), encoding="utf-8") as f:
                n += len(_DEF_RE.findall(f.read()))
        except OSError:
            continue
    return n


def classify_refill(rec: dict) -> str:
    """Obstruction family for a refill `attempted` record — the drafter's triage
    analogue. Computed at write time (rides the record in refill-history.jsonl)
    and at read time for records written before the field existed."""
    out = rec.get("outcome", "")
    if out == "seeded":
        return "seeded"
    if out == "depth":
        return "needs_primitives"
    if out in ("newdef_depth", "ungrounded"):
        return "defs_rejected"
    if out == "trivial":
        return "trivial_restatement"
    if out in ("unfaithful", "drift"):
        return "fidelity"
    if out in ("intent", "formalize"):
        return "undraftable"
    if out in ("vacuous", "false"):
        return "statement_wrong"
    if out == "budget":
        return "budget"
    return "infra"


def load_refill_families(history_path: str) -> dict[int, str]:
    """Issue number → family of its LATEST refill-history record. The router's
    runtime evidence; tolerant of junk lines and of the file being absent."""
    fams: dict[int, str] = {}
    try:
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("issue") is not None:
                    fams[int(rec["issue"])] = rec.get("family") or classify_refill(rec)
    except OSError:
        pass
    return fams


def load_prior_unknowns(history_path: str) -> dict[int, list[str]]:
    """Issue → union (first-seen order) of `unknown_identifiers` across its
    refill-history rows — the missing declarations earlier drafts guessed at;
    they become defs-route hints ("define equivalents where sensible")."""
    out: dict[int, list[str]] = {}
    try:
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("issue") is None:
                    continue
                bucket = out.setdefault(int(rec["issue"]), [])
                for row in rec.get("history", []) or []:
                    for u in row.get("unknown_identifiers", []) or []:
                        if u not in bucket:
                            bucket.append(u)
    except OSError:
        pass
    return out


def route_for(issue: dict, *, def_count: int, family: str | None) -> str:
    """`theorem` (statement can consume existing pointer defs) or `defs` (the
    library lacks the primitives — draft definitions + the theorem). Runtime
    evidence beats static measurement: #53 measures consumable via chooserPrice,
    but its faithful statement can't use it, so its depth-exhaustion routes it."""
    if family == "needs_primitives":
        return "defs"
    if def_count == 0:
        return "defs"
    return "theorem"


def order_by_route(issues: list[dict]) -> list[dict]:
    """Theorem-route issues first (cheap wins), stable within each group."""
    return ([i for i in issues if i.get("route", "theorem") == "theorem"]
            + [i for i in issues if i.get("route", "theorem") == "defs"])


# --- definitions-path gates (F1) -----------------------------------------------
#
# On the defs route the drafter introduces 1-3 defs + one theorem; the pointer
# depth gate is replaced by ONE daemon probe with two verdicts:
#   newdef_depth — the theorem's TYPE must use ≥1 drafted def (else the defs are
#                  decoration and the statement is still raw);
#   ungrounded   — every drafted def's VALUE must use ≥1 IMPORTED constant
#                  (`getModuleIdxFor?.isSome`) — an identity/self-referential
#                  wrapper fails; honest defs over real structure pass.
# Wrapping real content in honest defs is exactly what we WANT (knockInPayoff
# over indicator integrals); design quality stays with R's merge review.

_DEFS_MARKER = "defs-gate:"


def drafted_def_names(stub: str) -> list[str]:
    """Names of the `def`/`abbrev`/`structure` declarations the stub introduces,
    in order (the theorem is not one of them)."""
    return _DEF_RE.findall(stub)


def defs_probe(lean_text: str, thm_name: str, def_names: list[str]) -> str:
    """The stub + a `run_cmd` meta block that FAILS elaboration unless (a) the
    theorem's type uses ≥1 drafted def (newdef_depth) and (b) every drafted def's
    value uses ≥1 imported constant (ungrounded)."""
    names = ", ".join(f"`MathFin.{d}" for d in def_names)
    meta = (
        "\nopen Lean in\n"
        "run_cmd do\n"
        "  let env ← getEnv\n"
        f"  let some thm := env.find? `MathFin.{thm_name}\n"
        f'    | throwError "{_DEFS_MARKER} theorem {thm_name} not found"\n'
        f"  let newDefs : List Name := [{names}]\n"
        "  let used := thm.type.getUsedConstants\n"
        "  unless newDefs.any (fun d => used.contains d) do\n"
        f'    throwError "{_DEFS_MARKER} newdef_depth: the theorem\'s type uses none of '
        'the drafted defs {newDefs}"\n'
        "  for d in newDefs do\n"
        "    let some ci := env.find? d\n"
        f'      | throwError "{_DEFS_MARKER} drafted def {{d}} not found"\n'
        "    let some v := ci.value?\n"
        f'      | throwError "{_DEFS_MARKER} ungrounded: {{d}} has no value"\n'
        "    let ext := v.getUsedConstants.filter (fun c => (env.getModuleIdxFor? c).isSome)\n"
        "    unless ext.size > 0 do\n"
        f'      throwError "{_DEFS_MARKER} ungrounded: {{d}} is a free-floating wrapper '
        '(its value uses no imported constant)"\n'
    )
    return lean_text.rstrip() + "\n" + meta


def defs_rejection(lean_text: str, thm_name: str, def_names: list[str], *, check_fn) -> dict:
    """Elaborate the defs probe via `check_fn` (the daemon). Returns
    `{failed, gate: "newdef_depth"|"ungrounded"|None, verdict, tokens}`. No defs
    drafted ⇒ instant `newdef_depth` fail (no daemon call — the route's contract
    was ignored). Fails OPEN on unmarked errors, like the other structural gates."""
    if not def_names:
        return {"failed": True, "gate": "newdef_depth", "tokens": 0,
                "verdict": "no definitions drafted — the defs route requires 1-3 new defs"}
    res = check_fn(defs_probe(lean_text, thm_name, def_names))
    errs = [str(e) for e in (res.get("errors") or []) if _DEFS_MARKER in str(e)]
    if not errs:
        return {"failed": False, "gate": None, "verdict": "", "tokens": 0}
    gate = "ungrounded" if "ungrounded" in errs[0] else "newdef_depth"
    return {"failed": True, "gate": gate, "verdict": errs[0], "tokens": 0}


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


# --- semantic-gate feedback (the repair cascade's re-draft signal) ------------
#
# The only repaired failure class used to be compilation (formalize_with_repair);
# every semantic gate was a terminal skip, so the drafter was never told WHY a
# clean-elaborating draft was rejected (design: 2026-07-17-semantic-repair-cascade).
# Each gate gets a repair DIRECTION here; the block is sent to BOTH stages of the
# next attempt (magistral may need to re-frame the statement; leanstral must stop
# inlining what it should consume).

_GATE_INSTRUCTIONS = {
    "intent": "Respond with ONLY one JSON object carrying statement, objects, "
              "module_name, benchmark_id, docstring, deferred — no prose around it.",
    "formalize": "The intended statement could not be rendered into elaborating Lean "
                 "after all repair rounds. Re-specify it using ONLY objects from the "
                 "declarations shown (name each exactly); prefer fewer, concrete "
                 "objects over prose-only quantities.",
    "depth": "The statement's TYPE consumes no definition from the issue's pointer "
             "modules — it restates the mathematics over raw reals (e.g. via `let` or "
             "inlined formulas). Re-state the theorem so its TYPE is EXPRESSED THROUGH "
             "the named MathFin declarations, fully applied (e.g. `MathFin.zcb r 0 T`); "
             "never re-derive or inline their formulas.",
    "trivial": "The statement is closed by `rfl`/`simp` alone — a definitional "
               "restatement with no mathematical content. State the SUBSTANTIVE fact "
               "the issue asks for: an identity or inequality between INDEPENDENTLY "
               "defined quantities, not a definition unfolded into itself.",
    "vacuous": "The hypotheses are mutually contradictory (`False` is provable from "
               "them), so the theorem is vacuously true. Fix the hypothesis set — "
               "check inequality directions and degenerate parameter values.",
    "false": "The NEGATION of the conclusion was PROVED under the hypotheses — the "
             "statement is false AS WRITTEN. The issue's mathematics is presumed "
             "right; the rendering flipped an inequality or sign, swapped arguments, "
             "or omitted a needed hypothesis. Fix the rendering. Do NOT weaken the "
             "conclusion.",
    "unfaithful": "A faithfulness judge found the statement diverges grossly from "
                  "the issue. Address each listed divergence without weakening any "
                  "fact you state.",
    "drift": "The Lean does not faithfully render the intended statement. Re-render "
             "every hypothesis and the full conclusion exactly.",
    "newdef_depth": "The theorem's TYPE must be stated THROUGH the drafted definitions — "
                    "apply each drafted def in the hypotheses/conclusion, never restate "
                    "their formulas inline; and the module must actually contain the "
                    "1-3 definitions.",
    "ungrounded": "A drafted definition is a free-floating wrapper: its body must be "
                  "BUILT FROM existing Mathlib/MathFin constants (an integral, Real.exp, "
                  "max, an existing MathFin price), not a bare variable or a "
                  "self-referential shell.",
}


def render_gate_feedback(gate: str, detail: str, stub: str | None) -> str:
    """The re-draft feedback block for a semantic-gate rejection: the rejected stub
    (when one exists), the gate's own verdict, and the gate-specific revision
    instruction."""
    txt = f"PREVIOUS ATTEMPT — rejected by the `{gate}` gate"
    if detail:
        txt += f": {detail}"
    txt += "\n"
    if stub:
        txt += f"```lean\n{stub.strip()}\n```\n"
    txt += "REVISE: " + _GATE_INSTRUCTIONS.get(
        gate, "Fix the reported failure without weakening any fact.")
    return txt


def semantic_verdict(*, lean_text: str, stub: str, name: str, intent: dict, issue: dict,
                     deferred: list[str], reason_fn, prove_fn, check_fn, gate_budget: int,
                     depth_gate: bool = True, triviality_gate: bool = True,
                     route: str = "theorem", def_names: list[str] | None = None,
                     system_prompt=None) -> tuple[dict | None, int]:
    """Run the semantic gate battery on an ELABORATING draft, cheapest-first:
    depth (theorem route) / defs consumption+grounding (defs route) → triviality
    (structural, zero tokens) → hypothesis-rejection → disproof (kernel,
    leanstral) → issue-faithfulness → intent-fidelity (magistral judges).
    Returns `(failure, tokens)`: failure is None when every gate passes, else
    `{gate, detail}` for `render_gate_feedback`. The battery's DIVERSITY is the
    anti-Goodhart defense of the repair loop: a re-draft that games one gate still
    faces five others (and open-pr's honesty guards + the human merge after that)."""
    tokens = 0
    if route == "defs":
        dr = defs_rejection(lean_text, name, def_names or [], check_fn=check_fn)
        tokens += dr["tokens"]
        if dr["failed"]:
            return {"gate": dr["gate"], "detail": dr.get("verdict", "")}, tokens
    elif depth_gate:
        dep = depth_rejection(lean_text, name, issue.get("pointers", []), check_fn=check_fn)
        tokens += dep["tokens"]
        if dep["shallow"]:
            return {"gate": "depth", "detail": dep.get("verdict", "")}, tokens
    if triviality_gate:
        triv = triviality_rejection(lean_text, check_fn=check_fn)
        tokens += triv["tokens"]
        if triv["trivial"]:
            return {"gate": "trivial", "detail": triv.get("verdict", "")}, tokens
    vac = hypothesis_rejection(lean_text, name, chat_fn=prove_fn, check_fn=check_fn,
                               budget=gate_budget, system_prompt=system_prompt)
    tokens += vac["tokens"]
    if vac["vacuous"]:
        return {"gate": "vacuous", "detail": "False is provable from the hypotheses"}, tokens
    dis = disproof(lean_text, name, chat_fn=prove_fn, check_fn=check_fn,
                   budget=gate_budget, system_prompt=system_prompt)
    tokens += dis["tokens"]
    if dis["false"]:
        return {"gate": "false", "detail": "the negated conclusion was proved"}, tokens
    j = judge_faithfulness(issue, stub, chat_fn=reason_fn, deferred=deferred)
    tokens += j["tokens"]
    if not j.get("faithful"):
        detail = j.get("verdict", "")
        if j.get("issues"):
            detail += "; issues: " + "; ".join(str(x) for x in j["issues"][:4])
        return {"gate": "unfaithful", "detail": detail}, tokens
    fid = intent_fidelity_check(intent, stub, reason_fn=reason_fn)
    tokens += fid["tokens"]
    if not fid.get("faithful"):
        return {"gate": "drift", "detail": fid.get("verdict", "")}, tokens
    return None, tokens


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
           queue_dir: str, budget: int, max_issues: int = 1,
           max_attempt_issues: int = 3, gate_budget: int = 20_000, formalize_rounds: int = 3,
           formalize_token_budget: int = 40_000, formalize_fn=None, retrieve_fn=None,
           proactive_fn=None, depth_gate: bool = True, triviality_gate: bool = True,
           semantic_rounds: int = 2, system_prompt=None, log=lambda m: None) -> dict:
    """Draft + gate + stage up to `max_issues` targets from `issues`.

    For each candidate (up to `max_attempt_issues`): intent (magistral `reason_fn`
    SPECIFIES the statement) → formalize-with-repair (leanstral `formalize_fn` writes
    elaborating Lean, compiler-repaired + retrieval-augmented) → the semantic gate
    battery (`semantic_verdict`: depth → triviality → vacuity → disproof → judge →
    intent-fidelity). A gate rejection is NOT terminal: it becomes a
    `render_gate_feedback` block and the issue is RE-DRAFTED — both stages see the
    verdict — up to `semantic_rounds` total attempts (the repair cascade; design:
    2026-07-17-semantic-repair-cascade). `semantic_rounds=1` is the old single-shot
    behavior. An issue that exhausts its rounds stays `status:ready` (never
    auto-closed). A passing target's stub + `.entry.json` are written to `queue_dir`.
    `formalize_fn` defaults to `prove_fn` (both leanstral).

    Returns `{seeded, tokens, attempted}` — `attempted` is the obstruction telemetry:
    one `{issue, attempts, outcome: "seeded"|<last gate>|"error", history}` record per
    issue tried, so a zero-seed tick says exactly which gate ate each issue."""
    formalize_fn = formalize_fn or prove_fn
    seeded, attempted, spent = [], [], 0
    for issue in issues[:max_attempt_issues]:
        if len(seeded) >= max_issues or spent >= budget:
            break
        n = issue.get("number")
        route = issue.get("route", "theorem")
        history, feedback, staged = [], None, False
        # A transient error on ONE issue (e.g. an HTTP 429 that exhausts retries,
        # a daemon hiccup, a malformed draft) must not kill the tick — log it and
        # move to the next candidate.
        try:
            ctx = context_fn(issue)
            for attempt in range(1, max(1, semantic_rounds) + 1):
                if spent >= budget:
                    history.append({"attempt": attempt, "gate": "budget",
                                    "detail": f"refill budget exhausted ({spent} >= {budget})"})
                    break
                di = draft_intent(issue, ctx, chat_fn=reason_fn, feedback=feedback,
                                  route=route, prior_unknowns=issue.get("prior_unknowns"))
                spent += di["tokens"]
                if not di["ok"]:
                    fail = {"gate": "intent", "detail": "no parseable intent reply"}
                    history.append({"attempt": attempt, **fail})
                    log(f"#{n}: no parseable intent (attempt {attempt})")
                    feedback = render_gate_feedback(fail["gate"], fail["detail"], None)
                    continue
                intent = di["intent"]
                if route == "defs" and not (intent.get("definitions") or []):
                    fail = {"gate": "intent",
                            "detail": "defs route: the intent must name 1-3 new definitions"}
                    history.append({"attempt": attempt, **fail})
                    log(f"#{n}: intent named no definitions (attempt {attempt})")
                    feedback = render_gate_feedback(fail["gate"], fail["detail"], None)
                    continue

                proactive = proactive_fn(intent["statement"]) if proactive_fn else ""
                fr = formalize_with_repair(intent, ctx, issue=issue, chat_fn=formalize_fn,
                                           check_fn=check_fn, emit_fn=emit_target_files,
                                           rounds=formalize_rounds, retrieve_fn=retrieve_fn,
                                           token_budget=formalize_token_budget,
                                           proactive_premises=proactive,
                                           revision_note=feedback or "",
                                           log=lambda m: log(f"#{n} formalize {m}"))
                spent += fr["tokens"]
                unknowns = fr.get("unknowns") or []
                if not fr["ok"]:
                    fail = {"gate": "formalize",
                            "detail": f"no elaborating Lean after {formalize_rounds} rounds"}
                    row = {"attempt": attempt, **fail}
                    if unknowns:
                        row["unknown_identifiers"] = unknowns
                    history.append(row)
                    log(f"#{n}: {fail['detail']} (attempt {attempt})")
                    feedback = render_gate_feedback(fail["gate"], fail["detail"], None)
                    continue
                stub, lean_text, entry = fr["stub"], fr["lean_text"], fr["entry"]
                name = split_statement(stub)[0]
                deferred = normalize_deferred((fr.get("meta") or {}).get("deferred"))

                fail, gate_tokens = semantic_verdict(
                    lean_text=lean_text, stub=stub, name=name, intent=intent, issue=issue,
                    deferred=deferred, reason_fn=reason_fn, prove_fn=prove_fn,
                    check_fn=check_fn, gate_budget=gate_budget, depth_gate=depth_gate,
                    triviality_gate=triviality_gate, route=route,
                    def_names=drafted_def_names(stub) if route == "defs" else None,
                    system_prompt=system_prompt)
                spent += gate_tokens
                if fail is None:
                    paths = _write_target(queue_dir, n, lean_text, entry)
                    seeded.append({"id": f"cal-bk-{n}", "issue": n, "paths": paths})
                    staged = True
                    log(f"#{n}: staged cal-bk-{n} (attempt {attempt})")
                    break
                row = {"attempt": attempt, **fail}
                if unknowns:
                    row["unknown_identifiers"] = unknowns
                history.append(row)
                log(f"#{n}: {fail['gate']} — {fail['detail']} (attempt {attempt})\n"
                    f"  statement: {stub}")
                feedback = render_gate_feedback(fail["gate"], fail["detail"], stub)
            outcome = "seeded" if staged else (history[-1]["gate"] if history else "error")
            rec = {"issue": n, "attempts": attempt, "outcome": outcome, "history": history}
            rec["family"] = classify_refill(rec)
            attempted.append(rec)
        except Exception as e:  # noqa: BLE001 — resilience: skip the issue, not the tick
            log(f"#{n}: error ({type(e).__name__}: {e}) — skipping")
            history.append({"attempt": len(history) + 1, "gate": "error",
                            "detail": f"{type(e).__name__}: {e}"})
            rec = {"issue": n, "attempts": len(history), "outcome": "error",
                   "history": history}
            rec["family"] = classify_refill(rec)
            attempted.append(rec)
            continue
    return {"seeded": seeded, "tokens": spent, "attempted": attempted}


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


def build_retrieve_fns(*, backend, main_repo, index_dir, k, embed_model, api_key):
    """(reactive_retrieve_fn, proactive_fn). Embedding backend ranks the whole
    MathFin corpus; proactive_fn retrieves on the intent STATEMENT. Falls open to
    loogle (reactive only) when the embedding cache is absent."""
    loogle_fn = lambda nm: loogle_candidates(nm, main_repo=main_repo)  # noqa: E731
    if backend != "embedding":
        return loogle_fn, None
    premises = _embed.load_premises(index_dir)
    cache = _embed.cache_path(index_dir, embed_model)
    idx = _embed.EmbeddingIndex.load(cache, premises, embed_model) if premises else None
    if idx is None:
        return loogle_fn, None   # fails-open — no index/cache ⇒ loogle
    embed_fn = lambda texts: _embed.mistral_embed(texts, api_key=api_key, model=embed_model)  # noqa: E731
    reactive = _embed.make_embedding_retrieve_fn(idx, k, embed_fn)
    proactive = lambda stmt: idx.retrieve(stmt, k, embed_fn)  # noqa: E731
    return reactive, proactive


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
    p.add_argument("--intent-model", default=None, help="magistral: stage-1 intent (default: config)")
    p.add_argument("--formalize-model", default=None, help="leanstral: stage-2 formalize (default: config)")
    p.add_argument("--prover-model", default=None)
    p.add_argument("--draft-max-tokens", type=int, default=None)
    p.add_argument("--formalize-rounds", type=int, default=None)
    p.add_argument("--depth-gate", dest="depth_gate", action=argparse.BooleanOptionalAction,
                   default=None, help="pointers-scoped depth gate (default: config)")
    p.add_argument("--triviality-gate", dest="triviality_gate",
                   action=argparse.BooleanOptionalAction, default=None,
                   help="rfl/simp triviality gate (default: config)")
    p.add_argument("--semantic-rounds", type=int, default=None,
                   help="total draft attempts per issue incl. feedback re-drafts (default: config)")
    p.add_argument("--retrieval", dest="retrieval", action=argparse.BooleanOptionalAction,
                   default=None, help="loogle-augmented repair retrieval (default: config)")
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
    prover_model = pick(args.prover_model, cfg.prover_model)
    draft_max_tokens = pick(args.draft_max_tokens, cfg.draft_max_tokens)
    depth_gate = pick(args.depth_gate, cfg.depth_gate)
    triviality_gate = pick(args.triviality_gate, cfg.triviality_gate)
    semantic_rounds = pick(args.semantic_rounds, cfg.semantic_rounds)
    intent_model = pick(args.intent_model, cfg.intent_model)
    formalize_model = pick(args.formalize_model, cfg.formalize_model)
    formalize_rounds = pick(args.formalize_rounds, cfg.formalize_rounds)
    retrieval = pick(args.retrieval, cfg.retrieval)
    formalize_token_budget = cfg.formalize_token_budget

    queue_dir = args.queue_dir or os.path.join(_foundry_root(), "targets", "queue")

    seeded_nums = _already_seeded(queue_dir)
    issues = [i for i in prepare_issues(_fetch_issues(args.slug))
              if i["number"] not in seeded_nums
              and (args.only is None or i["number"] == args.only)]
    if not issues:
        print(json.dumps({"seeded": [], "tokens": 0, "reason": "no unseeded ready issues"}))
        return 0

    # primitives-aware routing: measure each issue's consumables, consult the
    # refill history's runtime evidence, route theorem/defs, cheap wins first.
    hist_path = os.path.join(_foundry_root(), "runs", "refill-history.jsonl")
    families = load_refill_families(hist_path)
    prior_unknowns = load_prior_unknowns(hist_path)
    for i in issues:
        i["route"] = route_for(i, def_count=count_pointer_defs(args.main_repo, i.get("pointers", [])),
                               family=families.get(i["number"]))
        i["prior_unknowns"] = prior_unknowns.get(i["number"], [])
    issues = order_by_route(issues)
    print(f"[refill] routes: " + ", ".join(f"#{i['number']}→{i['route']}" for i in issues[:8]),
          file=sys.stderr)

    prove_system = build_system_prompt(args.main_repo)   # the leaf-prover gate doctrine

    def reason_fn(msgs):   # magistral: stage-1 intent + judge + intent-fidelity
        return mistral_chat(msgs, api_key=api_key, model=intent_model,
                            max_tokens=draft_max_tokens)

    def formalize_fn(msgs):   # leanstral: stage-2 formalize
        return mistral_chat(msgs, api_key=api_key, model=formalize_model,
                            reasoning_effort="high")

    def prove_fn(msgs):   # leanstral: kernel gates
        return mistral_chat(msgs, api_key=api_key, model=prover_model,
                            reasoning_effort="high")

    def context_fn(issue):
        ptrs = issue.get("pointers", [])
        return extract_signatures(args.main_repo, ptrs) if ptrs else ""

    from scout_index import default_index_dir
    index_dir = default_index_dir()
    if retrieval:
        retrieve_fn, proactive_fn = build_retrieve_fns(
            backend=cfg.retrieval_backend, main_repo=args.main_repo, index_dir=index_dir,
            k=cfg.retrieval_k, embed_model=cfg.embed_model, api_key=api_key)
    else:
        retrieve_fn, proactive_fn = None, None

    res = refill(issues, reason_fn=reason_fn, prove_fn=prove_fn, check_fn=daemon_check,
                 context_fn=context_fn, queue_dir=queue_dir, budget=budget,
                 max_issues=max_issues, max_attempt_issues=max_attempt, gate_budget=gate_budget,
                 formalize_rounds=formalize_rounds, formalize_token_budget=formalize_token_budget,
                 formalize_fn=formalize_fn, retrieve_fn=retrieve_fn, proactive_fn=proactive_fn,
                 depth_gate=depth_gate, triviality_gate=triviality_gate,
                 semantic_rounds=semantic_rounds, system_prompt=prove_system,
                 log=lambda m: print(f"[refill] {m}", file=sys.stderr))

    # obstruction telemetry: one row per issue tried, so a zero-seed tick says which
    # gate ate each issue and whether feedback moved the draft between rounds
    # (triage.py's analogue for the drafter).
    hist = os.path.join(_foundry_root(), "runs", "refill-history.jsonl")
    os.makedirs(os.path.dirname(hist), exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    for rec in res.get("attempted", []):
        append_jsonl(hist, {"ts": stamp, **rec})

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


def normalize_deferred(val) -> list[str]:
    """The drafter's declared-deferred facts (json `deferred`) as a clean list of
    one-line phrases: the parts of a multi-fact issue this subset does NOT prove, to
    become follow-up issues. Accepts a list (json array) or a single string; drops
    blanks. `[]` (covers the whole issue) is the common case."""
    if val is None:
        return []
    items = val if isinstance(val, list) else [val]
    out = []
    for x in items:
        s = str(x).strip()
        if s:
            out.append(s)
    return out


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
    deferred = normalize_deferred(meta.get("deferred"))
    pointers = issue.get("pointers", [])
    name, binders, concl = split_statement(stub)

    new_defs = [str(d) for d in (meta.get("definitions") or [])]
    header_lines = [
        f"-- pointers: {', '.join(pointers)}",
        f"-- main-module: {main_module}",
        f"-- benchmark: {_BENCHMARK}",
        f"-- benchmark-id: {benchmark_id}",
        f"-- source-issue: {n}",
    ]
    if deferred:
        # this proof is a faithful SUBSET of the issue; the deferred facts ride the
        # header (build_manifest → manifest → open-pr surfaces them as follow-ups).
        header_lines.append(f"-- deferred: {'; '.join(deferred)}")
    if new_defs:
        # this module INTRODUCES definitions (the defs route) — open-pr labels the
        # PR `new-defs`, the architecture-heavy review class.
        header_lines.append(f"-- new-defs: {', '.join(new_defs)}")
    headers = "\n".join(header_lines)
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
    scope = (
        f"Full formal proof in {main_module} (magistral-drafted statement, "
        "leanstral proof). Re-export from MathFin. Axioms-clean."
    )
    provenance = {
        "statement_source": "magistral-autoform",
        "statement_model": "magistral-medium",
        "source": "leanstral-autoform",
        "model": "labs-leanstral-1-5",
        "issue": n,
    }
    if deferred:
        # honest disclosure in the entry itself: `full` proof of a SUBSET of the issue.
        scope += (f" Faithful SUBSET of issue #{n}; deferred to follow-up issues: "
                  f"{'; '.join(deferred)}.")
        provenance["deferred"] = deferred
    if new_defs:
        scope += f" Introduces definitions: {', '.join(new_defs)} (new-defs review class)."
        provenance["new_defs"] = new_defs
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
            "formalization_scope": scope,
            "provenance": provenance,
        },
    }
    placement = {
        "main_module": main_module,
        "benchmark": _BENCHMARK,
        "benchmark_id": benchmark_id,
        "source_issue": n,
        "deferred": deferred,
    }
    return lean_text, entry, placement


if __name__ == "__main__":
    sys.exit(main())
