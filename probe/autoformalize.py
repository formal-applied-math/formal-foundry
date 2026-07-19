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
from house_context import build_drafter_prompt, build_system_prompt, extract_signatures
from issues import difficulty_rank, select_issues
from pipeline_lib import AutoformalizeConfig
from probe import daemon_check, mistral_chat, run_target
from probe_lib import DEF_RE, append_jsonl, extract_lean_code, lint_violations

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
    "drop a hypothesis, or flip an inequality.\n"
    "- Lint-clean for the library's CI: every `def`/`abbrev`/`structure` carries a `/-- … -/` "
    "docstring immediately above it, and definition names are lowerCamelCase (theorem names "
    "stay snake_case).\n"
    "- State at the NATURAL level of generality: when the fact is algebra over given quantities, "
    "do not hard-wire a concrete model or curve the claim does not require. Prefer the structural "
    "hypothesis that says exactly what is needed — `s.Nonempty` rather than a member-witness "
    "`x ∈ s`, `A ≠ 0` rather than assuming positivity of a quantity that is already provably "
    "positive from its definition.\n"
    "- Definitions bind their arguments in the signature (`def f (x : ℝ) : ℝ := …`), never a "
    "lambda against a `∀` ascription; write anonymous functions with `↦`.\n"
    "- Exactly ONE `sorry` — the CORE theorem, first. You MAY add corollaries AFTER it "
    "(the issue-shaped instantiation of a general core, or per-fact projections of an "
    "`∧`-bundle) as additional theorems proved by sorry-free TERMS applying the core."
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
    "definitions. When the natural shape is a GENERAL core plus the issue-shaped "
    'instantiation, also emit "corollary": {"name": "<snake_case>", "statement": '
    '"<one line>"} — the core carries the proof, the corollary applies it.'
)


# --- Drafter authority wiring (H1) -------------------------------------------
# The drafter (intent + formalize) wrote statements with no pins and no house
# statement-design rules — hence hallucinated constants and depth-gate deaths.
# `set_drafter_prompt` wires the pins + statement-design preamble in once at
# pipeline start (patterns.md read live); '' until wired, so the base system
# prompts are unchanged for callers that never wire it.
_DRAFTER_PROMPT: str = ""


def set_drafter_prompt(main_repo: str) -> None:
    """Wire the live drafter authority (pins + the statement-design section of
    patterns.md) into the intent/formalize system prompts. Call once at pipeline
    start; reads patterns.md live, not at import time."""
    global _DRAFTER_PROMPT
    _DRAFTER_PROMPT = build_drafter_prompt(main_repo)


def _drafter_system(base: str) -> str:
    """Prepend the wired drafter authority to a base drafter system prompt."""
    return (_DRAFTER_PROMPT + "\n" + base) if _DRAFTER_PROMPT else base


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
    return [{"role": "system", "content": _drafter_system(INTENT_SYSTEM)},
            {"role": "user", "content": user}]


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
                 "sorry; each built from existing constants; lowerCamelCase names, each with "
                 "a `/-- … -/` docstring above it) followed by the single theorem "
                 "stated THROUGH them, ending `:= by sorry`.\n")
    cor = intent.get("corollary")
    if isinstance(cor, dict) and cor.get("statement"):
        user += ("\nCOROLLARY TO ADD AFTER THE CORE (a sorry-free theorem proved by a term "
                 f"applying the core): {cor.get('name', 'corollary')} — {cor['statement']}\n")
    if grounding:
        user += "\nAVAILABLE SIGNATURES:\n" + grounding
    if revision_note:
        user += "\n\n" + revision_note
    return [{"role": "system", "content": _drafter_system(FORMALIZE_SYSTEM)},
            {"role": "user", "content": user}]


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
    # (the "invalid 'import' command" hint is retired — _prelint_stub now strips stub-level
    #  imports deterministically at emit time, so that error can no longer reach the model.)
    if "unknown universe level" in blob:  # A14 backstop (prelint rewrites the common cases)
        hints.append("Do NOT write an explicit universe variable (`Type u`, `Sort v`) or a "
                     "`universe` declaration — the build uses `autoImplicit false`, so `u` is "
                     "unbound. Use the auto-generalized `Type*` / `Sort*` instead.")
    if re.search(r"typeclass instance problem is stuck.*\?m", blob, re.DOTALL):  # A3
        hints.append("A typeclass instance metavariable is stuck (`?m…`): name the ambiguous "
                     "implicit explicitly at the call site (e.g. `(μ := μ)`) or `@`-apply the "
                     "lemma, so instance search is not left guessing.")
    if re.search(r"failed to synthesize.*\b(?:LE|LT|OfNat|Zero|One)\s+Type\b", blob, re.DOTALL):  # A11
        hints.append("`ℝ≥0` was misparsed as `ℝ ≥ 0` (a Prop), so an order/numeral class on "
                     "`Type` cannot be synthesized: add `open scoped NNReal` and write `ℝ≥0`.")
    if _unknown_identifiers(errors):  # A2
        hints.append("For an unknown identifier: grep the PINNED `.lake/packages/mathlib` source "
                     "for the name and its namespace before re-guessing (loogle tracks a newer "
                     "pin — an upper bound only); do not invent a constant.")
    return ("\n" + "\n".join(hints)) if hints else ""


