"""af_gates — extracted from autoformalize.py; see it for the pipeline overview."""
from __future__ import annotations

from probe_lib import DEF_RE, append_jsonl, extract_lean_code, lint_violations
import re

from af_parse import *  # noqa: F401,F403
from domain_pack import DomainPack

__all__ = ['_probed_conclusion', '_probed_signature', '_DEPTH_MARKER', '_mod_name', 'depth_probe', 'depth_rejection', '_MAP_MARKER', 'map_probe', 'map_rejection', '_TRIV_TACTIC', '_SORRY_RE', 'triviality_goal', 'triviality_rejection', '_DEFS_MARKER', 'drafted_def_names', 'defs_probe', 'defs_rejection', '_INSTANCE_PROBE_TACTIC_RE', '_example_blocks', '_example_probes_def', 'instance_probe_rejection', '_TRUNK_MARKER', 'trunk_rejection', '_SHAPE_MARKER', 'shape_probe', 'shape_text_problems', 'shape_rejection', 'LONG_FILE_LINES']



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




# --- map gate (the corpus must rest on the program map) -------------------------
#
# The depth gate asks whether the type consumes a def from the issue's POINTER modules.
# A library with a blueprint asks more of a new result: its corpus entry must rest on a
# node of the program map — its statement or proof must depend, through the library's
# own declarations, on a declaration the map tags — and the library's build holds the
# count of entries that do not at a ratchet. A theorem over `zcb` alone passes the depth
# gate (it consumes a pointer def) and fails that ratchet, AFTER a prove session and the
# library's whole build have been paid for it.
#
# So the same question is asked here, of the STATEMENT, where the answer is cheap: walk
# the constants of the theorem's type, through the types and bodies of the library's own
# declarations, and require one of the map's tagged names to be reached. It is the
# sufficient half of the library's condition (a proof could still reach the map through
# a lemma the statement does not mention), and deliberately so: a statement that names no
# program object is the thing the library's "one graph" rule exists to refuse.

_MAP_MARKER = "map-gate:"
_TRUNK_MARKER = "trunk-gate:"


def map_probe(pack: DomainPack, lean_text: str, name: str, anchors: list[str],
              trunk: dict | None = None, *, through_proof: bool = False) -> str:
    """The stub + a `run_cmd` meta block that FAILS elaboration unless the theorem's TYPE
    reaches one of `anchors` (the fully-qualified names the program map tags), walking
    through the types and bodies of declarations in the library's own modules and of the
    stub's own new definitions. A theorem met on the way counts through its proof, as it
    does in the library's own index and in `af_routing.map_census`, so the census and the
    gate agree on what rests on the map. Only the library's OWN declarations are walked and
    only its own anchors count: the library's index records dependence on its own
    constants alone, so a statement that touches the map only at an upstream node (a
    Mathlib or BrownianMotion declaration the blueprint also draws) is off the map by the
    library's count, and its build would refuse the entry after the proof was paid for.

    With `trunk` (`library_map.price_table`: the library's pricing functional and the
    definitions that claim to be prices) the same walk also fails, under its own marker,
    when the statement reaches a price but not the functional. That is the library's
    `price_entries_off_trunk` rule read on the statement: a price founded on the functional
    carries the statement to it, one not yet re-founded does not (cal-bk-318: `SVI.bsSmile`
    is defined through `bsV`; the proof passed and its entry was refused at assembly).

    `through_proof` starts the walk from the theorem's proof as well as its type: the
    library counts an entry by what its proof cites, so a proof can reach an unfounded
    price its statement never names (cal-bk-318's redraft, through `SVI.blackCall`). With
    no `anchors` the map half is off and only the trunk is checked."""
    names = ", ".join(f"`{a}" for a in anchors)
    trunk = trunk or {}
    prices = ", ".join(f"`{a}" for a in trunk.get("prices") or [])
    functional = ", ".join(f"`{a}" for a in trunk.get("trunk") or [])
    meta = (
        "\nopen Lean in\n"
        "run_cmd do\n"
        "  let env ← getEnv\n"
        f"  let some ci := env.find? `{pack.qualified(name)}\n"
        f'    | throwError "{_MAP_MARKER} declaration {name} not found"\n'
        "  let mods := env.header.moduleNames\n"
        f"  let anchors : NameSet := ([{names}] : List Name).foldl (·.insert ·) {{}}\n"
        f"  let prices : NameSet := ([{prices}] : List Name).foldl (·.insert ·) {{}}\n"
        f"  let functional : NameSet := ([{functional}] : List Name).foldl (·.insert ·) {{}}\n"
        "  let own (c : Name) : Bool :=\n"
        "    match env.getModuleIdxFor? c with\n"
        f"    | some i => mods[i.toNat]!.getRoot == `{pack.lake_root}\n"
        "    | none => true\n"
        "  let mut seen : NameSet := {}\n"
        "  let mut todo : Array Name := ci.type.getUsedConstants\n"
        + ("  todo := todo ++ ((ci.value? true).map (·.getUsedConstants)).getD #[]\n"
           if through_proof else "")
        + "  let mut hit := false\n"
        "  let mut price : Option Name := none\n"
        "  let mut onTrunk := false\n"
        "  while !todo.isEmpty do\n"
        "    let c := todo.back!\n"
        "    todo := todo.pop\n"
        "    if seen.contains c then continue\n"
        "    seen := seen.insert c\n"
        "    if !own c then continue\n"
        "    if anchors.contains c then hit := true\n"
        "    if functional.contains c then onTrunk := true\n"
        "    if prices.contains c && price.isNone then price := some c\n"
        "    if let some d := env.find? c then\n"
        "      todo := todo ++ d.type.getUsedConstants\n"
        "      match d with\n"
        "      | .defnInfo v => todo := todo ++ v.value.getUsedConstants\n"
        "      | .thmInfo v => todo := todo ++ v.value.getUsedConstants\n"
        "      | .opaqueInfo v => todo := todo ++ v.value.getUsedConstants\n"
        "      | .inductInfo v => todo := todo ++ v.ctors.toArray\n"
        "      | _ => pure ()\n"
        + ("  unless hit do\n"
           f'    throwError "{_MAP_MARKER} the statement reaches no declaration of the program '
           'map"\n' if anchors else "")
        + "  if let some p := price then\n"
        "    unless onTrunk do\n"
        f'      throwError m!"{_TRUNK_MARKER} the statement reaches the price {{p}}, which the '
        'library has not yet founded on its pricing functional"\n'
    )
    return lean_text.rstrip() + "\n" + meta


