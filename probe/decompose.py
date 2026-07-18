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
    proof_sketch: str = ""


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
                proof_sketch=m.get("proof_sketch", ""),
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
