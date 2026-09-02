"""af_gates — extracted from autoformalize.py; see it for the pipeline overview."""
from __future__ import annotations

from probe_lib import DEF_RE, append_jsonl, extract_lean_code, lint_violations
import re

from af_parse import *  # noqa: F401,F403
from domain_pack import DomainPack

__all__ = ['_probed_conclusion', '_probed_signature', '_DEPTH_MARKER', '_mod_name', 'depth_probe', 'depth_rejection', '_TRIV_TACTIC', '_SORRY_RE', 'triviality_goal', 'triviality_rejection', '_DEFS_MARKER', 'drafted_def_names', 'defs_probe', 'defs_rejection', '_INSTANCE_PROBE_TACTIC_RE', '_example_blocks', '_example_probes_def', 'instance_probe_rejection']



def _probed_conclusion(text: str, name: str) -> str | None:
    """The whitespace-normalized conclusion of the theorem/lemma named `name` in
    `text`, or None if it is absent or unparseable. Used to verify an adversarial
    gate proof kept the PROBED conclusion (`False` / `¬…`) rather than reverting to
    the provable original."""
    m = re.search(
        rf"(?m)^\s*(?:@\[[^\]]*\]\s*)?(?:theorem|lemma)\s+{re.escape(name)}(?![\w'.])",
        text)
    if not m:
        return None
    sub = text[m.start():]
    try:
        _n, _b, sep, end = _locate(sub)
    except ValueError:
        return None
    return " ".join(sub[sep + 1:end].split())




def _probed_signature(text: str, name: str) -> str | None:
    """The whitespace-normalized SIGNATURE — binders through conclusion — of the
    theorem/lemma named `name` in `text`, or None if it is absent or unparseable.
    The statement-integrity pin (item J): the accepted proof must still assert THIS,
    so a prover that (against instruction) weakened a binder or the conclusion to
    something trivially provable is caught. Broader than `_probed_conclusion`, which
    guards only the vacuity/disproof probes' conclusion swap."""
    m = re.search(
        rf"(?m)^\s*(?:@\[[^\]]*\]\s*)?(?:theorem|lemma)\s+{re.escape(name)}(?![\w'.])",
        text)
    if not m:
        return None
    sub = text[m.start():]
    try:
        _n, bstart, _sep, end = _locate(sub)
    except ValueError:
        return None
    return " ".join(sub[bstart:end].split())




# --- pointers-scoped depth gate (option B) -----------------------------------
#
# The kernel gates catch a FALSE or vacuous statement; they do NOT catch a
# TRUE-but-shallow one — a Mathlib identity in domain clothing (cal-bk-53 reduced to
# `integral_add_compl`; cal-bk-67 inlined the forward-rate formula as `let`s over raw
# reals instead of consuming the library's own def). The depth gate is a structural,
# ELABORATOR-grounded check (not an LLM judge, per the rigorous-vs-soft rule): elaborate
# the stub, then a `run_cmd` meta block inspects the theorem's TYPE and requires it to
# USE at least one constant DEFINED in one of the issue's `-- pointers:` library modules.
# If none, the meta throwErrors — surfacing as a daemon error the gate keys on by its
# `depth-gate:` marker. With no pointers there is nothing to scope to, so it falls back
# to requiring any own-namespace constant (namespace fallback).

_DEPTH_MARKER = "depth-gate:"




def _mod_name(pack: DomainPack, pointer: str) -> str:
    """`<LakeRoot>/FixedIncome/ZCB.lean` -> the Lean module name
    `<LakeRoot>.FixedIncome.ZCB`. Delegates to the pack so the emitter and the gate
    cannot disagree about how a pointer path becomes a module name."""
    return pack.module_of(pointer)