def formalize_with_repair(intent: dict, grounding: str, *, issue: dict, chat_fn, check_fn,
                          emit_fn, rounds: int = 3, retrieve_fn=None,
                          token_budget: int = 40_000, proactive_premises: str = "",
                          revision_note: str = "", derivable_fn=None,
                          log=lambda m: None) -> dict:
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
    advised_bundle = False   # the ∧-advisory is ONE soft round, never a hard gate
    lint_repaired = 0        # H11 telemetry: lint-dirty repair rounds spent
    retrieval_backend = None  # H11 telemetry: which backend surfaced a candidate
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
            lint = lint_violations(stub)
            if not lint:
                try:
                    concl = split_statement(stub)[2]
                except ValueError:
                    concl = ""
                if not advised_bundle and bundle_conclusion(concl):
                    # soft: exactly one nudge round; whatever comes back is accepted
                    advised_bundle = True
                    log(f"round {i + 1}: ∧-bundle conclusion — one advisory round")
                    messages += [_assistant(content),
                                 {"role": "user", "content": _BUNDLE_ADVISORY}]
                    continue
                derivable = derivable_fn(lean_text) if derivable_fn else []
                if derivable:
                    # hard like lint (a provable hypothesis is slop by the values
                    # gate), bounded by the same rounds; the probe itself fails open
                    log(f"round {i + 1}: derivable hypothesis(es) {derivable}")
                    messages += [
                        _assistant(content),
                        {"role": "user", "content":
                         "These hypotheses are PROVABLE from the earlier binders or the "
                         "library (a bounded positivity/norm_num/simp/exact? probe closed "
                         "them): " + ", ".join(f"`{d}`" for d in derivable)
                         + ". REMOVE them — omitting a provable hypothesis is a correct "
                         "refinement, not a weakening. Keep everything else identical, "
                         "still ending `:= by sorry`."}]
                    continue
                log(f"round {i + 1}: elaborates ✓ ({tokens} tok total)")
                return {"ok": True, "stub": stub, "meta": round_meta, "lean_text": lean_text,
                        "entry": entry, "tokens": tokens, "unknowns": unknowns,
                        "advised_bundle": advised_bundle, "lint_repaired": lint_repaired,
                        "retrieval_backend": retrieval_backend}
            # elaborates but the main repo's `lake lint` would reject it (the class
            # that opened PR #123 red) — textual, so repair it here, not in review.
            log(f"round {i + 1}: elaborates but lint-dirty ({len(lint)}); first: {lint[0][:140]}")
            messages += [
                _assistant(content),
                {"role": "user", "content":
                 "The statement elaborates but the library's CI `lake lint` rejects it:\n- "
                 + "\n- ".join(lint)
                 + "\nFix ONLY these: def names lowerCamelCase (theorem names stay "
                 "snake_case); a `/-- … -/` docstring immediately above every "
                 "def/abbrev/structure. Keep the mathematics identical, still ending "
                 "`:= by sorry`."}]
            lint_repaired += 1
            continue
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
                    retrieval_backend = getattr(retrieve_fn, "backend", "?")  # H11
                    feedback += f"\n\nCandidates for `{nm}` (verify they elaborate under our pin):\n{cand}"
        messages += [_assistant(content),
                     {"role": "user", "content": feedback}]
    return {"ok": False, "stub": None, "meta": None,
            "lean_text": None, "entry": None, "tokens": tokens, "unknowns": unknowns,
            "advised_bundle": advised_bundle, "lint_repaired": lint_repaired,
            "retrieval_backend": retrieval_backend}


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
    if res.get("error"):   # H5: wedged daemon is NOT a depth verdict — retryable, never a pass
        return {"shallow": False, "indeterminate": True, "tokens": 0,
                "verdict": "indeterminate: " + str(res["error"])[:120]}
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
    if res.get("error"):   # H5: wedged daemon is NOT a triviality verdict — retryable
        return {"trivial": False, "indeterminate": True, "tokens": 0,
                "verdict": "indeterminate: " + str(res["error"])[:120]}
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

# Evidence is ARCHITECTURE-SCOPED (R: "don't optimize the current architecture
# for past architecture failures"): every attempted-record is stamped with
# ROUTING_ARCH and the routing loaders trust ONLY current-architecture records.
# Bumping this constant on an architecture change makes the pipeline run from
# zero evidence automatically, while within-version memory (don't re-buy the
# same failure every tick) keeps working. Unstamped/foreign records stay in the
# file as human-readable telemetry; they just don't steer.
ROUTING_ARCH = "routing-v1-2026-07-17"

def count_pointer_defs(main_repo: str, pointers: list[str]) -> int:
    """Consumable exports (`def`/`abbrev`/`structure`, via the shared
    `probe_lib.DEF_RE`) in the issue's pointer modules — the routing measurement.
    0 ⇒ a theorem-only stub's TYPE has nothing to consume ⇒ the definitions path.
    Missing files count 0 (fail toward defs)."""
    n = 0
    for p in pointers:
        if not p.endswith(".lean"):
            continue
        try:
            with open(os.path.join(main_repo, p), encoding="utf-8") as f:
                n += len(DEF_RE.findall(f.read()))
        except OSError:
            continue
    return n


def classify_refill(rec: dict) -> str:
    """Obstruction family for a refill `attempted` record — the drafter's triage
    analogue. Computed at write time (rides the record in refill-history.jsonl)
    and at read time for records written before the field existed. Routing
    evidence takes precedence over trailing noise: a depth rejection ANYWHERE in
    the history classifies the issue even when a later attempt died on a flaky
    intent parse (the first CI run's #66: ['depth', 'intent'])."""
    out = rec.get("outcome", "")
    if out == "seeded":
        return "seeded"
    gates = {h.get("gate") for h in rec.get("history", []) or []}
    if out in ("depth", "blocked_on_infra") or gates & {"depth", "blocked_on_infra"}:
        return "needs_primitives"
    if out in ("newdef_depth", "ungrounded") or gates & {"newdef_depth", "ungrounded"}:
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
    if out == "indeterminate" or "indeterminate" in gates:
        return "infra_indeterminate"   # H5: wedged daemon, retryable — not a real verdict
    return "infra"


