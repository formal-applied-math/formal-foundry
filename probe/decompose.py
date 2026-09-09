"""Lemma-DAG decomposition (Phase 2 of the autoformalization upgrade plan).

A hard target the plain draft->prove path can't close is split by Claude into a DAG
of named leaf lemmas plus a main theorem that applies them. This module is the
SCHEMA layer: it validates that DAG (shape, size, cycles, dangling deps) BEFORE any
prover budget is spent — the first of the Lean-side gates that keep a hard split
honest before any leaf gets proving budget.

Design of record: docs/superpowers/specs/2026-07-18-decomposer-design.md.
Stdlib only; no Lean, no API, no network.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field

from domain_pack import DomainPack

# Default leaf ceiling (R decision 2026-07-18: tight splits first). The pipeline.toml
# `[decompose] max_leaves` override is threaded in by the tick wiring (Task 2.4).
MAX_LEAVES = 3


class DagError(ValueError):
    """A malformed or invalid lemma-DAG: bad shape, cycle, oversize, dangling dep,
    or name collision. Surfaced to the decomposer as a re-decompose signal."""


@dataclass
class Node:
    name: str
    statement: str
    pointers: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    applied_to: list[str] = field(default_factory=list)  # library/Mathlib lemmas the leaf's
                                                          # proof consumes — a prover hint
    is_main: bool = False
    proof: str = ""   # the main node's Lean proof applying the leaves (leaves: "")


@dataclass
class Dag:
    main: Node
    leaves: list[Node]
    #: the target stub's own `def`s/`abbrev`s and its imports. Every module the decompose
    #: path assembles — the skeleton, each leaf stub, the recomposed candidate — needs
    #: them, because a target INTRODUCES definitions its leaf statements then refer to.
    #: Carried on the Dag rather than passed per call because draft and recompose are
    #: SEPARATE processes: this survives the dag.json roundtrip between them.
    preamble: str = ""
    target_pointers: list[str] = field(default_factory=list)

    @property
    def nodes(self) -> list[Node]:
        """All nodes, leaves before the main node."""
        return [*self.leaves, self.main]


def parse_dag(spec, *, max_leaves: int | None = None) -> Dag:
    """Validate a decomposer's lemma-DAG (dict or JSON string) into a `Dag`. Raises
    `DagError` on any defect. The main node implicitly depends on every leaf; leaves
    may depend only on other leaves. A cycle is caught by the `topo_order` at the end."""
    limit = MAX_LEAVES if max_leaves is None else max_leaves
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError as e:
            raise DagError(f"dag spec is not JSON: {e}") from e
    if not isinstance(spec, dict):
        raise DagError("dag spec must be a JSON object")

    m = spec.get("main")
    if not isinstance(m, dict) or not m.get("name") or not m.get("statement"):
        raise DagError("main node must be an object with a name + statement")
    leaves_spec = spec.get("leaves")
    if not isinstance(leaves_spec, list) or not leaves_spec:
        raise DagError("dag must carry a non-empty `leaves` list")
    if len(leaves_spec) > limit:
        raise DagError(f"too many leaves ({len(leaves_spec)} > MAX_LEAVES={limit})")

    leaves: list[Node] = []
    seen: set[str] = set()
    for leaf in leaves_spec:
        if not isinstance(leaf, dict) or not leaf.get("name") or not leaf.get("statement"):
            raise DagError("each leaf must be an object with a name + statement")
        name = leaf["name"]
        if name in seen:
            raise DagError(f"duplicate leaf name: {name}")
        seen.add(name)
        applied = leaf.get("applied_to") or []
        if not isinstance(applied, list) or not all(isinstance(a, str) for a in applied):
            raise DagError(f"leaf {name} `applied_to` must be a list of strings")
        leaves.append(Node(name=name, statement=leaf["statement"],
                           pointers=list(leaf.get("pointers") or []),
                           depends_on=list(leaf.get("depends_on") or []),
                           applied_to=applied))
    if m["name"] in seen:
        raise DagError(f"main name {m['name']} collides with a leaf name")

    for leaf in leaves:
        for dep in leaf.depends_on:
            if dep == leaf.name:
                raise DagError(f"leaf {leaf.name} depends on itself")
            if dep not in seen:
                raise DagError(f"leaf {leaf.name} depends on unknown leaf {dep}")

    main = Node(name=m["name"], statement=m["statement"], is_main=True,
                proof=m.get("proof", ""),
                depends_on=[leaf.name for leaf in leaves])
    # `target` is written by `dag_to_dict` from the target FILE, never by the model —
    # it is how the draft process hands the stub's context to the recompose process.
    tgt = spec.get("target") if isinstance(spec.get("target"), dict) else {}
    dag = Dag(main=main, leaves=leaves,
              preamble=str(tgt.get("preamble") or ""),
              target_pointers=list(tgt.get("pointers") or []))
    topo_order(dag)   # raises DagError on a cycle
    _check_leaf_reachability(main, leaves)   # raises DagError on a dead (orphan) leaf
    return dag


def _check_leaf_reachability(main: Node, leaves: list[Node]) -> None:
    """Reject a leaf the main proof never dispatches to — directly (its name appears in the
    proof) or transitively (a reachable leaf `depends_on` it). Such a leaf is dead weight
    that would burn prover budget for nothing. Only meaningful once the main carries a REAL
    proof; a sketch/empty proof (the schema-validation shape) skips the check. Substring
    matching is word-bounded so `bar_neg` is not seen inside `bar_negative`; over-counting a
    reference can only UNDER-reject, never falsely reject a good DAG."""
    if not main.proof.strip():
        return
    by_name = {leaf.name: leaf for leaf in leaves}
    reachable: set[str] = set()
    frontier = [leaf.name for leaf in leaves
                if re.search(rf"\b{re.escape(leaf.name)}\b", main.proof)]
    while frontier:
        name = frontier.pop()
        if name in reachable:
            continue
        reachable.add(name)
        frontier.extend(by_name[name].depends_on)
    orphans = [leaf.name for leaf in leaves if leaf.name not in reachable]
    if orphans:
        raise DagError(f"leaf(s) never dispatched to by the main proof: {', '.join(orphans)} "
                       "(dead weight — reference them in the proof or drop them)")


def topo_order(dag: Dag) -> list[Node]:
    """Nodes in dependency order (dependencies first, `main` last). Raises `DagError`
    on a cycle. This ordering is how leaves get proved before the recomposition."""
    by_name = {n.name: n for n in dag.nodes}
    order: list[Node] = []
    state: dict[str, int] = {}   # 0/absent = unvisited, 1 = visiting, 2 = done

    def visit(n: Node) -> None:
        s = state.get(n.name, 0)
        if s == 2:
            return
        if s == 1:
            raise DagError(f"cycle in the lemma-DAG through {n.name}")
        state[n.name] = 1
        for dep in n.depends_on:
            visit(by_name[dep])
        state[n.name] = 2
        order.append(n)

    for n in dag.nodes:
        visit(n)
    return order


# --- the decomposer call (2.2) ------------------------------------------------
# DECOMPOSE_SYSTEM is the B-class playbook (from the grind-history harvest) as the
# reasoner's operating instructions. The caller prepends the drafter authority (pins
# + statement-design, from house_context.build_drafter_prompt via `system_preamble`)
# so the leaves are STATED to the same house standard — no import of autoformalize.

# (the playbook prose itself: domains/<name>/prompts/decompose-system.md)


def decompose_messages(pack: DomainPack, target: str, context_pack: str, *,
                       feedback: str | None = None,
                       system_preamble: str = "") -> list[dict]:
    base = pack.prompt("decompose-system")
    system = (system_preamble + "\n" + base) if system_preamble else base
    user = f"HARD TARGET:\n{target}\n"
    if context_pack:
        user += "\nAvailable declarations to consume:\n" + context_pack
    if feedback:
        user += ("\n\n" + feedback
                 + "\nRe-emit a corrected lemma-DAG in the SAME JSON shape.")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _extract_json_object(text: str | None):
    """The first balanced `{...}` JSON object in `text` (tolerates ```json fences and
    prose around it), string-aware so a Lean `{x : ℝ}` binder inside a value does not
    miscount braces. None if none parses."""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def with_target(dag: Dag, target_text: str) -> Dag:
    """`dag` with the target stub's definitions and imports attached. Called once, at
    draft time, so every later stage assembles modules against the same context the
    skeleton gate validated."""
    dag.preamble = target_preamble(target_text)
    dag.target_pointers = [m.replace(".", "/") + ".lean"
                           for m in re.findall(r"^\s*(?:public\s+)?import\s+([A-Za-z0-9_.]+)",
                                               target_text, re.M)
                           if m.split(".")[0] != "Mathlib"]
    return dag


def dag_to_dict(dag: Dag) -> dict:
    """Serialize a `Dag` back to the decomposer's JSON shape (inverse of `parse_dag`), so
    a run can persist the DAG and reparse it in the recompose step."""
    return {
        "main": {"name": dag.main.name, "statement": dag.main.statement,
                 "proof": dag.main.proof},
        "leaves": [{"name": leaf.name, "statement": leaf.statement,
                    "pointers": leaf.pointers, "depends_on": leaf.depends_on,
                    "applied_to": leaf.applied_to}
                   for leaf in dag.leaves],
        # set by the tick from the target file, never by the model
        "target": {"preamble": dag.preamble, "pointers": dag.target_pointers},
    }


def draft_decomposition(pack: DomainPack, target: str, context_pack: str, *, chat_fn,
                        system_preamble: str = "", feedback: str | None = None,
                        max_reask: int = 1, max_leaves: int | None = None) -> dict:
    """Stage: Claude SPLITS a hard target into a validated lemma-DAG. A malformed/invalid
    reply ⇒ up to `max_reask` re-ask rounds (feedback =
    the `DagError`), then a structured failure — never an infinite loop. `feedback` seeds
    the FIRST message (the skeleton-gate re-decompose round passes the elaboration errors
    here). Engine is the injected `chat_fn`. Returns `{ok, dag, tokens, error}`."""
    tokens = 0
    last_err = "no reply"
    for _ in range(max(1, max_reask + 1)):
        content, tk = chat_fn(decompose_messages(pack, target, context_pack,
                                                 feedback=feedback,
                                                 system_preamble=system_preamble))
        tokens += tk
        raw = _extract_json_object(content)
        try:
            dag = parse_dag(raw if raw is not None else (content or ""), max_leaves=max_leaves)
            return {"ok": True, "dag": dag, "tokens": tokens, "error": ""}
        except DagError as e:
            last_err = str(e)
            feedback = f"That lemma-DAG was invalid: {e}"
    return {"ok": False, "dag": None, "tokens": tokens, "error": last_err}


# --- the skeleton-elaboration gate (2.3, the load-bearing check) --------------

# (the license header is the domain's: `pack.license`)


def _module_text(pack: DomainPack, pointers, body: str) -> str:
    """Wrap a declaration `body` in the target library's module boilerplate: license,
    `module` header, Mathlib + `.lean`-pointer imports, autoImplicit off, the
    `@[expose] public section` (without which the decls are module-private), the
    pack's namespace, and the HOUSE OPENS.

    The opens are not decoration. `target_preamble` strips the stub's own `open` lines
    as boilerplate on the promise that this function re-emits them, and for five weeks
    it did not — so a decompose-path module carried none at all, from either source.
    A stub defining `certaintyEquivalent (P : Measure Ω)` (cal-bk-80) then dies
    `Unknown identifier Measure` on a split that is structurally fine. The pack is the
    right source: `[module] opens` is declared as what EVERY emitted module carries, and
    the skeleton is one of the three consumers that contract names."""
    mods = sorted({p for p in pointers if p.endswith(".lean")})
    imports = "\n".join(["public import Mathlib"]
                        + [pack.import_line(p) for p in mods])
    return (
        f"{pack.license}\nmodule\n\n"
        f"{imports}\n\n"
        f"{pack.module_preamble()}\n\n"
        + body
        + f"\n\nend {pack.namespace}\n"
    )


def target_preamble(target_text: str) -> str:
    """The target stub's own declarations MINUS its theorem — the `def`s/`abbrev`s the
    stub introduces, which exist in no importable module.

    Cut at the first `theorem`/`lemma` at column zero: everything before it is what the
    target brings with it, everything after is the statement the DAG replaces. The
    licence block and the boilerplate `_module_text` re-emits (module header, imports,
    `set_option`, `@[expose]`, `namespace`, and the house `open`s) are dropped.

    Comment state is tracked rather than pattern-matched line by line, because both
    shortcuts are wrong. A `/--` doc comment starts with `/-`, so a regex that skips
    comment delimiters eats its OPENING line and orphans the prose beneath it as bare
    text — Lean then says `unexpected identifier; expected command`, which reads as a
    broken definition rather than a broken cut. And a doc comment's body may begin a
    line with `open`, `import` or `end`; inside a comment those are prose, not commands.
    """
    body = re.split(r"^(?:@\[[^\]]*\]\s*\n)?(?:private\s+|protected\s+|nonrec\s+)*"
                    r"(?:theorem|lemma)\s", target_text, maxsplit=1, flags=re.M)[0]
    boilerplate = re.compile(
        r"^\s*(?:module\b|public\s+import\b|import\b|set_option\b|@\[expose\]|"
        r"namespace\b|end\b|open\b)")
    out: list[str] = []
    in_doc = in_plain = False
    for line in body.splitlines():
        st = line.lstrip()
        if in_doc:                       # keep the whole doc comment, prose included
            out.append(line)
            in_doc = not st.rstrip().endswith("-/")
            continue
        if in_plain:                     # the licence and friends: drop entirely
            in_plain = not st.rstrip().endswith("-/")
            continue
        if st.startswith("/--"):
            out.append(line)
            in_doc = not (st.rstrip().endswith("-/") and len(st) > 4)
            continue
        if st.startswith("/-"):
            in_plain = not (st.rstrip().endswith("-/") and len(st) > 3)
            continue
        if boilerplate.match(line):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _decompose_module(pack: DomainPack, dag: Dag, blocks: list[str]) -> str:
    """A module assembled from DAG pieces, carrying the target's own context.

    The single place that knows what a decompose-path module needs, because all three
    assemblies need the same thing and all three were missing it: the skeleton the gate
    elaborates, each leaf stub the prover receives, and the recomposed candidate that
    becomes the PR. A target INTRODUCES definitions — its leaf statements then refer to
    them — and its imports are the ones the splitter did not think to declare."""
    body = ([dag.preamble] if dag.preamble else []) + blocks
    pointers = list(dag.target_pointers) + [p for leaf in dag.leaves for p in leaf.pointers]
    return _module_text(pack, pointers, "\n\n".join(body))


def assemble_skeleton(pack: DomainPack, dag: Dag, meta: dict | None = None) -> str:
    """The skeleton module: every leaf `<statement> := by sorry`, the main theorem
    `<statement> := <main.proof>` (its proof applying the leaves, NOT sorry). If a
    good decomposition, this elaborates with exactly `len(leaves)` sorries — that is
    what `skeleton_gate` checks, before any leaf gets proving budget.

    `dag.preamble` is the target stub's own context, and omitting it is why the
    decomposer never once produced a leaf. A pipeline target INTRODUCES definitions — `cal-bk-69` defines `P`,
    `KRD` and `ED` in the stub itself, in no importable module — and every leaf statement
    the splitter writes refers to them. A skeleton assembled from the DAG alone therefore
    cannot elaborate, for a reason that has nothing to do with the split being good or
    bad, and the gate correctly rejected it every time from 2026-07-27 to 2026-08-31.

    Imports likewise come from the target as well as the leaves: the splitter declares
    the pointers it happens to notice (on `cal-bk-69`, one of the target's three), and
    trusting only those drops the rest."""
    return _decompose_module(pack, dag, [
        f"{n.statement} := {n.proof or 'by sorry'}" if n.is_main
        else f"{n.statement} := by sorry" for n in topo_order(dag)])


# --- leaf routing: DAG leaves as ordinary single-sorry prove targets (2.4) ----

def _leaf_filename(parent_id: str, name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", f"leaf-{parent_id}-{name}") + ".lean"


def _leaf_stub(pack: DomainPack, dag: Dag, leaf: Node, proved: dict) -> str:
    """A single-sorry stub module for one leaf: any already-proved dependency decls
    (no sorry) inlined ABOVE `<leaf.statement> := by sorry`, so a dependent leaf's
    proof can consume them while the stub stays a single-sorry target."""
    deps = [proved[d] for d in leaf.depends_on if proved.get(d)]
    # a proving hint the vibe agent reads: the lemmas the decomposer expects this leaf to
    # consume (a Lean line comment — no sorry, no effect on elaboration; sliced off by
    # `extract_leaf_decl` at recompose since it starts at the `theorem` keyword)
    hint = f"-- apply: {', '.join(leaf.applied_to)}\n" if leaf.applied_to else ""
    return _decompose_module(pack, dag, [*deps, f"{hint}{leaf.statement} := by sorry"])


def build_leaf_manifest(pack: DomainPack, dag: Dag, meta: dict, out_dir: str, *,
                        toolchain: str = "", main_commit: str = "",
                        proved: dict | None = None) -> dict:
    """Write per-leaf single-sorry stubs + a `manifest.json` for the DAG's leaves, in
    the shape `vibe_prove.py run/gate` consumes VERBATIM (they are ordinary single-sorry
    targets). Each leaf target carries `parent` (the main theorem name), `parent_id` (the
    decompose target id), and `dag_order` (its index in topo order among the leaves — the
    order they must be proved in). `proved` optionally maps an already-proved leaf name ->
    its gated declaration block, inlined into a dependent leaf's stub (keep-and-revise).
    Returns the manifest dict; writes the stubs + `manifest.json` into `out_dir`."""
    proved = proved or {}
    os.makedirs(out_dir, exist_ok=True)
    parent_id = meta.get("id", "dag")
    leaves = [n for n in topo_order(dag) if not n.is_main]   # dependencies first
    targets = []
    for i, leaf in enumerate(leaves):
        stub = _leaf_stub(pack, dag, leaf, proved)
        fname = _leaf_filename(parent_id, leaf.name)
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            f.write(stub)
        targets.append({
            "id": f"{parent_id}__{leaf.name}", "kind": "prove", "file": fname,
            "sorry_name": leaf.name, "pointers": list(leaf.pointers),
            "parent": dag.main.name, "parent_id": parent_id, "dag_order": i,
            "input_hash": hashlib.sha256((stub + toolchain).encode("utf-8")).hexdigest(),
        })
    manifest = {"toolchain": toolchain, "main_commit": main_commit,
                "decompose_parent": parent_id, "targets": targets}
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest


# --- recompose + keep-and-revise (2.5) ----------------------------------------

def extract_leaf_decl(pack: DomainPack, module_text: str, name: str) -> str | None:
    """The `theorem/lemma <name> ... := <proof>` block from a proved leaf module (from
    its keyword to just before the namespace's `end` / end of file). None if absent.

    Anchoring on the declaration NAME rather than on "the module's only declaration" is
    load-bearing now that a leaf module also carries the target's own definitions: the
    slice must begin BELOW them, because `recompose` re-emits the preamble itself and a
    slice that reached above the theorem would define everything twice."""
    m = re.search(
        rf"(?m)^\s*(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+)?(?:theorem|lemma)\s+{re.escape(name)}\b",
        module_text)
    if not m:
        return None
    start = m.start()
    tail = re.search(rf"(?m)^end {re.escape(pack.namespace)}\b", module_text[start:])
    end = start + tail.start() if tail else len(module_text)
    return module_text[start:end].strip()


def recompose(pack: DomainPack, dag: Dag, proved_leaves: dict, *, check_fn,
              meta: dict | None = None) -> dict:
    """Assemble proved leaves + the main theorem into ONE module and run the FULL gate.
    `proved_leaves` maps a leaf name -> its proved module text (what `vibe_prove` writes).

    - ALL leaves proved AND the assembled module passes `check_fn` -> `{ok:True, module,
      banked}`.
    - all proved but the recomposition FAILS the full gate -> `{ok:False, module, reason}`
      (a real failure mode: leaves that pass in isolation but not composed).
    - PARTIAL (a leaf missing or unextractable) -> `{ok:False, partial:True, banked,
      remainder, deferred:True}`: proved leaves are banked (standalone-PR candidates), the
      rest a declared remainder (`refs`, not `closes`) — never a silent gap; the gate is
      not called. `check_fn(module_text) -> {passed, reason}` binds the daemon + main name."""
    decls: dict[str, str] = {}
    for leaf in dag.leaves:
        mod = proved_leaves.get(leaf.name)
        d = extract_leaf_decl(pack, mod, leaf.name) if mod else None
        if d:
            decls[leaf.name] = d
    banked = [leaf.name for leaf in dag.leaves if leaf.name in decls]
    remainder = [leaf.name for leaf in dag.leaves if leaf.name not in decls]
    if remainder:
        return {"ok": False, "partial": True, "banked": banked, "remainder": remainder,
                "deferred": True, "reason": f"leaves not proved: {', '.join(remainder)}"}
    body = [decls[leaf.name] for leaf in topo_order(dag) if not leaf.is_main]
    body.append(f"{dag.main.statement} := {dag.main.proof}")
    module = _decompose_module(pack, dag, body)
    g = check_fn(module)
    if g.get("passed"):
        return {"ok": True, "partial": False, "module": module, "banked": banked,
                "remainder": [], "reason": ""}
    return {"ok": False, "partial": False, "module": module, "banked": banked,
            "remainder": [], "reason": g.get("reason", "recomposition failed the full gate")}


def environment_probe(pack: DomainPack) -> str:
    """A module that exercises the environment a skeleton verdict depends on.

    Deliberately NOT `example : True := by trivial`. That needs no environment, so a REPL
    which has lost its Mathlib heap answers it happily, the canary reports healthy, and a
    bogus rejection stands — the probe has to fail on exactly the state it exists to
    detect. Built through `_module_text`, so it carries the same imports and house opens
    every assembled skeleton does, and fails when those cannot resolve."""
    return _module_text(pack, [], "example : True := by trivial")


def environment_canary(pack: DomainPack, check_fn) -> bool:
    """Whether the Lean environment is intact enough for a rejection to mean anything.

    Fails CLOSED: a canary we could not run tells us nothing about the environment, and
    asserting a real rejection on that is precisely how a false verdict gets recorded."""
    try:
        res = check_fn(environment_probe(pack))
    except Exception:
        return False
    if not res or res.get("error"):
        return False
    return not res.get("errors")

def skeleton_gate(lean_text: str, n_leaves: int, *, check_fn, canary_fn=None) -> dict:
    """Elaborate the assembled skeleton. PASSES iff elaboration is clean AND
    `sorry_count == n_leaves` — the leaves are the only sorries and the main genuinely
    reduces to them. A daemon infra-error ⇒ INDETERMINATE (Task 1.4 `error` sentinel),
    never a false pass. Returns `{passed, indeterminate, sorry_count, errors, verdict}`.

    This is where a bad decomposition dies for ONE elaboration's cost — the mid-tier
    reasoner compensation. On failure the caller does one bounded re-decomposition."""
    res = check_fn(lean_text)
    if res.get("error"):   # H5: wedged daemon is not a verdict
        return {"passed": False, "indeterminate": True, "sorry_count": 0, "errors": [],
                "verdict": "indeterminate: " + str(res["error"])[:120]}
    errors = [str(e) for e in (res.get("errors") or [])]
    sc = res.get("sorry_count", 0)
    passed = (not errors) and (sc == n_leaves)
    if not passed and canary_fn is not None and not canary_fn():
        # The narrow, nasty window: the REPL answers WELL-FORMED but has lost its
        # environment, so the gate sees real-looking errors and has no doubt at all.
        # 2026-09-08 22:33 recorded a good cal-bk-80 split as a bad one this way —
        # `unknown namespace MeasureTheory`, container died two minutes later, and the
        # same skeleton passed on a healthy daemon. A transport failure was already
        # indeterminate; this is the case that was not. Paid only when the gate is
        # about to reject, so the happy path is unaffected.
        return {"passed": False, "indeterminate": True, "sorry_count": sc,
                "errors": errors,
                "verdict": "indeterminate: the Lean environment is unhealthy, so these "
                           "errors are not a verdict on the split — " + "; ".join(errors[:2])}
    if passed:
        verdict = ""
    elif errors:
        verdict = "skeleton does not elaborate: " + "; ".join(errors[:2])
    else:
        verdict = f"expected {n_leaves} sorries (the leaves), got {sc}"
    return {"passed": passed, "indeterminate": False, "sorry_count": sc,
            "errors": errors, "verdict": verdict}
