"""necessity_sweep — drive the necessity prober (backlog item R) over a whole corpus.

`strengthen.py` answers "is this hypothesis needed by this theorem?" for one candidate
inside the pipeline's gate phase. This module asks the same question of an entire
library, offline, with no pipeline and no model calls: it loads catalogued entries,
triages their binders, and records one verdict per binder.

Positives are certified — the reduced statement was proved and re-gated. Negatives are
not: the prover here is a fixed eight-tactic sweep, so "not closed" means "not shown
unnecessary", never "necessary". Every consumer of this data must preserve that.
"""
from __future__ import annotations

import glob as _glob
import json
import re
from dataclasses import dataclass

__all__ = ["PRIMARY_DECL", "Entry", "primary_decl", "probe_worthy_binders",
           "load_mathfin_entries", "sweep_can_prove", "sweep_entry", "done_keys",
           "run_sweep", "main"]

PRIMARY_DECL = re.compile(
    r"^(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+|nonrec\s+)?"
    r"(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)", re.M)


@dataclass(frozen=True)
class Entry:
    arm: str
    entry_id: str
    domain: str
    thm: str
    status: str
    provenance: str
    code: str


def primary_decl(code: str) -> str | None:
    """The name of the entry's primary declaration — the LAST top-level theorem/lemma.
    Catalogued entries put helper lemmas first and the headline result last."""
    names = PRIMARY_DECL.findall(code)
    return names[-1] if names else None


def _occurs_free(name: str, text: str) -> bool:
    """Whether `name` occurs in `text` as a whole Lean identifier. Boundary-aware, so
    `h` does not match inside `hs` — a substring test would silently pre-filter real
    hypotheses and understate the result."""
    return re.search(r"(?<![A-Za-z0-9_'])" + re.escape(name) + r"(?![A-Za-z0-9_'])",
                     text) is not None


def probe_worthy_binders(code: str, thm: str) -> list[str]:
    """Explicit binders worth spending a daemon call on, in signature order.

    SOUND PRE-FILTER: if a binder's name occurs free in the rest of the signature or in
    the conclusion, dropping it cannot elaborate, so the free filter would reject it and
    the call is wasted. Skipping those removes only certain-failures — never a possible
    positive. Measured on formal-mathfin `benchmarks/` at 48cb004 (2026-08-20): 1,489
    explicit binders in the 330 `full` entries -> 700, across 262 entries.

    The spec and plan quote 1,465 -> 689 from corpus commit 8e52f446; this function
    still reproduces those exactly on that snapshot. The corpus grew by nine entries
    later the same day (c419f0f0, the reified payoff language), which is the whole
    difference — the filter's meaning has not moved.
    """
    from autoformalize import _binder_groups, _locate_named
    try:
        bstart, sep, end = _locate_named(code, thm)
    except ValueError:
        return []
    sig, concl = code[bstart:sep], code[sep:end]
    groups = list(_binder_groups(sig))
    out: list[str] = []
    for i, (_s, _e, opener, names) in enumerate(groups):
        if opener != "(":            # implicit/instance binders are not hypotheses
            continue
        rest = "".join(sig[gs:ge] for j, (gs, ge, _o, _n) in enumerate(groups) if j != i)
        for nm in names:
            if not _occurs_free(nm, rest) and not _occurs_free(nm, concl):
                out.append(nm)
    return out


def load_mathfin_entries(bench_glob: str) -> list[Entry]:
    """Catalogued entries from a `benchmarks/*.json` glob, one per primary declaration.
    Entries with no theorem declaration (definition-only catalogue rows) are skipped."""
    out: list[Entry] = []
    for path in sorted(_glob.glob(bench_glob)):
        domain = path.rsplit("/", 1)[-1][:-len(".json")]
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        for e in payload.get("theorems", []):
            code = (e.get("code") or {}).get("lean") or ""
            thm = primary_decl(code)
            if not thm:
                continue
            md = e.get("metadata") or {}
            prov = ((md.get("provenance") or {}).get("source")) or "human"
            out.append(Entry(arm="mathfin", entry_id=e.get("id", ""), domain=domain,
                             thm=thm, status=md.get("formalization_status") or "(none)",
                             provenance=prov, code=code))
    return out


def _statement_only(code: str, thm: str) -> str | None:
    """`code` with the theorem's proof replaced by `sorry` and NO binder dropped —
    the power-control probe."""
    from autoformalize import _locate_named
    try:
        bstart, sep, end = _locate_named(code, thm)
    except ValueError:
        return None
    return code[:bstart] + code[bstart:sep] + code[sep:end] + ":= by sorry\n"


def sweep_can_prove(code: str, thm: str, *, prove_fn) -> bool:
    """Whether the fixed tactic sweep closes this theorem with ALL hypotheses present.

    The power control. A theorem that fails it is one the instrument cannot speak about:
    the sweep's failure on a REDUCED statement then says nothing about the dropped
    hypothesis. Such theorems are excluded from the rate denominator and reported as the
    blind fraction. Fails closed — any trouble reads as "cannot prove", which only ever
    shrinks the population we make claims about."""
    probe = _statement_only(code, thm)
    if probe is None:
        return False
    try:
        got = prove_fn(probe)
    except Exception:
        return False
    text = (got or {}).get("lean_text") or ""
    return bool(text) and "sorry" not in text