def load_refill_families(history_path: str) -> dict[int, str]:
    """Issue number → family of its LATEST current-architecture refill-history
    record. Records stamped with a different (or no) ROUTING_ARCH are ignored —
    a past architecture's failures never steer this one. Tolerant of junk lines
    and of the file being absent."""
    fams: dict[int, str] = {}
    try:
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("arch") != ROUTING_ARCH:
                    continue
                if rec.get("issue") is not None:
                    # always RE-classify (the stored family is telemetry, not
                    # authority) so classifier fixes apply to existing records:
                    # the first CI run stored #66 as undraftable although its
                    # history carries depth evidence.
                    fams[int(rec["issue"])] = classify_refill(rec)
    except OSError:
        pass
    return fams


def load_prior_unknowns(history_path: str) -> dict[int, list[str]]:
    """Issue → union (first-seen order) of `unknown_identifiers` across its
    current-architecture refill-history rows — the missing declarations earlier
    drafts guessed at; they become defs-route hints ("define equivalents where
    sensible"). Foreign-architecture records are ignored, like the families."""
    out: dict[int, list[str]] = {}
    try:
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("issue") is None \
                        or rec.get("arch") != ROUTING_ARCH:
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


# families that failed for non-primitives reasons under the CURRENT architecture:
# fresh issues attempt first; these lemons go to the back of their route group
# (the first CI run burned ~100k tokens re-attempting #61's empty-reply furnace
# at position 2 while def-rich #108 sat unattempted at position 6).
_DEMOTED_FAMILIES = {"undraftable", "fidelity", "statement_wrong", "trivial_restatement"}


def order_by_route(issues: list[dict]) -> list[dict]:
    """Attempt order: fresh issues before current-arch lemons, easier difficulty
    first, then MORE pointer consumables (a def-richer context gives the drafter
    more to consume), then issue number. The ROUTE deliberately does NOT rank:
    it selects the PATH, not the priority — a needs_primitives issue carries
    positive evidence + hints (run 2 would otherwise have parked the evidenced
    defs backlog behind ~45 untested theorem issues for ~15 ticks). Stable ties."""
    def key(i: dict):
        return (1 if i.get("family") in _DEMOTED_FAMILIES else 0,
                difficulty_rank(i.get("difficulty")),
                -int(i.get("def_count", 0) or 0),
                i.get("number", 0))
    return sorted(issues, key=key)


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
    return DEF_RE.findall(stub)


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
        # grounding checks the BODY under the lambda binders: on the whole value,
        # a binder type like `(x : ℝ)` already contributes `Real`, so the identity
        # wrapper `def idw (x : ℝ) : ℝ := x` passed (caught by the 2026-07-17 live
        # 3-case validation). Peeling lambdas leaves the computational content.
        "    let mut body := v\n"
        "    while body.isLambda do\n"
        "      body := body.bindingBody!\n"
        "    let ext := body.getUsedConstants.filter (fun c => (env.getModuleIdxFor? c).isSome)\n"
        "    unless ext.size > 0 do\n"
        f'      throwError "{_DEFS_MARKER} ungrounded: {{d}} is a free-floating wrapper '
        '(its body uses no imported constant)"\n'
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
    if res.get("error"):   # H5: wedged daemon is NOT a defs verdict — retryable
        return {"failed": False, "gate": None, "indeterminate": True, "tokens": 0,
                "verdict": "indeterminate: " + str(res["error"])[:120]}
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
        if dr.get("indeterminate"):   # H5: infra, not a verdict — retryable
            return {"gate": "indeterminate", "detail": dr.get("verdict", "")}, tokens
        if dr["failed"]:
            return {"gate": dr["gate"], "detail": dr.get("verdict", "")}, tokens
    elif depth_gate:
        dep = depth_rejection(lean_text, name, issue.get("pointers", []), check_fn=check_fn)
        tokens += dep["tokens"]
        if dep.get("indeterminate"):
            return {"gate": "indeterminate", "detail": dep.get("verdict", "")}, tokens
        if dep["shallow"]:
            return {"gate": "depth", "detail": dep.get("verdict", "")}, tokens
    if triviality_gate:
        triv = triviality_rejection(lean_text, check_fn=check_fn)
        tokens += triv["tokens"]
        if triv.get("indeterminate"):
            return {"gate": "indeterminate", "detail": triv.get("verdict", "")}, tokens
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


def route_feasibility(intent: dict, pointers: list[str], *, lookup_fn) -> dict:
    """Feasibility census at intent time (H12): the `MathFin.*` primitives the intent
    NAMES but that exist neither in the pointer modules nor in the pin index.
    `lookup_fn(name) -> bool` reports existence (scout index, fallback grep). ≥1
    missing ⇒ `feasible=False` — drafting is doomed (the #1 recorded death family,
    `needs_primitives`), so record `blocked_on_infra` with the missing list and a
    suggested-defs note rather than burning a formalize budget on a hallucinated
    constant. Mathlib names are not checked (out of our authority; the elaborator
    gates those). Returns `{feasible, missing, note}`."""
    named = [o for o in (intent.get("objects") or [])
             if isinstance(o, str) and o.startswith("MathFin.")]
    missing = [o for o in named if not lookup_fn(o)]
    if not missing:
        return {"feasible": True, "missing": [], "note": ""}
    note = ("intent names MathFin primitives that do not exist yet: "
            + ", ".join(missing) + " — route to the defs stage (introduce them) or "
            "leave a human issue comment; do not draft against invented constants.")
    return {"feasible": False, "missing": missing, "note": note}