_SHAPE_MARKER = "shape-gate:"
_HEARTBEATS = re.compile(r"\bset_option\s+maxHeartbeats\b")
#: Mathlib's `linter.style.longFile` default, the library's `long_files` ratchet
LONG_FILE_LINES = 1500


def shape_probe(pack: DomainPack, lean_text: str) -> str:
    """The module + a `run_cmd` block that FAILS, under `shape-gate:`, when a theorem the
    module declares is one the library's declaration index would count as debt:

    * a DUPLICATE: its statement's `Expr` hash equals a library theorem's, or another of
      the module's own (`duplicate_statements`, baseline 0; cal-bk-318's decompose leaf
      `Phi_tendsto_zero_atBot := tendsto_Phi_atBot`);
    * a MATHLIB-SHAPED PRIVATE: private, and its statement mentions no constant of the
      library or of the module itself (`private_mathlib_shaped`, baseline 0).

    The library computes both in its own Lean index tool over the declarations a
    reader would name; this walks the same set (no auto-generated declaration, no
    constructor or recursor) with the same hash, so the two agree."""
    root = pack.lake_root
    meta = (
        "\nopen Lean in\n"
        "run_cmd do\n"
        "  let env ← getEnv\n"
        "  let mods := env.header.moduleNames\n"
        "  let inLib (c : Name) : Bool :=\n"
        "    match env.getModuleIdxFor? c with\n"
        f"    | some i => mods[i.toNat]!.getRoot == `{root}\n"
        "    | none => true\n"
        "  let lib : Std.HashMap UInt64 Name := env.constants.map₁.fold (init := {}) fun acc n ci =>\n"
        "    if (ci matches .thmInfo _) && inLib n && !env.isAutoDecl (privateToUserName n) then\n"
        "      acc.insert ci.type.hash n else acc\n"
        "  let mut seen : Std.HashMap UInt64 Name := {}\n"
        "  let mut bad : Array String := #[]\n"
        "  for (n, ci) in env.constants.map₂.toList do\n"
        "    unless (ci matches .thmInfo _) do continue\n"
        "    if env.isAutoDecl (privateToUserName n) then continue\n"
        "    let h := ci.type.hash\n"
        "    if let some m := lib[h]? then\n"
        "      bad := bad.push s!\"{privateToUserName n} states what {m} states\"\n"
        "    else if let some m := seen[h]? then\n"
        "      bad := bad.push s!\"{privateToUserName n} states what {privateToUserName m} states\"\n"
        "    seen := seen.insert h n\n"
        "    if isPrivateName n && !(ci.type.getUsedConstants.any inLib) then\n"
        "      bad := bad.push s!\"private {privateToUserName n} mentions nothing of the library: \" ++\n"
        "        \"use the upstream lemma, inline it, or make it public under a name that says what it proves\"\n"
        "  unless bad.isEmpty do\n"
        "    let msg := String.intercalate \"; \" bad.toList\n"
        f'    throwError m!"{_SHAPE_MARKER} {{msg}}"\n'
    )
    return lean_text.rstrip() + "\n" + meta


