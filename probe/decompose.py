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
    dag = Dag(main=main, leaves=leaves)
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
    `@[expose] public section` (without which the decls are module-private), and the
    pack's namespace."""
    mods = sorted({p for p in pointers if p.endswith(".lean")})
    imports = "\n".join(["public import Mathlib"]
                        + [pack.import_line(p) for p in mods])
    return (
        f"{pack.license}\nmodule\n\n"
        f"{imports}\n\n"
        f"{pack.module_preamble(opens=False)}\n\n"
        + body
        + f"\n\nend {pack.namespace}\n"
    )


def assemble_skeleton(pack: DomainPack, dag: Dag, meta: dict | None = None) -> str:
    """The skeleton module: every leaf `<statement> := by sorry`, the main theorem
    `<statement> := <main.proof>` (its proof applying the leaves, NOT sorry). If a
    good decomposition, this elaborates with exactly `len(leaves)` sorries — that is
    what `skeleton_gate` checks, before any leaf gets proving budget."""
    blocks = [f"{n.statement} := {n.proof or 'by sorry'}" if n.is_main
              else f"{n.statement} := by sorry"
              for n in topo_order(dag)]   # leaves first, main last
    pointers = [p for leaf in dag.leaves for p in leaf.pointers]
    return _module_text(pack, pointers, "\n\n".join(blocks))


# --- leaf routing: DAG leaves as ordinary single-sorry prove targets (2.4) ----

def _leaf_filename(parent_id: str, name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", f"leaf-{parent_id}-{name}") + ".lean"


def _leaf_stub(pack: DomainPack, leaf: Node, proved: dict) -> str:
    """A single-sorry stub module for one leaf: any already-proved dependency decls
    (no sorry) inlined ABOVE `<leaf.statement> := by sorry`, so a dependent leaf's
    proof can consume them while the stub stays a single-sorry target."""
    deps = [proved[d] for d in leaf.depends_on if proved.get(d)]
    pointers = list(leaf.pointers)
    # a proving hint the vibe agent reads: the lemmas the decomposer expects this leaf to
    # consume (a Lean line comment — no sorry, no effect on elaboration; sliced off by
    # `extract_leaf_decl` at recompose since it starts at the `theorem` keyword)
    hint = f"-- apply: {', '.join(leaf.applied_to)}\n" if leaf.applied_to else ""
    return _module_text(pack, pointers, "\n\n".join([*deps, f"{hint}{leaf.statement} := by sorry"]))


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
        stub = _leaf_stub(pack, leaf, proved)
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
    its keyword to just before the namespace's `end` / end of file). None if absent. The generated
    leaf modules hold exactly one declaration, so the slice is unambiguous."""
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
    module = _module_text(pack, [p for leaf in dag.leaves for p in leaf.pointers],
                          "\n\n".join(body))
    g = check_fn(module)
    if g.get("passed"):
        return {"ok": True, "partial": False, "module": module, "banked": banked,
                "remainder": [], "reason": ""}
    return {"ok": False, "partial": False, "module": module, "banked": banked,
            "remainder": [], "reason": g.get("reason", "recomposition failed the full gate")}


def skeleton_gate(lean_text: str, n_leaves: int, *, check_fn) -> dict:
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
    if passed:
        verdict = ""
    elif errors:
        verdict = "skeleton does not elaborate: " + "; ".join(errors[:2])
    else:
        verdict = f"expected {n_leaves} sorries (the leaves), got {sc}"
    return {"passed": passed, "indeterminate": False, "sorry_count": sc,
            "errors": errors, "verdict": verdict}