def refill(issues: list[dict], *, reason_fn, prove_fn, check_fn, context_fn,
           queue_dir: str, budget: int, max_issues: int = 1,
           max_attempt_issues: int = 3, gate_budget: int = 20_000, formalize_rounds: int = 3,
           formalize_token_budget: int = 40_000, formalize_fn=None, retrieve_fn=None,
           proactive_fn=None, depth_gate: bool = True, triviality_gate: bool = True,
           semantic_rounds: int = 2, derivable_fn=None, system_prompt=None,
           feasibility_fn=None, log=lambda m: None) -> dict:
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
        tele = {"advised_bundle": False, "lint_repaired": 0, "retrieval_backend": None}  # H11
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
                # H12: feasibility census — if the intent names MathFin primitives that
                # don't exist yet (the #1 death family), record blocked_on_infra + the
                # missing list and STOP, never burning a formalize budget on a doomed
                # draft. The defs route is exempt (it is allowed to introduce new defs).
                if feasibility_fn is not None and route != "defs":
                    feas = route_feasibility(intent, issue.get("pointers", []),
                                             lookup_fn=feasibility_fn)
                    if not feas["feasible"]:
                        row = {"attempt": attempt, "gate": "blocked_on_infra",
                               "detail": feas["note"], "missing": feas["missing"]}
                        history.append(row)
                        log(f"#{n}: blocked_on_infra — missing {feas['missing']} "
                            f"(attempt {attempt})")
                        break   # doomed target — surface for the defs route / a human

                proactive = proactive_fn(intent["statement"]) if proactive_fn else ""
                fr = formalize_with_repair(intent, ctx, issue=issue, chat_fn=formalize_fn,
                                           check_fn=check_fn, emit_fn=emit_target_files,
                                           rounds=formalize_rounds, retrieve_fn=retrieve_fn,
                                           token_budget=formalize_token_budget,
                                           proactive_premises=proactive,
                                           revision_note=feedback or "",
                                           derivable_fn=derivable_fn,
                                           log=lambda m: log(f"#{n} formalize {m}"))
                spent += fr["tokens"]
                unknowns = fr.get("unknowns") or []
                if fr.get("advised_bundle"):
                    tele["advised_bundle"] = True
                tele["lint_repaired"] += fr.get("lint_repaired", 0)
                if fr.get("retrieval_backend"):
                    tele["retrieval_backend"] = fr["retrieval_backend"]
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
                if fail["gate"] == "indeterminate":
                    # H5: a wedged daemon is not a verdict — stop re-drafting (futile
                    # against the same wedged daemon) and leave the issue UNSEEDED and
                    # retryable for the next tick, never a false seed or rejection.
                    log(f"#{n}: indeterminate (daemon infra) — deferring to next tick")
                    break
                feedback = render_gate_feedback(fail["gate"], fail["detail"], stub)
            if staged:
                outcome = "seeded"
            elif history and history[-1]["gate"] == "indeterminate":
                outcome = "indeterminate"   # retryable infra hiccup, not a real rejection
            elif history:
                outcome = history[-1]["gate"]
            else:
                outcome = "error"
            rec = {"issue": n, "attempts": attempt, "outcome": outcome, "history": history,
                   "arch": ROUTING_ARCH, "telemetry": tele}
            rec["family"] = classify_refill(rec)
            attempted.append(rec)
        except Exception as e:  # noqa: BLE001 — resilience: skip the issue, not the tick
            log(f"#{n}: error ({type(e).__name__}: {e}) — skipping")
            history.append({"attempt": len(history) + 1, "gate": "error",
                            "detail": f"{type(e).__name__}: {e}"})
            rec = {"issue": n, "attempts": len(history), "outcome": "error",
                   "history": history, "arch": ROUTING_ARCH}
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
    loogle_fn.backend = "loogle"   # H11 telemetry label
    if backend != "embedding":
        return loogle_fn, None
    premises = _embed.load_premises(index_dir)
    cache = _embed.cache_path(index_dir, embed_model)
    idx = _embed.EmbeddingIndex.load(cache, premises, embed_model) if premises else None
    if idx is None:
        return loogle_fn, None   # fails-open — no index/cache ⇒ loogle
    embed_fn = lambda texts: _embed.mistral_embed(texts, api_key=api_key, model=embed_model)  # noqa: E731
    reactive = _embed.make_embedding_retrieve_fn(idx, k, embed_fn)
    try:
        reactive.backend = "embedding"   # H11 telemetry label (skip if not settable)
    except (AttributeError, TypeError):
        pass
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
        i["def_count"] = count_pointer_defs(args.main_repo, i.get("pointers", []))
        i["family"] = families.get(i["number"])
        i["route"] = route_for(i, def_count=i["def_count"], family=i["family"])
        i["prior_unknowns"] = prior_unknowns.get(i["number"], [])
    issues = order_by_route(issues)
    print(f"[refill] routes: " + ", ".join(f"#{i['number']}→{i['route']}" for i in issues[:8]),
          file=sys.stderr)

    prove_system = build_system_prompt(args.main_repo)   # the leaf-prover gate doctrine
    set_drafter_prompt(args.main_repo)   # H1: pins + statement-design reach the drafter too

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

    # H12 feasibility census: does a named MathFin.* primitive exist? scout index
    # first (authoritative), then a grep of the main-repo MathFin/ sources. Fail-open
    # (return True) whenever neither can confidently decide, so the census only ever
    # blocks a target it is SURE names a missing primitive — never a good one.
    from scout_index import ScoutIndex
    _feas_idx = ScoutIndex(index_dir)

    def feasibility_fn(name: str) -> bool:
        if _feas_idx.available and _feas_idx.signature_of(name) is not None:
            return True
        short = re.escape(name.rsplit(".", 1)[-1])
        try:
            out = subprocess.run(
                ["grep", "-rlE", rf"(def|theorem|lemma|abbrev|structure)[[:space:]]+{short}\b",
                 os.path.join(args.main_repo, "MathFin")],
                capture_output=True, text=True, timeout=15)
            return bool(out.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            return True   # can't check ⇒ fail-open (never block a good target)

    res = refill(issues, reason_fn=reason_fn, prove_fn=prove_fn, check_fn=daemon_check,
                 context_fn=context_fn, queue_dir=queue_dir, budget=budget,
                 max_issues=max_issues, max_attempt_issues=max_attempt, gate_budget=gate_budget,
                 formalize_rounds=formalize_rounds, formalize_token_budget=formalize_token_budget,
                 formalize_fn=formalize_fn, retrieve_fn=retrieve_fn, proactive_fn=proactive_fn,
                 depth_gate=depth_gate, triviality_gate=triviality_gate,
                 semantic_rounds=semantic_rounds, system_prompt=prove_system,
                 derivable_fn=lambda lt: derivable_hypotheses(lt, check_fn=daemon_check),
                 feasibility_fn=feasibility_fn,
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


# --- strengthen: post-proof unused-hypothesis stripping ------------------------
# 2/2 production PRs shipped a hypothesis the finished proof never used (#123
# hTn, #124 hσ_eq). Unused-ness is a property of the PROOF, so it is only
# knowable after the vibe prover closes the goal (Lean suppresses the
# unusedVariables linter under `sorry`) — hence a gate-time transform, not a
# draft-time gate. Dropping an unused hypothesis can only STRENGTHEN the
# theorem, so the fidelity direction is safe by construction; the full kernel
# gate re-runs on the stripped statement before it is accepted.


def _locate_named(text: str, name: str) -> tuple[int, int, int]:
    """`_locate` spans `(bstart, sep, end)` for the SPECIFIC decl `name` — the
    proved candidate may hold vibe-added helper lemmas before the main theorem."""
    m = re.search(rf"^\s*(?:@\[[^\]]*\]\s*)?(?:theorem|lemma)\s+{re.escape(name)}(?![A-Za-z0-9_'.])",
                  text, re.MULTILINE)
    if not m:
        raise ValueError(f"declaration `{name}` not found")
    off = m.start()
    _n, bstart, sep, end = _locate(text[off:])
    return off + bstart, off + sep, off + end


def _binder_groups(binders: str) -> list[tuple[int, int, str, list[str] | None]]:
    """Top-level binder groups: `(start, end, opener, names)`, end exclusive;
    `names` is None for `{…}`/`[…]` groups (inferred binders — never stripped)."""
    groups: list[tuple[int, int, str, list[str] | None]] = []
    depth, start, opener = 0, -1, ""
    for i, c in enumerate(binders):
        if c in _OPEN:
            if depth == 0:
                start, opener = i, c
            depth += 1
        elif c in _CLOSE:
            depth -= 1
            if depth == 0 and start != -1:
                names = None
                if opener == "(":
                    group = binders[start + 1:i]
                    colon = group.find(":")
                    names = (group[:colon] if colon != -1 else group).split()
                groups.append((start, i + 1, opener, names))
                start = -1
    return groups


def remove_explicit_binders(binders: str, drop: set[str]) -> tuple[str, list[str]]:
    """Drop the named EXPLICIT `(…)` binders from a signature's binder string.
    A multi-name group `(a b : T)` loses just the named ones; a group emptied of
    its names is removed whole. Implicit/instance groups are never touched.
    Returns `(new_binders, dropped_names)`."""
    parts, dropped, last = [], [], 0
    for start, end, opener, names in _binder_groups(binders):
        parts.append(binders[last:start])
        last = end
        seg = binders[start:end]
        if opener == "(" and names:
            hit = [x for x in names if x in drop]
            if hit:
                dropped += hit
                kept = [x for x in names if x not in drop]
                if kept:
                    group = binders[start + 1:end - 1]
                    seg = "(" + " ".join(kept) + " " + group[group.find(":"):].strip() + ")"
                else:
                    seg = ""
        parts.append(seg)
    parts.append(binders[last:])
    return re.sub(r"[ \t]{2,}", " ", "".join(parts)), dropped


def unused_theorem_hypotheses(warnings, binders: str) -> list[str]:
    """Names the elaborator flagged `unused variable` that are EXPLICIT binders of
    the theorem — the strippable set (proof-internal unused vars are not statement
    surgery). `_`-prefixed names are deliberate; skipped."""
    flagged: list[str] = []
    for w in warnings or []:
        flagged += re.findall(r"[Uu]nused variable `([^`]+)`", str(w))
    explicit = set(explicit_arg_names(binders))
    seen: set[str] = set()
    out = []
    for x in flagged:
        if x in explicit and not x.startswith("_") and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _rebuild_snippet(snippet: str, candidate: str, thm_name: str) -> str | None:
    """Rebuild the re-export snippet against the stripped module theorem: same
    binders, application re-derived from them (emit's own formula). None if the
    snippet's shape is unexpected — the caller must then revert the whole strip,
    because a module/snippet signature mismatch would block open-pr regen. A
    snippet that applies a DIFFERENT theorem (the corollary shape re-exports the
    corollary while `thm_name` is the sorry-carrying core) is refused outright."""
    if f"MathFin.{thm_name}" not in snippet:
        return None
    try:
        cb, cs, _ce = _locate_named(candidate, thm_name)
        new_binders = candidate[cb:cs].strip()
        _sn, sb, ss, send = _locate(snippet)
        app = f"MathFin.{thm_name} {' '.join(explicit_arg_names(new_binders))}".rstrip()
        return (snippet[:sb] + f" {new_binders} " + snippet[ss:send + 2]
                + f"\n  {app}\n")
    except ValueError:
        return None


def bundle_conclusion(concl: str) -> bool:
    """True when the conclusion is an `∧`-bundle at the TOP level (paren-nested
    conjunctions are someone else's shape). Triggers the one-round advisory:
    prefer named per-fact lemmas/corollaries around a single proved core."""
    depth = 0
    for c in concl:
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == "∧" and depth == 0:
            return True
    return False


_BUNDLE_ADVISORY = (
    "The conclusion is an `∧`-bundle. If ONE core fact yields the parts, restate as the "
    "core theorem (`:= by sorry`) plus the issue-shaped corollary proved by applying it; "
    "if the parts are independent leaf facts, keep the bundle as the single `sorry` "
    "theorem and ADD named per-fact corollaries as projections (`(thm …).1`, "
    "`(thm …).2.1`, …) after it. Extra theorems must be sorry-free terms — exactly ONE "
    "`sorry` total, on the FIRST theorem. If the bundle truly is the honest final shape, "
    "resend it unchanged."
)


# the bounded battery: intros peels ∀/→, then certificates before search. `exact?`
# makes the probe catch lemma-shaped derivability (the zcb_pos class); the `Prop`
# ascription in the probe goal makes data binders a type error, never a false hit.
_DERIVABLE_TAC = "by intros; first | positivity | norm_num | simp | exact?"


def derivable_probe(lean_text: str) -> tuple[str, list[str], int] | None:
    """Build the one-file probe for the stub's theorem: everything before the
    theorem (imports + drafted defs, kept so hypothesis types elaborate), then one
    single-line `example` per single-name explicit binder proving its type from the
    EARLIER binders only, then `end MathFin`. Returns `(probe_text, names,
    first_example_line)`; None when there is nothing to probe."""
    m = _DECL_RE.search(lean_text)
    if not m:
        return None
    off = m.start()
    try:
        _n, bstart, sep, _end = _locate(lean_text[off:])
    except ValueError:
        return None
    binders = lean_text[off + bstart:off + sep]
    groups = _binder_groups(binders)
    lines, names = [], []
    for i, (start, end, opener, gnames) in enumerate(groups):
        if opener != "(" or not gnames or len(gnames) != 1:
            continue
        group = binders[start + 1:end - 1]
        colon = group.find(":")
        if colon == -1:
            continue
        typ = re.sub(r"\s+", " ", group[colon + 1:].strip())
        earlier = " ".join(re.sub(r"\s+", " ", binders[s:e]) for s, e, _o, _gn in groups[:i])
        head = f"example {earlier} " if earlier else "example "
        lines.append(f"set_option maxHeartbeats 50000 in {head}: (({typ}) : Prop) := {_DERIVABLE_TAC}")
        names.append(gnames[0])
    if not lines:
        return None
    prefix = lean_text[:off]
    if not prefix.endswith("\n"):
        prefix += "\n"
    base = prefix.count("\n") + 1
    return prefix + "\n".join(lines) + "\n\nend MathFin\n", names, base


def derivable_hypotheses(lean_text: str, *, check_fn) -> list[str]:
    """Single-name explicit hypotheses of the stub's theorem that the bounded
    battery PROVES from the earlier binders + the library — the #123 `hP` class
    (zcb positivity assumed although `zcb_pos` exists; the gate-time strengthen
    pass cannot see it because the finished proof USES the hypothesis). One daemon
    call. Fail-open: a daemon error, an unlocatable error, or any error OUTSIDE
    the example lines (broken context) returns [] — never blocks a good draft."""
    built = derivable_probe(lean_text)
    if built is None:
        return []
    probe, names, base = built
    try:
        r = check_fn(probe)
    except Exception:  # noqa: BLE001 — probe is advisory-shaped; never crash the draw
        return []
    if not isinstance(r, dict):
        return []
    if r.get("error"):   # H5: daemon error — fail open to [] (never flag all-names)
        return []
    example_lines = {base + j for j in range(len(names))}
    hit = set()
    for e in r.get("errors") or []:
        lns = [int(x) for x in re.findall(r"line (\d+):", str(e))]
        if not lns:
            return []
        for ln in lns:
            if ln not in example_lines:
                return []
            hit.add(ln)
    return [nm for j, nm in enumerate(names) if base + j not in hit]


_MATHFIN_IMPORT_RE = re.compile(r"^public import (MathFin\.\S+)[ \t]*\n", re.MULTILINE)


def trim_unused_imports(candidate: str, *, check_fn) -> dict:
    """Drop `public import MathFin.X` lines the proved candidate does not need.
    Emit imports EVERY issue pointer, and 'an unused import is harmless' is false
    by the coherence lens: both production PRs carried unused pointer imports,
    one adding a spurious FixedIncome→Futures edge. Subtractive and fail-open:
    each removal is kept only if the file still elaborates clean without it.
    `public import Mathlib` is never touched. Returns `{candidate, removed}`."""
    removed: list[str] = []
    for m in list(_MATHFIN_IMPORT_RE.finditer(candidate)):
        line, mod = m.group(0), m.group(1)
        trial = candidate.replace(line, "", 1)
        r = check_fn(trial)
        if r and r.get("success") and not r.get("errors") and r.get("sorry_count", 0) == 0:
            candidate = trial
            removed.append(mod)
    return {"candidate": candidate, "removed": removed}


GOLF_SYSTEM = (
    "You are polishing an ACCEPTED, kernel-checked Lean 4 proof to the library's house "
    "register. Rewrite ONLY proof bodies (what follows each `:=`): every statement, "
    "name, docstring, import and definition stays byte-identical. House idioms: the "
    "certificate over search (`mul_nonneg h₁ h₂` over `nlinarith`, `hA.ne'` over "
    "`linarith`); bare proof TERM over `by exact`; no `set … with h` whose equation is "
    "never used; fold `have h := e; simp at h; exact h` into `simpa using e`; fewer "
    "`have`s — surface the shape with `suffices`/`show`; `↦` over `=>`; never introduce "
    "`sorry` or `?`-suggestion tactics. If the proof is already minimal, return it "
    "unchanged. Output exactly one ```lean block containing the FULL file."
)

_GOLF_DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:noncomputable\s+)?(?:theorem|lemma|def|abbrev)\s",
    re.MULTILINE,
)


