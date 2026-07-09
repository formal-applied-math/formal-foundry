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
    """'MathFin/FixedIncome/VasicekBondPrice.lean' → 'MathFin.FixedIncome.VasicekBondPrice'.

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
        for r in self._cd():
            if r.get("name") == name:
                return list(r.get("deps") or [])
        return []


def default_index_dir(foundry_root: str | None = None) -> str:
    """The conventional index location: <foundry>/index/ (probe/ is one below)."""
    if foundry_root is None:
        foundry_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(foundry_root, "index")
