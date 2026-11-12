"""Adapter over a cached lean_scout index (types / tactics / const_dep JSONL).

`house_context` consumes this to build context packs from REAL elaborated
signatures + tactic exemplars + the dependency graph, replacing the regex
scrape. lean_scout is run out-of-band (`scripts/build-index.sh`) once per pin;
this reads the cached JSONL with the stdlib only (no pyarrow — we use `--jsonl`).

If the index is absent, `available` is False and every method returns empty, so
callers fall back to the regex path. Field names track the lean_scout schema:
  types:     {name, module, type, docString, allowCompletion}
  tactics:   {module, goals:[{pp, usedConstants, …}], goalsAfter, ppTac, kind, …}
  const_dep: {name, module, deps:[…], allowCompletion}
"""

from __future__ import annotations

import json
import os


def path_to_module(name_or_path: str) -> str:
    """'<LakeRoot>/FixedIncome/VasicekBondPrice.lean' → '<Namespace>.FixedIncome.VasicekBondPrice'.

    A value that is already a Lean module name (no '/', no '.lean') is returned
    unchanged, so callers may pass either form."""
    p = name_or_path
    if p.endswith(".lean"):
        p = p[:-5]
    if "/" in p:
        p = p.replace("/", ".")
    return p


def _load_jsonl(path: str) -> list[dict]:
    recs: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
    except (OSError, ValueError):
        return []
    return recs


class ScoutIndex:
    """Lazily-loaded view over foundry/index/{types,tactics,const_dep}.jsonl."""

    def __init__(self, index_dir: str | None):
        self.index_dir = index_dir
        self._types: list[dict] | None = None
        self._tactics: list[dict] | None = None
        self._const_dep: list[dict] | None = None
        self._types_by_name_cache: dict[str, dict] | None = None
        self._dep_of_cache: dict[str, list[str]] | None = None

    @property
    def available(self) -> bool:
        return bool(self.index_dir) and os.path.isfile(
            os.path.join(self.index_dir, "types.jsonl"))

    def _t(self) -> list[dict]:
        if self._types is None:
            self._types = _load_jsonl(os.path.join(self.index_dir or "", "types.jsonl"))
        return self._types

    def _tac(self) -> list[dict]:
        if self._tactics is None:
            self._tactics = _load_jsonl(os.path.join(self.index_dir or "", "tactics.jsonl"))
        return self._tactics

    def _cd(self) -> list[dict]:
        if self._const_dep is None:
            self._const_dep = _load_jsonl(os.path.join(self.index_dir or "", "const_dep.jsonl"))
        return self._const_dep

    def _types_by_name(self) -> dict[str, dict]:
        """name -> types record, built once. Since the index carries the Mathlib
        neighborhoods (index_filter), a per-call linear scan is no longer free:
        `_index_pack` calls `signature_of` once per closure constant."""
        if self._types_by_name_cache is None:
            self._types_by_name_cache = {r["name"]: r for r in self._t() if r.get("name")}
        return self._types_by_name_cache

    def _dep_of(self) -> dict[str, list[str]]:
        """name -> direct deps, built once (see `_types_by_name`)."""
        if self._dep_of_cache is None:
            self._dep_of_cache = {r["name"]: list(r.get("deps") or [])
                                  for r in self._cd() if r.get("name")}
        return self._dep_of_cache

    def signatures(self, modules: list[str], max_per_module: int = 40
                   ) -> dict[str, list[tuple[str, str, str | None]]]:
        """{module: [(name, type, docString), …]} for the requested modules.

        `modules` may be repo-relative .lean paths or Lean module names."""
        wanted = {path_to_module(m) for m in modules}
        by_mod: dict[str, list[tuple[str, str, str | None]]] = {}
        for r in self._t():
            mod = r.get("module")
            if mod in wanted:
                bucket = by_mod.setdefault(mod, [])
                if len(bucket) < max_per_module:
                    bucket.append((r.get("name", ""), r.get("type", ""),
                                   r.get("docString")))
        return by_mod

    def tactic_exemplars(self, modules: list[str], limit: int = 6
                         ) -> list[tuple[str, str]]:
        """(goal_pp, tactic) pairs drawn from the cited modules — house-style
        few-shot examples of how this library discharges goals."""
        wanted = {path_to_module(m) for m in modules}
        out: list[tuple[str, str]] = []
        for r in self._tac():
            if r.get("module") not in wanted:
                continue
            goals = r.get("goals") or []
            tac = r.get("ppTac")
            if goals and tac:
                pp = goals[0].get("pp") if isinstance(goals[0], dict) else None
                if pp:
                    out.append((pp, tac))
                    if len(out) >= limit:
                        break
        return out

    def dependencies(self, name: str) -> list[str]:
        """Direct constant dependencies of `name` (empty if unknown)."""
        return list(self._dep_of().get(name, []))

    def signature_of(self, name: str) -> tuple[str, str, str | None] | None:
        """(module, type, docString) for a constant by name, or None if the
        types index does not know it."""
        r = self._types_by_name().get(name)
        if r is None:
            return None
        return (r.get("module", ""), r.get("type", ""), r.get("docString"))

    def dependents_of(self, targets) -> set[str]:
        """Every constant that depends on one of `targets`, transitively, with the
        targets themselves. `deps` in const_dep are a declaration's type AND value
        (`ConstantInfo.getUsedConstantsAsSet`), so a theorem counts through its proof:
        this is the relation the target library's program map draws its edges with.
        Memoised per target set: the refill asks the same three sets for every issue."""
        key = frozenset(targets)
        memo = self.__dict__.setdefault("_dependents_memo", {})
        if key not in memo:
            memo[key] = frozenset(self._dependents(key))
        return set(memo[key])

    def _dependents(self, targets) -> set[str]:
        rev = self.__dict__.get("_rev_cache")
        if rev is None:
            rev = {}
            for name, deps in self._dep_of().items():
                for d in deps:
                    rev.setdefault(d, []).append(name)
            self._rev_cache = rev
        seen, stack = set(), [t for t in targets]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(rev.get(x, ()))
        return seen

    def module_of(self, name: str) -> str | None:
        """The module that declares `name`, or None when the index does not hold it."""
        if getattr(self, "_module_of_cache", None) is None:
            self._module_of_cache = {r["name"]: r.get("module") for r in self._cd()
                                     if r.get("name")}
        return self._module_of_cache.get(name)

    def names_in(self, modules: list[str]) -> dict[str, list[str]]:
        """{module: [constant names]} for the requested modules (paths or module names)."""
        wanted = {path_to_module(m) for m in modules}
        out: dict[str, list[str]] = {}
        for r in self._cd():
            if r.get("module") in wanted and r.get("name"):
                out.setdefault(r["module"], []).append(r["name"])
        return out

    def dependency_closure(self, names: list[str], depth: int = 2) -> list[str]:
        """BFS over const_dep from `names` up to `depth` hops; the reachable
        constants excluding the seeds, in discovery order. This is the cross-file
        premise reach a context pack should surface (miniCTX: in-file context
        alone misses the cross-file dependencies)."""
        dep_of = self._dep_of()
        seen, frontier, order = set(names), list(names), []
        for _ in range(depth):
            nxt = []
            for n in frontier:
                for dname in dep_of.get(n, []):
                    if dname not in seen:
                        seen.add(dname)
                        order.append(dname)
                        nxt.append(dname)
            frontier = nxt
            if not frontier:
                break
        return order


def default_index_dir(foundry_root: str | None = None) -> str:
    """The conventional index location: <foundry>/index/ (probe/ is one below)."""
    if foundry_root is None:
        foundry_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(foundry_root, "index")