def _decl_signatures(text: str) -> list[str]:
    """Whitespace-normalized decl signatures (decl keyword up to the depth-0 `:=`),
    in order — the golf invariant: a polish may touch only what follows `:=`."""
    sigs = []
    n = len(text)
    for m in _GOLF_DECL_RE.finditer(text):
        depth, k = 0, m.end()
        while k < n - 1:
            c = text[k]
            if c in _OPEN:
                depth += 1
            elif c in _CLOSE:
                depth -= 1
            elif c == ":" and depth == 0 and text[k + 1] == "=":
                sigs.append(re.sub(r"\s+", " ", text[m.start():k]).strip())
                break
            k += 1
    return sigs


def golf_candidate(candidate: str, *, chat_fn, regate_fn, log=lambda m: None) -> dict:
    """One post-gate polish round: the prover golfs its own accepted proof toward
    the house register (the repo contract: a machine-found proof is refactored to
    the certificate that shows why before it merges). Accepted only if every decl
    signature is byte-equivalent (proof-only edits) AND the full gate passes again;
    any miss keeps the proved original (fail-open). Returns {candidate, golfed}."""
    try:
        content, _tk = chat_fn([{"role": "system", "content": GOLF_SYSTEM},
                                {"role": "user", "content": f"```lean\n{candidate}\n```"}])
    except Exception:  # noqa: BLE001 — polish is optional; never lose the proof
        return {"candidate": candidate, "golfed": False}
    golfed = extract_lean_code(content or "")
    if not golfed or golfed.strip() == candidate.strip() or "sorry" in golfed:
        return {"candidate": candidate, "golfed": False}
    if _decl_signatures(golfed) != _decl_signatures(candidate):
        log("golf: statement drift — rejected before re-gating")
        return {"candidate": candidate, "golfed": False}
    g = regate_fn(golfed)
    if not (isinstance(g, dict) and g.get("passed")):
        log(f"golf: re-gate failed ({(g or {}).get('reason')}); keeping the original")
        return {"candidate": candidate, "golfed": False}
    log("golf: accepted (proof-only edit, full gate green)")
    return {"candidate": golfed, "golfed": True}


