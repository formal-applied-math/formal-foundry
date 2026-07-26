"""Run-record provenance layer (item N).

The obstruction census, the 09-30 price-sheet decision, and any future meta-review each
re-derive their view from the raw `runs/*.jsonl` telemetry in their own way. This
formalizes that telemetry into ONE normalized substrate: a versioned record per
(subject x attempt x stage), DERIVED (never authored) and content-addressed, plus a
SQLite view rebuilt from scratch each time (TauCetiData's shape, applied to our runs).

- draft records come from `refill-history.jsonl` (one per issue x attempt; a seed on
  attempt 1 has no history row, so it becomes one tick-level record so nothing is lost).
- prove records come from `pipeline-*-summary.jsonl` (one per target).
- per-stage fields the source stream does not carry are null — an honest gap.

Pure stdlib; no daemon, no Mistral, no `autoformalize` import (family/arch ride the
records already). Schema: `schema/run_record.v1.json`.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sqlite3
from typing import Iterator

SCHEMA_VERSION = "v1"
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCHEMA_PATH = os.path.join(os.path.dirname(_HERE), "schema", "run_record.v1.json")

# the normalized record shape (order is the SQLite column order too)
_FIELDS = ["record_id", "stage", "issue", "target", "attempt", "outcome", "gate",
           "family", "detail", "model", "tokens", "arch", "harness", "arm", "ts",
           "source_file"]
_INT_FIELDS = {"issue", "attempt", "tokens"}
# the identifying tuple the content hash is taken over (arm distinguishes A/B rows for the
# same target/ts so two arms never collide onto one id)
_ID_KEYS = ["stage", "issue", "target", "attempt", "outcome", "gate", "arm", "ts", "source_file"]


def load_schema() -> dict:
    """The versioned JSON Schema for a run record."""
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def record_id(rec: dict) -> str:
    """A stable content hash of the identifying tuple — the same record always hashes the
    same (idempotent rebuilds) and two records that differ in any identifying field get
    distinct ids (dedup key)."""
    payload = {k: rec.get(k) for k in _ID_KEYS}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _finish(rec: dict) -> dict:
    """Project onto the schema fields and stamp the content id."""
    out = {k: rec.get(k) for k in _FIELDS}
    out["record_id"] = record_id(rec)
    return out


def _read_jsonl(path: str) -> Iterator[dict]:
    """Yield dict records from a jsonl file, tolerant of junk lines and an absent file."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


# --- the shared substrate I/O (the single reader the consumers wire onto) ------

def read_jsonl(path: str) -> list[dict]:
    """Tolerant jsonl read (blank + junk lines skipped, absent file → []). The one reader
    obstructions/scoreboard go through, so file-discovery and parse-tolerance live here."""
    return list(_read_jsonl(path))


def summary_paths(runs_dir: str) -> list[str]:
    """Every prover `*-summary.jsonl` under `runs_dir`, sorted (the single glob)."""
    return sorted(glob.glob(os.path.join(runs_dir, "*-summary.jsonl")))


def raw_refill_records(runs_dir: str) -> list[dict]:
    """The RAW `refill-history.jsonl` records (record grain, full history intact) — for
    consumers whose classification needs the whole tick record, not the per-attempt view."""
    return read_jsonl(os.path.join(runs_dir, "refill-history.jsonl"))


def raw_summary_records(runs_dir: str) -> list[dict]:
    """The RAW prover summary rows across every `*-summary.jsonl`."""
    out: list[dict] = []
    for p in summary_paths(runs_dir):
        out.extend(read_jsonl(p))
    return out


def _target_to_issue(target: str | None) -> int | None:
    if not target:
        return None
    m = re.search(r"(\d+)$", target)
    return int(m.group(1)) if m else None


