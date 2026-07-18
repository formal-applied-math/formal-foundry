"""Lemma-DAG decomposition (Phase 2 of the autoformalization upgrade plan).

A hard target the plain draft->prove path can't close is split by the general
reasoner (Magistral) into a DAG of named leaf lemmas plus a main theorem that
applies them. This module is the SCHEMA layer: it validates that DAG (shape,
size, cycles, dangling deps) BEFORE any prover budget is spent — the first of the
Lean-side gates that make a mid-tier reasoner safe inside a verified protocol.

Design of record: docs/superpowers/specs/2026-07-18-decomposer-design.md.
Stdlib only; no Lean, no API, no network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

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
        leaves.append(Node(name=name, statement=leaf["statement"],
                           pointers=list(leaf.get("pointers") or []),
                           depends_on=list(leaf.get("depends_on") or [])))
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
    return dag


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

DECOMPOSE_SYSTEM = (
    "You are a Lean 4 proof ARCHITECT for MathFin (on Mathlib + BrownianMotion). Given a "
    "hard target theorem, SPLIT it into a lemma-DAG: a few named leaf lemmas plus a MAIN "
    "theorem whose proof applies them. Do NOT prove anything — you STATE the leaves and "
    "sketch the main proof as leaf applications.\n"
    "Playbook, in order:\n"
    "- Spike the RISKIEST kernel first: leaf #1 is the single step most likely to be "
    "impossible (a missing primitive, the hard analytic core). If it cannot be stated "
    "cleanly, the whole split is wrong — say so instead of inventing a constant.\n"
    "- Recon by conclusion-head: name each leaf after the Mathlib/MathFin result family "
    "its conclusion belongs to, so the prover consumes the right lemma.\n"
    "- Definition-shaping: shape a leaf so its hard side-conditions are INHERITED from a "
    "closed structure, not asserted as fresh hypotheses.\n"
    "- Skeleton-with-sorries: the main theorem's proof must ELABORATE as leaf applications "
    "with the leaves left `:= by sorry`.\n"
    "- Scope-fork with declared deferral: a leaf out of reach is split off and marked "
    "deferred, never silently dropped.\n"
    "- Keep leaves FEW and short (a handful at most); a split that needs many leaves is "
    "mis-shaped.\n"
    "Respond with ONLY a JSON object:\n"
    '{"main": {"name": "<snake_case>", "statement": "theorem <name> <binders> : <conclusion>", '
    '"proof": "<Lean proof applying the leaves — a term or `by ...`, NO sorry>"}, '
    '"leaves": [{"name": "<snake_case>", "statement": "theorem <name> <binders> : <conclusion>", '
    '"pointers": ["MathFin/.../X.lean"], "depends_on": ["<sibling leaf name>", ...]}]}. '
    "Every `statement` is a COMPLETE Lean 4 theorem HEAD (`theorem <name> ... : ...`) with NO "
    "`:=` — the leaves are assembled with `:= by sorry` and the main with your `proof`, and the "
    "whole skeleton must elaborate. pointers name the modules whose defs a leaf consumes."
)


def decompose_messages(target: str, context_pack: str, *, feedback: str | None = None,
                       system_preamble: str = "") -> list[dict]:
    system = (system_preamble + "\n" + DECOMPOSE_SYSTEM) if system_preamble else DECOMPOSE_SYSTEM
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


def draft_decomposition(target: str, context_pack: str, *, chat_fn,
                        system_preamble: str = "", max_reask: int = 1,
                        max_leaves: int | None = None) -> dict:
    """Stage: the general reasoner (Magistral) SPLITS a hard target into a validated
    lemma-DAG. A malformed/invalid reply ⇒ up to `max_reask` re-ask rounds (feedback =
    the `DagError`), then a structured failure — never an infinite loop. Engine is the
    injected `chat_fn`. Returns `{ok, dag, tokens, error}`."""
    feedback = None
    tokens = 0
    last_err = "no reply"
    for _ in range(max(1, max_reask + 1)):
        content, tk = chat_fn(decompose_messages(target, context_pack, feedback=feedback,
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

_LICENSE = ("/-\nCopyright (c) 2026 Raphael Coelho. All rights reserved.\n"
            "Released under Apache 2.0 license as described in the file LICENSE.\n"
            "Authors: Raphael Coelho\n-/")


def assemble_skeleton(dag: Dag, meta: dict | None = None) -> str:
    """The skeleton module: every leaf `<statement> := by sorry`, the main theorem
    `<statement> := <main.proof>` (its proof applying the leaves, NOT sorry). If a
    good decomposition, this elaborates with exactly `len(leaves)` sorries — that is
    what `skeleton_gate` checks, before any leaf gets proving budget."""
    meta = meta or {}
    pointers = sorted({p for leaf in dag.leaves for p in leaf.pointers if p.endswith(".lean")})
    imports = "\n".join(["public import Mathlib"]
                        + [f"public import {p[:-5].replace('/', '.')}" for p in pointers])
    blocks: list[str] = []
    for node in topo_order(dag):   # leaves first, main last
        if node.is_main:
            blocks.append(f"{node.statement} := {node.proof or 'by sorry'}")
        else:
            blocks.append(f"{node.statement} := by sorry")
    return (
        f"{_LICENSE}\nmodule\n\n"
        f"{imports}\n\n"
        "set_option autoImplicit false\n\n"
        "@[expose] public section\n\n"
        "namespace MathFin\n\n"
        + "\n\n".join(blocks)
        + "\n\nend MathFin\n"
    )


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