def _protected_from_strip(binders: str, body: str, drop: list[str]) -> set[str]:
    """Flagged-unused binders the strengthen pass must NOT strip — pure-parse
    pre-checks that avoid a wasted re-gate round / a broken PR before the full
    re-gate backstop even runs (H8):
    - (a) sole-implicit-pin: the binder is the only use of an implicit `{B}` — its
      type mentions `B`, and `B` appears nowhere else in the binders, so dropping it
      would orphan the implicit;
    - (b) a `≠` (disequality) side-condition when the proof uses a context-pulling
      tactic (`grind`/`nlinarith`/…): the classic `A ≠ 0` field/division side-condition
      the linter reports `unused` although the tactic consumes it from context, not by
      name. (Order hypotheses `0 ≤ …` are left to the re-gate — the linter is reliable
      there, and over-protecting would keep genuinely-dead binders.)"""
    protected: set[str] = set()
    uses_ctx = bool(re.search(r"\b(?:grind|nlinarith|positivity|bound|gcongr|omega|polyrith)\b",
                              body))
    implicits: set[str] = set()
    for m in re.finditer(r"[{⦃]\s*([^:{}⦃⦄]+?)\s*:", binders):
        implicits.update(m.group(1).split())
    for name in drop:
        bm = re.search(r"\(\s*" + re.escape(name) + r"\b[^:()]*:\s*[^()]*\)", binders)
        if not bm:
            continue
        btext = bm.group(0)
        typ = btext.split(":", 1)[1] if ":" in btext else ""
        if uses_ctx and "≠" in typ:
            protected.add(name)
            continue
        for iv in implicits:
            iv_re = r"(?<![\w'])" + re.escape(iv) + r"(?![\w'])"
            # occurrences outside this binder; ≤1 means only the implicit's own
            # `{iv : …}` declaration remains → stripping orphans it.
            if re.search(iv_re, typ) and \
                    len(re.findall(iv_re, binders)) - len(re.findall(iv_re, btext)) <= 1:
                protected.add(name)
                break
    return protected