def _closing_tactic(probe: str, proved: str) -> str | None:
    """The tactic the sweep substituted for `sorry`, recovered by diffing the probe
    against what came back. Recorded so a reader can see which sweep slot did the work."""
    i = probe.find("sorry")
    if i < 0 or not proved:
        return None
    tail = len(probe) - (i + len("sorry"))
    return proved[i:len(proved) - tail].strip() or None


def sweep_entry(entry: "Entry", *, check_fn, prove_fn, regate_fn) -> list[dict]:
    """Probe every probe-worthy binder of one entry. Returns one record per binder plus
    exactly one `power_control` record. Never raises: infrastructure trouble becomes a
    `daemon_error` record, which is excluded from every rate."""
    import time
    from strengthen import necessity_probe

    def rec(binder, verdict, proves, tactic, elapsed):
        return {"arm": entry.arm, "entry_id": entry.entry_id, "domain": entry.domain,
                "thm": entry.thm, "status": entry.status, "provenance": entry.provenance,
                "binder": binder, "verdict": verdict, "sweep_proves_original": proves,
                "closing_tactic": tactic, "elapsed_s": round(elapsed, 3)}

    t0 = time.monotonic()
    proves_original = sweep_can_prove(entry.code, entry.thm, prove_fn=prove_fn)
    out = [rec(None, "power_control", proves_original, None, time.monotonic() - t0)]

    for nm in probe_worthy_binders(entry.code, entry.thm):
        t1 = time.monotonic()
        probe = necessity_probe(entry.code, entry.thm, {nm})
        if probe is None:
            out.append(rec(nm, "free_filter_rejected", proves_original, None,
                           time.monotonic() - t1))
            continue
        res = check_fn(probe)
        if res.get("error"):
            out.append(rec(nm, "daemon_error", proves_original, None,
                           time.monotonic() - t1))
            continue
        if res.get("errors"):
            out.append(rec(nm, "free_filter_rejected", proves_original, None,
                           time.monotonic() - t1))
            continue
        try:
            attempt = prove_fn(probe)
        except Exception:
            out.append(rec(nm, "daemon_error", proves_original, None,
                           time.monotonic() - t1))
            continue
        proved = (attempt or {}).get("lean_text") or ""
        if not proved or "sorry" in proved:
            out.append(rec(nm, "not_shown_unnecessary", proves_original, None,
                           time.monotonic() - t1))
            continue
        if not regate_fn(proved).get("passed"):
            out.append(rec(nm, "not_shown_unnecessary", proves_original, None,
                           time.monotonic() - t1))
            continue
        out.append(rec(nm, "certified_unnecessary", proves_original,
                       _closing_tactic(probe, proved), time.monotonic() - t1))
    return out


def done_keys(path: str) -> set[tuple[str, str]]:
    """`(arm, entry_id)` pairs already present in an output file. A run killed mid-write
    can leave a truncated final line; that line is dropped rather than raising, so a
    resume never needs the file repaired by hand."""
    out: set[tuple[str, str]] = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                out.add((d.get("arm", ""), d.get("entry_id", "")))
    except FileNotFoundError:
        return set()
    return out


def run_sweep(entries, out_path: str, *, check_fn, prove_fn, regate_fn,
              log=print) -> dict:
    """Sweep `entries`, appending records to `out_path` and skipping entries already
    there. Flushes after every entry so a kill costs one entry, not the run."""
    import os
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    seen = done_keys(out_path)
    stats = {"entries": 0, "records": 0, "skipped": 0}
    with open(out_path, "a", encoding="utf-8") as f:
        for e in entries:
            if (e.arm, e.entry_id) in seen:
                stats["skipped"] += 1
                continue
            recs = sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                              regate_fn=regate_fn)
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
            stats["entries"] += 1
            stats["records"] += len(recs)
            hits = sum(1 for r in recs if r["verdict"] == "certified_unnecessary")
            log(f"[sweep] {e.entry_id}: {len(recs)} records, {hits} certified")
    return stats


def main(argv=None) -> int:
    import argparse
    import sys
    from probe import daemon_check
    from strengthen import tactic_sweep_prover

    ap = argparse.ArgumentParser(description="necessity sweep over a Lean corpus")
    ap.add_argument("--arm", choices=("mathfin", "mathlib"), default="mathfin")
    ap.add_argument("--bench", default="../../formal-mathfin/benchmarks/*.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--status", default="full",
                    help="only sweep entries with this formalization_status; 'all' for every one")
    ap.add_argument("--limit", type=int, default=0, help="stop after N entries (0 = all)")
    args = ap.parse_args(argv)

    entries = load_mathfin_entries(args.bench)
    if args.status != "all":
        entries = [e for e in entries if e.status == args.status]
    if args.limit:
        entries = entries[:args.limit]

    prove_fn = tactic_sweep_prover(daemon_check)

    def regate_fn(code):
        res = daemon_check(code)
        if res.get("error"):
            return {"passed": False, "reason": res["error"]}
        return {"passed": not res.get("errors") and res.get("sorry_count", 0) == 0}

    stats = run_sweep(entries, args.out, check_fn=daemon_check, prove_fn=prove_fn,
                      regate_fn=regate_fn)
    print(f"[sweep] done: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
