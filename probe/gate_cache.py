"""Persistent goal -> verdict cache for the adversarial gate probes (item L).

The vacuity (`⊢ False` from the hyps) and disproof (`⊢ ¬C`) probes re-elaborate the same
adversarial goal from scratch every attempt and every tick — a stuck issue that keeps
redrafting the same statement pays the full prove cost each time. This content-addresses
the goal and substitutes the cached verdict on a hit (Nexus caches disproofs too), saving
Mistral tokens + daemon time. A genuine refutation is deterministic (the b078f9f statement
pin guards against a reverted-goal false positive), so caching it is safe within a
toolchain generation; a Mathlib/Lean pin bump bumps CACHE_GENERATION and drops the file.

Opt-in: the cron builds a cache only when `[autoformalize].gate_cache` is set (default off ⇒
byte-identical). Pure stdlib; the store is runs/gate-cache.json.
"""
from __future__ import annotations

import hashlib
import json
import os

# bump on a Mathlib/Lean pin change so stale verdicts (proved against the old toolchain)
# are dropped wholesale rather than substituted.
CACHE_GENERATION = "v1"


def goal_key(goal: str) -> str:
    """Content hash of an adversarial goal (a whole lean file) — the cache key. The
    vacuity and disproof goals for one stub differ textually, so one hash space serves
    both without a kind tag."""
    return hashlib.sha256(goal.encode("utf-8")).hexdigest()


class GateCache:
    """A content-addressed {goal -> proved} store, persisted to `path` as JSON. Reads that
    predate the current CACHE_GENERATION (a toolchain bump) or a corrupt file start empty."""

    def __init__(self, path: str):
        self.path = path
        self._entries: dict[str, bool] = {}
        self.hits = 0
        self.misses = 0
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and data.get("generation") == CACHE_GENERATION:
            entries = data.get("entries")
            if isinstance(entries, dict):
                self._entries = {k: bool(v) for k, v in entries.items()}

    def get(self, goal: str) -> bool | None:
        """The cached verdict for `goal`, or None on a miss (also tallies hits/misses)."""
        v = self._entries.get(goal_key(goal))
        if v is None:
            self.misses += 1
            return None
        self.hits += 1
        return v

    def put(self, goal: str, proved: bool) -> None:
        self._entries[goal_key(goal)] = bool(proved)
        self._save()

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"generation": CACHE_GENERATION, "entries": self._entries}, f)
        os.replace(tmp, self.path)   # atomic — a crash mid-write never corrupts the store

    @property
    def stats(self) -> dict:
        return {"entries": len(self._entries), "hits": self.hits, "misses": self.misses}