def strengthen_candidate(candidate: str, snippet: str | None, thm_name: str,
                         warnings, *, regate_fn, max_passes: int = 3,
                         log=lambda m: None) -> dict:
    """Drop theorem hypotheses the finished proof never used and re-gate. Keeps
    the stronger statement only if the FULL gate passes again; any failure —
    re-gate red, unlocatable decl, unrebuildable snippet — reverts to the
    original (fail-open: never lose a good proof to the optimizer). Returns
    `{candidate, entry_code, stripped}`; `entry_code` is the rebuilt re-export
    (None when nothing was stripped or no snippet was supplied)."""
    original = candidate
    stripped: list[str] = []
    for _ in range(max_passes):
        try:
            bstart, sep, _end = _locate_named(candidate, thm_name)
        except ValueError:
            break
        binders = candidate[bstart:sep]
        drop = unused_theorem_hypotheses(warnings, binders)
        prot = _protected_from_strip(binders, candidate[sep:], drop)  # H8 pure-parse guard
        drop = [d for d in drop if d not in prot]
        if not drop:
            break
        new_binders, dropped = remove_explicit_binders(binders, set(drop))
        if not dropped:
            break
        cand2 = candidate[:bstart] + new_binders + candidate[sep:]
        g2 = regate_fn(cand2)
        if not g2.get("passed"):
            log(f"strengthen: dropping {dropped} failed the re-gate "
                f"({g2.get('reason')}); keeping the proved original")
            break
        log(f"strengthen: dropped unused hypothesis(es) {dropped}")
        candidate = cand2
        warnings = g2.get("warnings") or []
        stripped += dropped
    if not stripped:
        return {"candidate": original, "entry_code": None, "stripped": []}
    entry_code = None
    if snippet is not None:
        entry_code = _rebuild_snippet(snippet, candidate, thm_name)
        if entry_code is None:
            log("strengthen: snippet rebuild failed; reverting to the original statement")
            return {"candidate": original, "entry_code": None, "stripped": []}
    return {"candidate": candidate, "entry_code": entry_code, "stripped": stripped}


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


