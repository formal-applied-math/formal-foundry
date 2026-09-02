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
           "load_catalogue_entries", "load_library_entries", "PROBE_SUFFIX", "sweep_can_prove", "sweep_entry", "done_keys",
           "module_defs", "MODULE_DEF", "write_run_meta",
           "stratified_binder_sample", "batched_sweep_prover", "daemon_is_alive",
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
    positive. Measured on the flagship's `benchmarks/` at 48cb004 (2026-08-20): 1,489
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


_SWEEP_SORRY = re.compile(r"\bsorry\b")

#: consecutive daemon-error entries that mean the daemon is gone rather than flaky
_DEAD_DAEMON_LIMIT = 3

def _own_import_re(pack) -> re.Pattern:
    """Imports of the target library's OWN modules, from the pack's namespaces. The
    foundry does not name a domain: a second library is configuration (runbook 02)."""
    alt = "|".join(re.escape(n) for n in pack.own_namespaces)
    return re.compile(rf"^import\s+((?:{alt})(?:\.[A-Za-z0-9_']+)*)\s*$", re.M)
MODULE_DEF = re.compile(
    r"^(?:@\[[^\]]*\]\s*\n)?(?:private\s+|protected\s+|noncomputable\s+)*"
    r"(?:def|abbrev)\s+([A-Za-z_][A-Za-z0-9_']*)", re.M)


def module_defs(pack, code: str, root: str) -> list[str]:
    """The definitions an entry's own library modules introduce and its statement names.

    These fill the sweep's `{defs}`/`{unfold}` slots. Without them `tactic_sweep_prover`
    skips six of its eight tactics and the instrument degrades to `positivity` + `grind`
    — and every `full` entry in this corpus states a theorem about a library definition,
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
    for module in _own_import_re(pack).findall(code):
        path = os.path.join(root, *module.split(".")) + ".lean"
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        for name in MODULE_DEF.findall(src):
            if name not in out and _occurs_free(name, code):
                out.append(name)
    return out


def load_catalogue_entries(bench_glob: str) -> list[Entry]:
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
            out.append(Entry(arm="catalogue", entry_id=e.get("id", ""), domain=domain,
                             thm=thm, status=md.get("formalization_status") or "(none)",
                             provenance=prov, code=code))
    return out


#: `private` matches here so that a private declaration still ENDS the previous one —
#: drop it from the boundary and a public declaration's body swallows every private
#: lemma that follows it.
#:
#: Private declarations used to be dropped at emit time as well, because the shared
#: locator could not parse the modifier and they would have arrived as blind entries.
#: That was fixed at the source (`af_parse._DECL_RE`, `autoformalize._locate_named`), so
#: the 272 private declarations in this library are now probeable and stay in the
#: population. The emit-time locatability guard below still stands as the backstop.
_LIB_DECL = re.compile(
    r"^(?:@\[[^\]]*\]\s*\n)?(?:private\s+|protected\s+|nonrec\s+)?"
    r"(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)", re.M)
#: anchored, not searched: it asks whether THIS declaration is private, not whether one
#: appears anywhere in the text that follows it.
_LIB_PRIVATE = re.compile(r"^(?:@\[[^\]]*\]\s*\n)?private\s")
_COMMENT = re.compile(r"/-.*?-/|--[^\n]*", re.S)
_LIB_CONTEXT = re.compile(r"^(open\s.*|open\s+scoped\s.*|variable\s.*)$", re.M)
_LIB_NAMESPACE = re.compile(r"^(namespace|end)\s+([A-Za-z_][A-Za-z0-9_'.]*)\s*$", re.M)

#: appended to a probed declaration's name. The probe imports the module that already
#: declares it, and Lean will not accept the same name twice in the same namespace.
PROBE_SUFFIX = "_necessity_probe"


def _context_at(src: str, pos: int) -> tuple[list[str], list[str]]:
    """The context lines in effect at `pos`, **in source order**, and the namespaces
    still open there.

    Source order matters: a `variable` written inside a namespace must stay inside it,
    or a binder whose type is namespace-local stops resolving and the probe fails to
    elaborate for a reason that has nothing to do with the hypothesis under test."""
    head = src[:pos]
    items: list[tuple[int, str]] = [(m.start(), m.group(1).rstrip())
                                    for m in _LIB_CONTEXT.finditer(head)]
    stack: list[str] = []
    for m in _LIB_NAMESPACE.finditer(head):
        if m.group(1) == "namespace":
            stack.append(m.group(2))
            items.append((m.start(), f"namespace {m.group(2)}"))
        elif stack and stack[-1] == m.group(2):
            stack.pop()
            items = [it for it in items if it[1] != f"namespace {m.group(2)}"]
    return [text for _p, text in sorted(items)], stack


def load_library_entries(pack, root: str, max_proof_lines: int = 10,
                         import_root: str | None = None) -> list[Entry]:
    """Theorem declarations from a Lean library's own sources, each wrapped as a probe.

    **Why the library and not the catalogue.** Measured 2026-09-01: 330 of 332 catalogued
    `full` entries are term-mode re-exports — `:= Lib.brownian_markov_property hXpb hX
    t₀` — whose real proof lives here. A tactic sweep cannot reprove a research result
    from scratch, so sweeping the catalogue makes the power control fail on essentially
    everything and the blind fraction is 100% by construction, measuring the re-export
    layer rather than the mathematics. The two cases that motivated this whole gate,
    #161 `gainToPain_nonneg` and #162 `upCapture_smul`, are library lemmas.

    Each entry imports its own module and re-opens the context the declaration was
    elaborated in — `open`, `open scoped`, `variable`, and the enclosing `namespace`s —
    without which half these declarations would fail to elaborate for reasons that have
    nothing to do with hypothesis necessity.

    The declaration is **renamed**: the probe imports the module that already declares it,
    and Lean will not take the name twice. Renaming cannot let the sweep cheat by citing
    the original, since applying it would need the very hypothesis the probe dropped.

