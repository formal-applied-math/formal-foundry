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
           "module_defs", "MATHFIN_IMPORT", "MODULE_DEF", "write_run_meta",
           "stratified_binder_sample",
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


MATHFIN_IMPORT = re.compile(r"^import\s+(MathFin(?:\.[A-Za-z0-9_']+)*)\s*$", re.M)
MODULE_DEF = re.compile(
    r"^(?:@\[[^\]]*\]\s*\n)?(?:private\s+|protected\s+|noncomputable\s+)*"
    r"(?:def|abbrev)\s+([A-Za-z_][A-Za-z0-9_']*)", re.M)


def module_defs(code: str, mathfin_root: str) -> list[str]:
    """The definitions an entry's own MathFin modules introduce and its statement names.

    These fill the sweep's `{defs}`/`{unfold}` slots. Without them `tactic_sweep_prover`
    skips six of its eight tactics and the instrument degrades to `positivity` + `grind`
    — and every `full` entry in this corpus states a theorem about a MathFin definition,
    the case `strengthen.py`'s own trace says bare `positivity` fails on and
    `unfold gainToPain; positivity` closes. A sweep without these slots would report the
    corpus unreachable and measure its own wiring.

    Definitions the statement never names are dropped: splicing one into `unfold` makes
    the tactic fail to elaborate, so carrying it can only cost calls. Missing modules
    are skipped rather than raised on — a corpus entry may import a module this checkout
    does not have, and that is a reason to sweep it with fewer tactics, not to abort.
    """
    import os
    out: list[str] = []
    for module in MATHFIN_IMPORT.findall(code):
        path = os.path.join(mathfin_root, *module.split(".")) + ".lean"
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        for name in MODULE_DEF.findall(src):
            if name not in out and _occurs_free(name, code):
                out.append(name)
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


def sweep_entry(entry: "Entry", *, check_fn, prove_fn, regate_fn,
                binders=None) -> list[dict]:
    """Probe an entry's binders. Returns one record per binder plus exactly one
    `power_control` record. Never raises: infrastructure trouble becomes a
    `daemon_error` record, which is excluded from every rate.

    `binders` defaults to every probe-worthy binder — the census. Pass a subset to sweep
    only the binders a sample drew; the power control still runs, since without it the
    sampled binders' verdicts cannot be read."""
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

    for nm in (probe_worthy_binders(entry.code, entry.thm) if binders is None
               else binders):
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


def stratified_binder_sample(entries, n: int, seed: int):
    """Draw `n` probe-worthy binders, allocated across domains in proportion to how many
    each holds, and return `[(entry, [binder, ...]), ...]` in corpus order.

    The census is out on measured latency (see `runs/necessity-sweep/daemon-stability.md`),
    so the arm is a sample and the paper reports an interval rather than a point estimate.
    Two properties this has to have, both load-bearing for that interval:

    * **Reproducible from the seed alone.** The draw is a fact about the result and gets
      published with it, so a reader can redraw it.
    * **Binders drawn, not entries.** Sampling whole entries would be cheaper — one power
      control buys every binder in the entry — but it clusters: a wrapper's binders all
      fail together, so a cluster sample's binders are not independent and a Wilson
      interval over them would read narrower than the evidence supports. Drawing binders
      keeps the trials as close to independent as this corpus allows. Collisions still
      happen and the report says how many distinct entries the draw touched.

    Allocation is largest-remainder, so the per-domain counts sum to exactly `n` rather
    than to whatever rounding leaves.
    """
    import random
    pool: dict[str, list] = {}
    for e in entries:
        for nm in probe_worthy_binders(e.code, e.thm):
            pool.setdefault(e.domain, []).append((e, nm))
    total = sum(len(v) for v in pool.values())
    if n <= 0 or n >= total:
        quota = {d: len(v) for d, v in pool.items()}
    else:
        exact = {d: n * len(v) / total for d, v in pool.items()}
        quota = {d: int(x) for d, x in exact.items()}
        short = n - sum(quota.values())
        for d in sorted(pool, key=lambda d: (-(exact[d] - quota[d]), d))[:short]:
            quota[d] += 1

    rng = random.Random(seed)
    drawn: set[tuple[str, str]] = set()
    for domain in sorted(pool):
        for e, nm in rng.sample(pool[domain], quota[domain]):
            drawn.add((e.entry_id, nm))

    out = []
    for e in entries:
        got = [nm for nm in probe_worthy_binders(e.code, e.thm)
               if (e.entry_id, nm) in drawn]
        if got:
            out.append((e, got))
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