# A5: a `/-- … -/` decl docstring immediately followed by an `omit …/set_option …
# in` modifier is a parse error (`unexpected token 'omit'`) — the modifier must sit
# ABOVE the docstring. This regex captures (docstring)(modifier-lines) to swap them.
_MODIFIER_AFTER_DOC_RE = re.compile(
    r"(/--.*?-/)\n((?:[ \t]*(?:omit|set_option)\b[^\n]*?\bin\b[ \t]*\n)+)", re.DOTALL)
# A7: a capital `Σ`/`Π` glued into an identifier collides with sigma/pi-type
# notation (a recurring drafter slip — girsanov era). Match one adjacent to an
# ASCII identifier char so the standalone `Σ x, …` type-former is NOT flagged.
_SIGMA_PI_IDENT_RE = re.compile(r"[A-Za-z0-9_'][ΣΠ]|[ΣΠ][A-Za-z0-9_']")


def _prelint_stub(stub: str) -> str:
    """Emit-time deterministic fixes on a drafted stub, before assembly:
    - A13: STRIP any model-emitted `import` line. Leanstral (RL-trained on complete
      files) prepends `import Mathlib`; but `emit_target_files` inserts the stub AFTER
      the module header's own imports, so a stub import lands mid-file and the module
      system rejects it (`invalid 'import' command, it must be used in the beginning of
      the file` — recurred live on #109/#60, 2026-07-19). The header already imports
      Mathlib + the pointer modules, so the stub never needs one. Deterministic beats the
      soft repair hint the model kept ignoring.
    - A5: move an `omit …/set_option … in` modifier ABOVE an immediately preceding
      decl docstring (else `unexpected token 'omit'; expected 'lemma'`).
    - A7: reject a capital `Σ`/`Π` inside an identifier (sigma/pi-type collision);
      raised so `formalize_with_repair`'s try/except surfaces it to the model."""
    stub = re.sub(r"(?m)^[ \t]*(?:public[ \t]+)?import[ \t]+\S.*\n?", "", stub)
    # A14: rewrite an autobound universe variable (`Type u`, `Sort u_1`, `Type v`) to the
    # Mathlib idiom `Type*`/`Sort*`. emit pins `autoImplicit false` for build-parity, so an
    # explicit `u` is unbound → "unknown universe level u" (recurred live on #109/#60). Only
    # u/v/w-prefixed vars (Lean's autobound naming) are touched; numeric levels are left alone.
    stub = re.sub(r"\b(Type|Sort)[ \t]+([uvw][A-Za-z0-9_']*)\b", r"\1*", stub)
    bad = _SIGMA_PI_IDENT_RE.search(stub)
    if bad:
        raise ValueError(
            f"identifier uses `{bad.group()}` — a capital Σ/Π collides with sigma/pi-type "
            "notation; rename it with an ASCII/lowercase identifier (e.g. `sigma`).")
    return _MODIFIER_AFTER_DOC_RE.sub(lambda m: m.group(2) + m.group(1) + "\n", stub)


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
    stub = _prelint_stub(stub)   # A5 modifier-order fix + A7 Σ/Π-identifier reject
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
    # 'public import MathFin.FixedIncome.ZCB'). An unused pointer import is NOT
    # harmless (it adds a spurious cross-module edge) — trim_unused_imports drops any
    # the proved candidate does not need, post-proof.
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
        # lake-parity: the lakefile sets autoImplicit false, but the DAEMON that
        # gates drafts elaborates with Lean's default (true) — a drafted
        # `{Ω : Type u}` auto-binds `u`, passes every gate, then fails the
        # open-pr regen build with `unknown universe level` (run-4 PR blocker).
        # Pinning the option in the stub makes draft-time elaboration enforce
        # exactly what the build enforces, so the compile-repair loop fixes it.
        "set_option autoImplicit false\n\n"
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