def _draft_records(history_path: str) -> Iterator[dict]:
    source = os.path.basename(history_path)
    for rec in _read_jsonl(history_path):
        if rec.get("issue") is None:
            continue
        issue = int(rec["issue"])
        base = {"stage": "draft", "issue": issue, "target": f"cal-bk-{issue}",
                "family": rec.get("family"), "arch": rec.get("arch"), "model": None,
                "tokens": None, "harness": None, "arm": None, "ts": rec.get("ts", ""),
                "source_file": source}
        hist = rec.get("history") or []
        if not hist:
            # a no-history record (a seed on attempt 1, or a pre-attempt defer) — keep the
            # tick-level outcome so the seed is not lost from the substrate
            yield _finish({**base, "attempt": int(rec.get("attempts") or 1),
                           "outcome": rec.get("outcome", ""), "gate": None, "detail": None})
            continue
        for row in hist:
            yield _finish({**base, "attempt": int(row.get("attempt") or 0),
                           "outcome": row.get("gate", ""), "gate": row.get("gate"),
                           "detail": row.get("detail")})


def _prove_records(runs_dir: str) -> Iterator[dict]:
    for path in summary_paths(runs_dir):
        source = os.path.basename(path)
        for rec in _read_jsonl(path):
            target = rec.get("target")
            if not target:
                continue
            yield _finish({"stage": "prove", "issue": _target_to_issue(target),
                           "target": target, "attempt": int(rec.get("rounds") or 1),
                           "outcome": rec.get("outcome", ""), "gate": None, "family": None,
                           "detail": None, "model": rec.get("model"),
                           "tokens": rec.get("tokens"), "arch": None,
                           "harness": rec.get("harness"), "arm": rec.get("arm"),
                           "ts": rec.get("ts", ""), "source_file": source})


def _ab_records(runs_dir: str) -> Iterator[dict]:
    """The A/B decomposer log (`ab-decomposer.jsonl`) as prove records — the price-sheet
    decision's arm outcomes join the one substrate (leaves/refinery_minutes stay in the
    scoreboard's own log; they are outside the run-record shape)."""
    source = "ab-decomposer.jsonl"
    for rec in _read_jsonl(os.path.join(runs_dir, source)):
        target = rec.get("target")
        if not target:
            continue
        yield _finish({"stage": "prove", "issue": _target_to_issue(target),
                       "target": target, "attempt": 1, "outcome": rec.get("outcome", ""),
                       "gate": None, "family": None, "detail": rec.get("note") or None,
                       "model": None, "tokens": rec.get("tokens"), "arch": None,
                       "harness": None, "arm": rec.get("arm"), "ts": rec.get("ts", ""),
                       "source_file": source})


def iter_run_records(runs_dir: str) -> list[dict]:
    """All normalized run records under `runs_dir` (draft + prove, prove including the A/B
    arm log), schema-conformant and content-addressed. An absent directory yields []."""
    out: list[dict] = list(_draft_records(os.path.join(runs_dir, "refill-history.jsonl")))
    out.extend(_prove_records(runs_dir))
    out.extend(_ab_records(runs_dir))
    return out


def build_sqlite(runs_dir: str, db_path: str) -> int:
    """(Re)derive the SQLite `run_record` view from `runs_dir` and return the row count.
    Dropped and rebuilt every call — the DB is DERIVED, never authored — with `record_id`
    as the primary key so re-derivation is idempotent and content-dedups."""
    records = iter_run_records(runs_dir)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS run_record")
        cols = ", ".join(f"{f} {'INTEGER' if f in _INT_FIELDS else 'TEXT'}" for f in _FIELDS)
        conn.execute(f"CREATE TABLE run_record ({cols}, PRIMARY KEY (record_id))")
        conn.executemany(
            f"INSERT OR REPLACE INTO run_record ({','.join(_FIELDS)}) "
            f"VALUES ({','.join('?' * len(_FIELDS))})",
            [[r.get(f) for f in _FIELDS] for r in records])
        conn.commit()
        return conn.execute("SELECT COUNT(*) FROM run_record").fetchone()[0]
    finally:
        conn.close()


def _foundry_root() -> str:
    return os.path.dirname(_HERE)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="derive the run-record provenance DB (item N)")
    ap.add_argument("--runs-dir", default=os.path.join(_foundry_root(), "runs"))
    ap.add_argument("--db", default=os.path.join(_foundry_root(), "runs", "provenance.db"))
    a = ap.parse_args()
    n = build_sqlite(a.runs_dir, a.db)
    print(json.dumps({"records": n, "db": a.db, "schema": SCHEMA_VERSION}))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