def shape_text_problems(lean_text: str) -> list[str]:
    """The library's source counts a module can raise by its text alone."""
    code = re.sub(r"/-.*?-/", "", lean_text, flags=re.S)
    code = re.sub(r"--[^\n]*", "", code)
    out = []
    if _HEARTBEATS.search(code):
        out.append("`set_option maxHeartbeats` (the library's `max_heartbeats` count may not "
                   "rise): find a proof that elaborates in the default budget")
    if lean_text.count("\n") + 1 > LONG_FILE_LINES:
        out.append(f"the module is over {LONG_FILE_LINES} lines (`long_files`)")
    return out


def shape_rejection(pack: DomainPack, lean_text: str, *, check_fn) -> dict:
    """`{refused, indeterminate?, verdict}`: would the library's index or source counts
    rise because of what this module declares? Fails open on a daemon error."""
    text = shape_text_problems(lean_text)
    if text:
        return {"refused": True, "verdict": "; ".join(text)}
    res = check_fn(shape_probe(pack, lean_text))
    if res.get("error"):
        return {"refused": False, "indeterminate": True,
                "verdict": "indeterminate: " + str(res["error"])[:120]}
    errs = [str(e) for e in (res.get("errors") or []) if _SHAPE_MARKER in str(e)]
    return {"refused": bool(errs), "verdict": "; ".join(errs[:2])[:600]}


def trunk_rejection(pack: DomainPack, lean_text: str, name: str, trunk: dict | None,
                    *, check_fn) -> dict:
    """After a proof passes: does the theorem, statement AND proof, reach a price the
    library has not founded on its pricing functional, without reaching the functional?
    `{off_trunk, indeterminate?, verdict}`; skips with no table, fails open on a daemon
    error. The library's ratchet would refuse such an entry at assembly, after the build."""
    if not trunk:
        return {"off_trunk": False, "verdict": "no price table — trunk check skipped"}
    res = check_fn(map_probe(pack, lean_text, name, [], trunk, through_proof=True))
    if res.get("error"):
        return {"off_trunk": False, "indeterminate": True,
                "verdict": "indeterminate: " + str(res["error"])[:120]}
    errs = [str(e) for e in (res.get("errors") or []) if _TRUNK_MARKER in str(e)]
    return {"off_trunk": bool(errs), "verdict": "; ".join(errs[:2])}


def map_rejection(pack: DomainPack, lean_text: str, name: str, anchors: list[str],
                  *, check_fn, trunk: dict | None = None) -> dict:
    """Elaborate the map probe. `off_map=True` iff the meta block reported a `map-gate:`
    error, `off_trunk=True` iff it reported only a `trunk-gate:` one. With no anchors (a
    library with no blueprint, or an issue prepared without a checkout) the gate is
    inapplicable and SKIPS. Fails open on a daemon error, like the depth gate: an infra
    hiccup is never a verdict. No prover call ⇒ `tokens=0`."""
    if not anchors:
        return {"off_map": False, "off_trunk": False, "tokens": 0,
                "verdict": "no program map — map gate skipped"}
    res = check_fn(map_probe(pack, lean_text, name, anchors, trunk))
    if res.get("error"):
        return {"off_map": False, "off_trunk": False, "indeterminate": True, "tokens": 0,
                "verdict": "indeterminate: " + str(res["error"])[:120]}
    errs = [str(e) for e in (res.get("errors") or [])]
    off_map = [e for e in errs if _MAP_MARKER in e]
    off_trunk = [e for e in errs if _TRUNK_MARKER in e]
    return {"off_map": bool(off_map), "off_trunk": bool(off_trunk) and not off_map,
            "tokens": 0, "verdict": "; ".join((off_map or off_trunk)[:2])}




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