`root` is the directory *containing* the pack's `lake_root` — the checkout root —
    since the module name is the path relative to it. Only `package` is walked.

    `import_root` optionally replaces every probe's import with one shared module — the
    library's root, which re-exports the submodules. Default None: each declaration
    imports its own module, which is the faithful environment.

    A shared header was tried and **measured to be worthless** (2026-09-01). The argument
    for it was that one header lets the REPL keep a warm environment rather than
    re-elaborating a new one across 149 modules. That premise is false on this box: the
    REPL respawns on nearly every call, so nothing is ever warm. Measured on a trivial
    `example : 2+2 = 4 := by rfl` — root 193.8 s cold and 237.0 s on the second call, own
    module 206.8 s and 186.4 s, three respawns across the four. No shape is cheaper and no
    second call is warmer. Buying nothing, it loses to the faithful environment.

    `status` carries the proof shape — `term`, `tactic_short` (<= `max_proof_lines`),
    `tactic_long` — because the sweep's power varies sharply with it and the report
    stratifies on `status`.
    """
    import os
    from autoformalize import _locate_named
    out: list[Entry] = []
    # Only the package: a checkout also holds vendored upstream sources, exercise files
    # and tests, whose modules do not resolve as imports and which are not the library
    # under study.
    for dirpath, _dirs, files in os.walk(os.path.join(root, pack.lake_root)):
        for fn in sorted(files):
            if not fn.endswith(".lean"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, root)
            module = rel[:-len(".lean")].replace(os.sep, ".")
            try:
                with open(path, encoding="utf-8") as f:
                    src = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            # Prose in a docstring can begin a line with `theorem`, and matching it
            # invents a declaration (`theorem for ±1 walks` -> a decl named `for`)
            # whose probe cannot elaborate — which the sweep would record as the
            # theorem being unprovable. Mask comments, keeping offsets intact.
            masked = _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), src)
            starts = [(m.start(), m.group(1)) for m in _LIB_DECL.finditer(masked)]
            for i, (pos, name) in enumerate(starts):
                end = starts[i + 1][0] if i + 1 < len(starts) else len(src)
                body = src[pos:end].rstrip()
                # a trailing `end <ns>` belongs to the file, not the declaration
                body = re.sub(r"\n\s*end\s+[A-Za-z_][A-Za-z0-9_'.]*\s*$", "", body)
                # ... and a trailing docstring or attribute belongs to the NEXT one. A
                # `/-- ... -/` with no declaration after it is a syntax error, so
                # leaving it here would break the probe and read as the theorem
                # failing to elaborate.
                body = re.sub(r"(?:\n\s*(?:/--.*?-/|@\[[^\]]*\]))+\s*$", "", body,
                              flags=re.S).rstrip()
                nlines = len([l for l in body.splitlines() if l.strip()])
                if ":= by" in body or re.search(r"\bby\b", body):
                    status = "tactic_short" if nlines <= max_proof_lines else "tactic_long"
                else:
                    status = "term"
                context, stack = _context_at(src, pos)
                probe_name = name + PROBE_SUFFIX
                renamed = re.sub(
                    r"(^|\n)((?:@\[[^\]]*\]\s*\n)?(?:private\s+|protected\s+|nonrec\s+)?"
                    r"(?:theorem|lemma)\s+)" + re.escape(name) + r"(?![A-Za-z0-9_'.])",
                    lambda m: m.group(1) + m.group(2) + probe_name, body, count=1)
                head = [f"import {import_root or module}", ""] + context
                tail = [f"end {ns_}" for ns_ in reversed(stack)]
                code = "\n".join(head + ["", renamed, ""] + tail) + "\n"
                entry = Entry(arm="library", entry_id=f"{module}.{probe_name}",
                              domain=module, thm=probe_name, status=status,
                              provenance=f"{pack.name}-library", code=code)
                # The invariant that stops extraction failure from masquerading as
                # blindness: if the sweep's own parser cannot find the declaration, no
                # probe can be built from it and it must not enter the population.
                try:
                    _locate_named(entry.code, entry.thm)
                except ValueError:
                    continue
                out.append(entry)
    out.sort(key=lambda e: e.entry_id)
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


#: import-free liveness probe. Measured 1.9 s, against 35.7 s for anything that imports
#: the library — cheap enough to ask after every failed power control.
_LIVENESS_PROBE = "example : True := by trivial\n"


def daemon_is_alive(check_fn) -> bool:
    """Whether the daemon is answering at all, independent of any theorem.

    Needed because a failed power control is ambiguous: the sweep returns the probe
    untouched both when it genuinely cannot prove the theorem and when the daemon is
    gone. Those must not be conflated. A blind entry is *data* — it is never re-run and
    it raises the reported blind fraction — so an outage read as blindness would quietly
    become the result, and the arm would finish reporting that the instrument sees
    nothing."""
    try:
        res = check_fn(_LIVENESS_PROBE)
    except Exception:
        return False
    return not (res or {}).get("error")

def _closing_tactic(probe: str, proved: str) -> str | None:
    """The tactic the sweep substituted for `sorry`, recovered by diffing the probe
    against what came back. Recorded so a reader can see which sweep slot did the work."""
    i = probe.find("sorry")
    if i < 0 or not proved:
        return None
    tail = len(probe) - (i + len("sorry"))
    return proved[i:len(proved) - tail].strip() or None


def sweep_entry(entry: "Entry", *, check_fn, prove_fn, regate_fn,
                binders=None, skip_blind=True) -> list[dict]:
    """Probe an entry's binders. Returns one record per binder plus exactly one
    `power_control` record. Never raises: infrastructure trouble becomes a
    `daemon_error` record, which is excluded from every rate.

    `binders` defaults to every probe-worthy binder — the census. Pass a subset to sweep
    only the binders a sample drew; the power control still runs, since without it the
    sampled binders' verdicts cannot be read.

    `skip_blind` stops the sweep on an entry whose power control failed. Those binders'
    outcomes are uninterpretable by this study's own design — `sweep_report.rates`
    discards every record with `sweep_proves_original` false — so probing them buys
    nothing, and measured on the first pilot entry it costs 7.3 minutes each: a full
    eight-tactic sweep, every tactic failing, against a theorem the sweep has just
    demonstrated it cannot prove even with all its hypotheses. The binder still gets a
    `power_control_failed` record at zero elapsed time, so the population stays fully
    accounted for and the blind fraction is still countable. Pass False to spend the
    calls anyway."""
    import time
    from strengthen import necessity_probe

    def rec(binder, verdict, proves, tactic, elapsed):
        return {"arm": entry.arm, "entry_id": entry.entry_id, "domain": entry.domain,
                "thm": entry.thm, "status": entry.status, "provenance": entry.provenance,
                "binder": binder, "verdict": verdict, "sweep_proves_original": proves,
                "closing_tactic": tactic, "elapsed_s": round(elapsed, 3)}

    t0 = time.monotonic()
    proves_original = sweep_can_prove(entry.code, entry.thm, prove_fn=prove_fn)
    if not proves_original and not daemon_is_alive(check_fn):
        # Not blind — unreachable. Recorded as daemon_error so `done_keys` leaves the
        # entry unfinished and `run_sweep` can stop rather than consume the queue.
        out = [rec(None, "daemon_error", False, None, time.monotonic() - t0)]
        for nm in (probe_worthy_binders(entry.code, entry.thm) if binders is None
                   else binders):
            out.append(rec(nm, "daemon_error", False, None, 0.0))
        return out
    out = [rec(None, "power_control", proves_original, None, time.monotonic() - t0)]

    for nm in (probe_worthy_binders(entry.code, entry.thm) if binders is None
               else binders):
        if skip_blind and not proves_original:
            out.append(rec(nm, "power_control_failed", proves_original, None, 0.0))
            continue
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


def batched_sweep_prover(check_fn, def_names=(), tactics=None):
    """The whole sweep in ONE daemon call, then a per-tactic pass only on a hit.

    Same verdict as `strengthen.tactic_sweep_prover`, measured ~6x cheaper. On this
    corpus a daemon call costs 35.7 s of which 33.8 s is the library import — the same
    import, eight times, to do a few seconds of tactic work. Lean's
    `first | t1 | t2 | ...` tries alternatives in order inside a single elaboration,
    which is precisely the sweep's semantics, for one import.

    Two details it would be wrong to skip:

    * **Every alternative is guarded with `done`.** `first` commits to the first
      alternative that does not throw, and a tactic can succeed while leaving goals
      open. Unguarded, such a tactic would end the sweep with an unproved goal and be
      recorded as a failure where the per-call version would have tried the next slot.
      `(tac; done)` makes closing the goal the condition for winning.
    * **A hit is re-run per tactic.** The batched probe proves that *something* closes
      the goal, not what; the record has to name the slot, and the merged proof should
      be `positivity`, not a `first` chain. Positives are rare, so this pass costs
      almost nothing in aggregate while keeping the artifact honest.

    Fails open like everything else here — but note *how*: a batch that the daemon kills
    is not a negative, it is a missing measurement, and it falls back to the per-tactic
    sweep rather than being recorded as "nothing closed it".
    """
    from strengthen import SWEEP_TACTICS, tactic_sweep_prover
    if tactics is None:
        tactics = SWEEP_TACTICS
    defs = ", ".join(def_names)
    unfold = " ".join(def_names)
    live = [t for t in tactics
            if not (("{defs}" in t and not defs) or ("{unfold}" in t and not unfold))]

    def prove(probe: str) -> dict:
        if not live:
            return {"lean_text": probe, "tokens": 0}
        alts = " ".join(f"| ({t.format(defs=defs, unfold=unfold)}; done)" for t in live)
        attempt = _SWEEP_SORRY.sub("first " + alts, probe, count=1)
        res = check_fn(attempt)
        if res.get("error"):
            # The daemon kills the REPL at LEAN_ELAB_TIMEOUT (180 s by default) and a
            # batch is eight tactics deep in ONE elaboration, so it meets that cap on
            # precisely the hard theorems. A kill is not evidence that nothing closes
            # the goal — read as one it would manufacture false negatives, and an
            # all-negative A/B cannot detect them. Pay for the per-tactic sweep, where
            # each tactic gets the budget to itself.
            return tactic_sweep_prover(check_fn, def_names, tactics)(probe)
        if res.get("errors") or res.get("sorry_count", 0):
            return {"lean_text": probe, "tokens": 0}
        # Something closed it. Find out what, so the record can name the slot.
        return tactic_sweep_prover(check_fn, def_names, tactics)(probe)

    return prove

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
    """`(arm, entry_id)` pairs already **measured** in an output file.

    A run killed mid-write can leave a truncated final line; that line is dropped rather
    than raising, so a resume never needs the file repaired by hand.

    An entry that hit a `daemon_error` does NOT count as done. A dead daemon answers
    instantly, so an outage would otherwise write an error record for every remaining
    entry in seconds, mark them all finished, and no number of resumes would ever probe
    them again — the arm would quietly report a rate over whatever it reached before the
    daemon fell over. Re-running an entry costs one entry; losing it costs the study.
    """
    seen: set[tuple[str, str]] = set()
    spoiled: set[tuple[str, str]] = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                key = (d.get("arm", ""), d.get("entry_id", ""))
                seen.add(key)
                if d.get("verdict") == "daemon_error":
                    spoiled.add(key)
    except FileNotFoundError:
        return set()
    return seen - spoiled


def run_sweep(entries, out_path: str, *, check_fn, regate_fn, prove_fn=None,
              prove_for=None, binders_for=None, skip_blind=True, log=print) -> dict:
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
    stats = {"entries": 0, "records": 0, "skipped": 0, "aborted": None}
    consecutive_dead = 0
    with open(out_path, "a", encoding="utf-8") as f:
        for e in entries:
            if (e.arm, e.entry_id) in seen:
                stats["skipped"] += 1
                continue
            recs = sweep_entry(e, check_fn=check_fn, prove_fn=make_prover(e),
                              regate_fn=regate_fn,
                              binders=None if binders_for is None
                              else binders_for.get(e.entry_id, []),
                              skip_blind=skip_blind)
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
            stats["entries"] += 1
            stats["records"] += len(recs)
            hits = sum(1 for r in recs if r["verdict"] == "certified_unnecessary")
            log(f"[sweep] {e.entry_id}: {len(recs)} records, {hits} certified")

            # A dead daemon refuses instantly, so without this the loop would sprint
            # through every remaining entry writing errors and call itself finished.
            # Stop instead and let the operator bring the daemon back: done_keys will
            # not count these entries, so the resume picks them up.
            if any(r["verdict"] == "daemon_error" for r in recs):
                consecutive_dead += 1
                if consecutive_dead >= _DEAD_DAEMON_LIMIT:
                    stats["aborted"] = "daemon_unreachable"
                    log(f"[sweep] aborting: {consecutive_dead} consecutive entries "
                        f"hit daemon errors — the daemon is gone, not flaky")
                    break
            else:
                consecutive_dead = 0
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
    import os
    import sys
    from probe import daemon_check
    from strengthen import tactic_sweep_prover

    ap = argparse.ArgumentParser(description="necessity sweep over a Lean corpus")
    ap.add_argument("--arm", choices=("library", "catalogue", "mathlib"),
                    default="library",
                    help="'library' sweeps the target library's own declarations, where "
                         "the proofs and the hypotheses are; 'catalogue' sweeps the "
                         "benchmark entries, which are 99.4%% term-mode re-exports and on "
                         "which the instrument is blind by construction")
    ap.add_argument("--bench", default="", help="catalogue glob; defaults to the "
                                                "pack's benchmarks directory")
    ap.add_argument("--out", required=True)
    ap.add_argument("--status", default="tactic_short",
                    help="only sweep entries with this status; 'all' for every one. The "
                         "library arm's statuses are the proof shape: term, tactic_short, "
                         "tactic_long. The catalogue arm's are formalization_status")
    ap.add_argument("--max-proof-lines", type=int, default=10,
                    help="library arm: the tactic_short/tactic_long boundary")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N entries (0 = all). NOT a way to pilot: entries "
                         "keep corpus order, so a prefix of a draw is its alphabetically "
                         "first domains, not a sample of it. Pilot with a smaller "
                         "--sample, which inherits the stratification")
    ap.add_argument("--batched", action="store_true",
                    help="run the whole tactic sweep in one daemon call via Lean's "
                         "`first`, re-running per tactic only on a hit (~6x cheaper; "
                         "same verdict)")
    ap.add_argument("--sample", type=int, default=0,
                    help="draw this many binders, stratified by domain (0 = census). "
                         "The census was ruled out on measured latency; see "
                         "runs/necessity-sweep/daemon-stability.md")
    ap.add_argument("--seed", type=int, default=20260913,
                    help="seed for the stratified draw, reported with the result")
    ap.add_argument("--domain", default=None, help="pack name; defaults to pipeline.toml")
    ap.add_argument("--repo-root", default="",
                    help="the target library's checkout; defaults to the sibling named "
                         "by the pack. Its sources supply the definitions that fill the "
                         "sweep's unfold/simp slots")
    args = ap.parse_args(argv)

    import domain_pack
    pack = domain_pack.load(getattr(args, "domain", None)
                            or domain_pack.name_from_config("../pipeline.toml"))
    # the sibling checkout the pack names — `owner/repo` in the pack's slug
    repo_root = args.repo_root or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath("."))),
        pack.slug.split("/")[-1])
    bench = args.bench or os.path.join(repo_root, "benchmarks", "*.json")

    if args.arm == "library":
        entries = load_library_entries(pack, repo_root, args.max_proof_lines)
    else:
        entries = load_catalogue_entries(bench)
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
        defs = module_defs(pack, entry.code, repo_root)
        if args.batched:
            return batched_sweep_prover(daemon_check, defs)
        return tactic_sweep_prover(daemon_check, defs)

    def regate_fn(code):
        res = daemon_check(code)
        if res.get("error"):
            return {"passed": False, "reason": res["error"]}
        return {"passed": not res.get("errors") and res.get("sorry_count", 0) == 0}

    write_run_meta(args.out, arm=args.arm, corpus_root=repo_root,
                   extra={"status": args.status, "limit": args.limit,
                          "bench": bench, "entries_selected": len(entries),
                          "sample": args.sample, "seed": args.seed,
                          "batched": args.batched,
                          "binders_drawn": None if binders_for is None
                          else sum(len(b) for b in binders_for.values())})
    stats = run_sweep(entries, args.out, check_fn=daemon_check, prove_for=prove_for,
                      regate_fn=regate_fn, binders_for=binders_for)
    print(f"[sweep] done: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
