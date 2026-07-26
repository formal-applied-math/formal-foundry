"""Pure tests for the adversarial-gate goal cache (item L). No daemon, no API."""
from __future__ import annotations

import gate_cache as gc


def test_get_put_roundtrip_persists(tmp_path):
    p = str(tmp_path / "gate-cache.json")
    c = gc.GateCache(p)
    assert c.get("goal A") is None                       # miss
    c.put("goal A", True)
    c.put("goal B", False)
    assert c.get("goal A") is True and c.get("goal B") is False
    fresh = gc.GateCache(p)                              # a new instance reads the file
    assert fresh.get("goal A") is True and fresh.get("goal B") is False


def test_distinct_goals_distinct_keys(tmp_path):
    c = gc.GateCache(str(tmp_path / "c.json"))
    c.put("theorem g : False := by sorry", True)
    assert c.get("theorem g : False := by sorry") is True
    assert c.get("theorem h : False := by sorry") is None


def test_generation_bump_invalidates_the_file(tmp_path, monkeypatch):
    p = str(tmp_path / "c.json")
    gc.GateCache(p).put("g", True)
    monkeypatch.setattr(gc, "CACHE_GENERATION", "different")   # a toolchain pin bump
    assert gc.GateCache(p).get("g") is None


def test_tolerates_corrupt_file(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("not json{", encoding="utf-8")
    c = gc.GateCache(str(p))                             # must not raise
    assert c.get("g") is None
    c.put("g", True)                                    # overwrites with a valid store
    assert gc.GateCache(str(p)).get("g") is True


def test_stats_track_hits_and_misses(tmp_path):
    c = gc.GateCache(str(tmp_path / "c.json"))
    c.put("g", True)
    c.get("g"); c.get("g"); c.get("absent")
    assert c.stats["entries"] == 1 and c.stats["hits"] == 2 and c.stats["misses"] == 1