def run_sweep(entries, out_path: str, *, check_fn, regate_fn, prove_fn=None,
              prove_for=None, binders_for=None, log=print) -> dict:
    """Sweep `entries`, appending records to `out_path` and skipping entries already
    there. Flushes after every entry so a kill costs one entry, not the run.

    `prove_fn` is one prover for every entry. `prove_for(entry) -> prove_fn` builds one
    per entry, which is what a real arm needs: the sweep's `{defs}`/`{unfold}` slots are
    filled from the entry's own imported definitions, and those differ entry to entry.
    Exactly one of the two.

    `binders_for` maps `entry_id` to the binders to probe, which is how a sample runs;
    omitted, every entry gets its full probe-worthy set."""
    import os
    if (prove_fn is None) == (prove_for is None):
        raise TypeError("run_sweep takes exactly one of prove_fn or prove_for")
    make_prover = prove_for if prove_for is not None else (lambda _e: prove_fn)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    seen = done_keys(out_path)
    stats = {"entries": 0, "records": 0, "skipped": 0}
    with open(out_path, "a", encoding="utf-8") as f:
        for e in entries:
            if (e.arm, e.entry_id) in seen:
                stats["skipped"] += 1
                continue
            recs = sweep_entry(e, check_fn=check_fn, prove_fn=make_prover(e),
                              regate_fn=regate_fn,
                              binders=None if binders_for is None
                              else binders_for.get(e.entry_id, []))
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
            stats["entries"] += 1
            stats["records"] += len(recs)
            hits = sum(1 for r in recs if r["verdict"] == "certified_unnecessary")
            log(f"[sweep] {e.entry_id}: {len(recs)} records, {hits} certified")
    return stats


def write_run_meta(out_path: str, *, arm: str, corpus_root: str, extra=None) -> dict:
    """Append one line to `<out_path>.meta.jsonl` describing what this run read.

    The corpus is a separate, live checkout: it moved by an entry mid-session while this
    driver was being built. A rate is meaningless without the population it was taken
    over, so the run pins its own — commit, tactics, timestamp — beside the records
    rather than leaving the paper to quote a number measured by hand on another day.

    Kept OUT of the records file on purpose: the record schema is fixed, every key
    present on every line, and a differently-shaped row in the middle of it would break
    every consumer that trusts that. `corpus_commit` is `unknown` when the root is not a
    checkout — a missing provenance line is worth recording, not worth aborting over.
    """
    import datetime
    import subprocess
    from strengthen import SWEEP_TACTICS

    try:
        commit = subprocess.run(
            ["git", "-C", corpus_root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    meta = {"arm": arm, "corpus_root": corpus_root, "corpus_commit": commit,
            "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "sweep_tactics": list(SWEEP_TACTICS)}
    meta.update(extra or {})
    with open(out_path + ".meta.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
    return meta

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
    ap.add_argument("--sample", type=int, default=0,
                    help="draw this many binders, stratified by domain (0 = census). "
                         "The census was ruled out on measured latency; see "
                         "runs/necessity-sweep/daemon-stability.md")
    ap.add_argument("--seed", type=int, default=20260913,
                    help="seed for the stratified draw, reported with the result")
    ap.add_argument("--mathfin-root", default="../../formal-mathfin",
                    help="checkout whose MathFin/*.lean supply the definitions that fill "
                         "the sweep's unfold/simp slots")
    args = ap.parse_args(argv)

    entries = load_mathfin_entries(args.bench)
    if args.status != "all":
        entries = [e for e in entries if e.status == args.status]
    binders_for = None
    if args.sample:
        drawn = stratified_binder_sample(entries, args.sample, args.seed)
        binders_for = {e.entry_id: b for e, b in drawn}
        entries = [e for e, _b in drawn]
        print(f"[sweep] sampled {sum(len(b) for _e, b in drawn)} binders across "
              f"{len(entries)} entries, seed {args.seed}")

    # AFTER the draw, not before: --limit is "stop after N entries", so a pilot run
    # is the real run's first N entries and its per-record cost projects the rest.
    # Truncating first would instead draw a sample out of a truncated corpus, which
    # predicts nothing and silently changes the population.
    if args.limit:
        entries = entries[:args.limit]

    def prove_for(entry):
        # Per entry, not once: the sweep's {defs}/{unfold} slots take THIS entry's
        # imported definitions. Passed nothing, tactic_sweep_prover skips six of its
        # eight tactics and the instrument silently becomes `positivity` + `grind`.
        return tactic_sweep_prover(daemon_check,
                                   module_defs(entry.code, args.mathfin_root))

    def regate_fn(code):
        res = daemon_check(code)
        if res.get("error"):
            return {"passed": False, "reason": res["error"]}
        return {"passed": not res.get("errors") and res.get("sorry_count", 0) == 0}

    write_run_meta(args.out, arm=args.arm, corpus_root=args.mathfin_root,
                   extra={"status": args.status, "limit": args.limit,
                          "bench": args.bench, "entries_selected": len(entries),
                          "sample": args.sample, "seed": args.seed,
                          "binders_drawn": None if binders_for is None
                          else sum(len(b) for b in binders_for.values())})
    stats = run_sweep(entries, args.out, check_fn=daemon_check, prove_for=prove_for,
                      regate_fn=regate_fn, binders_for=binders_for)
    print(f"[sweep] done: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