def depth_probe(pack: DomainPack, lean_text: str, name: str, pointers: list[str]) -> str:
    """The stub + a `run_cmd` meta block that FAILS elaboration unless the theorem's
    TYPE uses a constant DEFINED in one of its pointer modules (pointers-scoped).
    `name` is the decl name (under the pack's namespace); `pointers` are repo-relative
    `<LakeRoot>/…/X.lean` paths (assumed non-empty — `depth_rejection` skips otherwise)."""
    mods = [_mod_name(pack, p) for p in pointers if p.endswith(".lean")]
    ptr_list = ", ".join(f"`{m}" for m in mods)
    meta = (
        "\nopen Lean in\n"
        "run_cmd do\n"
        "  let env ← getEnv\n"
        f"  let some ci := env.find? `{pack.qualified(name)}\n"
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




def depth_rejection(pack: DomainPack, lean_text: str, name: str, pointers: list[str],
                    *, check_fn) -> dict:
    """Elaborate the depth probe via `check_fn` (the daemon). `shallow=True` iff the meta
    block reported a `depth-gate:` error (the type consumes no pointer-module def). With
    NO pointers the gate is inapplicable — it SKIPS (a missing Pointers section is a
    metadata gap, not a shallowness verdict; the stub carries no library import to consume
    anyway). Fails OPEN too: a daemon-communication error is NOT a depth verdict, so an
    infra hiccup never rejects a good target (like the prover gates). No prover call ⇒
    `tokens=0`."""
    mods = [p for p in pointers if p.endswith(".lean")]
    if not mods:
        return {"shallow": False, "tokens": 0, "verdict": "no pointers — depth gate skipped"}
    res = check_fn(depth_probe(pack, lean_text, name, mods))
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




def defs_probe(pack: DomainPack, lean_text: str, thm_name: str,
               def_names: list[str]) -> str:
    """The stub + a `run_cmd` meta block that FAILS elaboration unless (a) the
    theorem's type uses ≥1 drafted def (newdef_depth) and (b) every drafted def's
    value uses ≥1 imported constant (ungrounded)."""
    names = ", ".join(f"`{pack.qualified(d)}" for d in def_names)
    meta = (
        "\nopen Lean in\n"
        "run_cmd do\n"
        "  let env ← getEnv\n"
        f"  let some thm := env.find? `{pack.qualified(thm_name)}\n"
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




def defs_rejection(pack: DomainPack, lean_text: str, thm_name: str,
                   def_names: list[str], *, check_fn) -> dict:
    """Elaborate the defs probe via `check_fn` (the daemon). Returns
    `{failed, gate: "newdef_depth"|"ungrounded"|None, verdict, tokens}`. No defs
    drafted ⇒ instant `newdef_depth` fail (no daemon call — the route's contract
    was ignored). Fails OPEN on unmarked errors, like the other structural gates."""
    if not def_names:
        return {"failed": True, "gate": "newdef_depth", "tokens": 0,
                "verdict": "no definitions drafted — the defs route requires 1-3 new defs"}
    res = check_fn(defs_probe(pack, lean_text, thm_name, def_names))
    if res.get("error"):   # H5: wedged daemon is NOT a defs verdict — retryable
        return {"failed": False, "gate": None, "indeterminate": True, "tokens": 0,
                "verdict": "indeterminate: " + str(res["error"])[:120]}
    errs = [str(e) for e in (res.get("errors") or []) if _DEFS_MARKER in str(e)]
    if not errs:
        return {"failed": False, "gate": None, "verdict": "", "tokens": 0}
    gate = "ungrounded" if "ungrounded" in errs[0] else "newdef_depth"
    return {"failed": True, "gate": gate, "verdict": errs[0], "tokens": 0}




# item M — the instance-probe gate. `newdef_depth`/`ungrounded` prove a def is
# USED and GROUNDED, but not that it COMPUTES the intended quantity: a sign flip,
# a normalization slip, or sup-vs-max reads as a plausible def the faithfulness
# judge and the vacuity/disproof probes all pass (the #73 maxDD `∀c` class). The
# fix, transplanted from Nexus's OEIS anti-misformalization guard: every new def
# must ship ≥1 concrete-instance `example` evaluating it on explicit small inputs
# against its intended value, proved by a norm_num/decide-class tactic. Those
# examples elaborate as ordinary stub content, so a slipped def makes its own
# intended-value example FAIL to elaborate — caught before the prove stage. This
# gate enforces the examples are PRESENT and real (not skipped, not faked, not a
# `x = x` tautology); the daemon check the battery already ran does the catching.
_INSTANCE_PROBE_TACTIC_RE = re.compile(r"\b(norm_num1?|decide|simp)\b")




def _example_blocks(lean_text: str) -> list[str]:
    """Each top-level `example … := …` block in the stub, up to the next decl."""
    return re.findall(
        r"(?ms)^[ \t]*example\b.*?"
        r"(?=^[ \t]*(?:example|theorem|lemma|def|noncomputable|abbrev|structure|end|/-|@\[)\b|\Z)",
        lean_text)




def _example_probes_def(block: str, def_name: str) -> bool:
    """True iff `block` is a CONCRETE-VALUE probe of `def_name`: an equation whose
    LHS applies the def and whose RHS is a concrete value (a numeral, NOT the def
    again), closed by a norm_num/decide/simp-class tactic (never sorry/admit)."""
    if ":=" not in block:
        return False
    stmt, proof = block.split(":=", 1)            # the first := ends the type
    if def_name not in stmt or "=" not in stmt:
        return False
    if re.search(r"\b(sorry|admit)\b", proof) or not _INSTANCE_PROBE_TACTIC_RE.search(proof):
        return False
    rhs = stmt.rsplit("=", 1)[1]                   # the asserted value
    return def_name not in rhs and re.search(r"\d", rhs) is not None




def instance_probe_rejection(lean_text: str, def_names: list[str]) -> dict:
    """Item M gate: reject unless every drafted def has ≥1 concrete-value `example`
    probing it. Pure-parse (presence + shape); the elaboration that turns a slipped
    def into a hard error was already run by the battery's daemon check. Returns
    `{failed, gate: "instance_probe"|None, verdict, tokens: 0}`."""
    if not def_names:                              # theorem route — nothing to probe
        return {"failed": False, "gate": None, "verdict": "", "tokens": 0}
    blocks = _example_blocks(lean_text)
    missing = [d for d in def_names
               if not any(_example_probes_def(b, d) for b in blocks)]
    if missing:
        return {"failed": True, "gate": "instance_probe", "tokens": 0,
                "verdict": ("no concrete-instance example for " + ", ".join(missing)
                            + " — add `example : <def> <small inputs> = <value> := by norm_num`")}
    return {"failed": False, "gate": None, "verdict": "", "tokens": 0}
