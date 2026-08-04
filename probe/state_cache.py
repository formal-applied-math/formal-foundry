"""Content-addressed proof-state store: `{state -> the tactic that advanced it}`.

The sibling of `gate_cache` one level down. `gate_cache` addresses whole STATEMENTS
and caches a gate verdict; this addresses intermediate STATES of accepted proofs and
records what moved them forward. Same shape deliberately: a JSON store under `runs/`
(committed by the tick's persist step, so it accumulates across ticks), dropped
wholesale on a toolchain generation bump, tolerant of a corrupt file.

It is also the measurement instrument. Before this is worth consuming, one number has
to come back positive: do states recur ACROSS targets? A state seen five times inside
one proof is a loop in that proof and reusable by nobody. `report()` separates the two,
and `suggestions()` renders only the cross-target ones — so if the answer is "they do
not recur", the cache stays silent and costs nothing rather than feeding the prover
noise.

Pure stdlib.
"""
from __future__ import annotations

import json
import os

# Bump on a Mathlib/Lean pin change: a tactic that closed a goal under the old pin is
# not guaranteed to close it under the new one, and a stale suggestion is worse than
# none. Mirrors gate_cache.CACHE_GENERATION.
CACHE_GENERATION = "v1"

# A suggestion block large enough to be useful and small enough not to crowd the
# context pack that carries the pointer signatures.
_MAX_SUGGESTIONS = 12


class StateCache:
    """A `{state_key -> {tactic, state, targets, seen}}` store persisted to `path`."""

    def __init__(self, path: str, generation: str | None = None):
        self.path = path
        self.generation = generation or CACHE_GENERATION
        self._entries: dict[str, dict] = {}
        self.hits = 0
        self.misses = 0
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and data.get("generation") == self.generation:
            entries = data.get("entries")
            if isinstance(entries, dict):
                self._entries = entries

    def _save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"generation": self.generation, "entries": self._entries}, f,
                      ensure_ascii=False, indent=2, sort_keys=True)

    # --- reads -------------------------------------------------------------

    def get(self, key: str) -> str | None:
        """The tactic recorded for a state, or None."""
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        return entry.get("tactic")

    def entry(self, key: str) -> dict | None:
        return self._entries.get(key)

    @property
    def stats(self) -> dict:
        return {"entries": len(self._entries), "hits": self.hits, "misses": self.misses}

    # --- writes ------------------------------------------------------------

    def put(self, key: str, *, tactic: str, state: str, target: str) -> bool:
        """Record a sighting. Returns True when the state is new to the store.

        The FIRST tactic wins: a later sighting bumps `seen` and appends its target but
        does not replace the suggestion. Both tactics closed the state, so either serves,
        and a stable store keeps the context pack from churning run to run.
        """
        entry = self._entries.get(key)
        if entry is None:
            self._entries[key] = {"tactic": tactic, "state": state,
                                  "targets": [target], "seen": 1}
            self._save()
            return True
        entry["seen"] = int(entry.get("seen", 0)) + 1
        targets = entry.setdefault("targets", [])
        if target not in targets:
            targets.append(target)
        self._save()
        return False

    def ingest(self, pairs: list[dict], *, target: str) -> int:
        """Record every `(state, tactic)` pair from one proof. Returns how many were new."""
        new = 0
        for pair in pairs:
            key = pair.get("key")
            if not key:
                continue
            if self.put(key, tactic=pair.get("tactic", ""), state=pair.get("state", ""),
                        target=target):
                new += 1
        return new

    # --- the measurement ---------------------------------------------------

    def report(self) -> dict:
        """Recurrence, split by the only distinction that matters.

        `cross_target_states` is the discriminating number: states reached while proving
        two or more DIFFERENT targets. Within-target recurrence is a proof revisiting its
        own goal and buys nothing.
        """
        cross = [k for k, e in self._entries.items() if len(e.get("targets", [])) > 1]
        return {
            "distinct_states": len(self._entries),
            "total_sightings": sum(int(e.get("seen", 0)) for e in self._entries.values()),
            "recurring_states": sum(1 for e in self._entries.values()
                                    if int(e.get("seen", 0)) > 1),
            "cross_target_states": len(cross),
            "cross_target_keys": sorted(cross),
        }

    # --- consumption -------------------------------------------------------

    def suggestions(self, limit: int = _MAX_SUGGESTIONS) -> str:
        """A context-pack block of proven `state -> tactic` pairs, restricted to states
        that recurred across targets. Empty when nothing has recurred, which is the
        honest default: no evidence of reuse, no suggestions."""
        cross = [e for e in self._entries.values() if len(e.get("targets", [])) > 1]
        cross.sort(key=lambda e: -int(e.get("seen", 0)))
        blocks = []
        for entry in cross[:limit]:
            blocks.append(f"-- goal:\n{entry.get('state', '')}\n-- closed by: "
                          f"{entry.get('tactic', '')}")
        return "\n\n".join(blocks)
