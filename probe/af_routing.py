"""af_routing — extracted from autoformalize.py; see it for the pipeline overview."""
from __future__ import annotations

from issues import difficulty_rank, select_issues
from probe_lib import DEF_RE, append_jsonl, extract_lean_code, lint_violations
import json
import os

__all__ = ['ROUTING_ARCH', 'count_pointer_defs', 'classify_refill', 'load_refill_families', 'load_prior_unknowns', '_DIVERSITY', '_LESSON_SKIP', 'load_prior_lessons', 'render_prior_lessons', 'route_for', 'resolve_route', '_DEMOTED_FAMILIES', 'order_by_route']



# --- primitives-aware routing (F3+F2 of the composed design) ------------------
#
# One measured property routes every issue: does the library already have the
# primitives its statement needs? Static measurement (pointer modules export
# consumables) + runtime evidence (a prior depth-exhausted attempt) pick between
# the theorem-stub path and the definitions path. Design:
# docs/superpowers/specs/2026-07-17-primitives-aware-routing-design.md.

# Evidence is ARCHITECTURE-SCOPED (R: "don't optimize the current architecture
# for past architecture failures"): every attempted-record is stamped with
# ROUTING_ARCH and the routing loaders trust ONLY current-architecture records.
# Bumping this constant on an architecture change makes the pipeline run from
# zero evidence automatically, while within-version memory (don't re-buy the
# same failure every tick) keeps working. Unstamped/foreign records stay in the
# file as human-readable telemetry; they just don't steer.
ROUTING_ARCH = "routing-v1-2026-07-17"



def count_pointer_defs(main_repo: str, pointers: list[str]) -> int:
    """Consumable exports (`def`/`abbrev`/`structure`, via the shared
    `probe_lib.DEF_RE`) in the issue's pointer modules — the routing measurement.
    0 ⇒ a theorem-only stub's TYPE has nothing to consume ⇒ the definitions path.
    Missing files count 0 (fail toward defs)."""
    n = 0
    for p in pointers:
        if not p.endswith(".lean"):
            continue
        try:
            with open(os.path.join(main_repo, p), encoding="utf-8") as f:
                n += len(DEF_RE.findall(f.read()))
        except OSError:
            continue
    return n




def classify_refill(rec: dict) -> str:
    """Obstruction family for a refill `attempted` record — the drafter's triage
    analogue. Computed at write time (rides the record in refill-history.jsonl)
    and at read time for records written before the field existed. Routing
    evidence takes precedence over trailing noise: a depth rejection ANYWHERE in
    the history classifies the issue even when a later attempt died on a flaky
    intent parse (the first CI run's #66: ['depth', 'intent'])."""
    out = rec.get("outcome", "")
    if out == "seeded":
        return "seeded"
    gates = {h.get("gate") for h in rec.get("history", []) or []}
    if out in ("depth", "blocked_on_infra") or gates & {"depth", "blocked_on_infra"}:
        return "needs_primitives"
    if out in ("newdef_depth", "ungrounded") or gates & {"newdef_depth", "ungrounded"}:
        return "defs_rejected"
    if out == "trivial":
        return "trivial_restatement"
    if out == "unfaithful":
        return "fidelity"
    if out in ("intent", "formalize"):
        return "undraftable"
    if out in ("vacuous", "false"):
        return "statement_wrong"
    if out == "budget":
        return "budget"
    if out == "indeterminate" or "indeterminate" in gates:
        return "infra_indeterminate"   # H5: wedged daemon, retryable — not a real verdict
    return "infra"




def load_refill_families(history_path: str) -> dict[int, str]:
    """Issue number → family of its LATEST current-architecture refill-history
    record. Records stamped with a different (or no) ROUTING_ARCH are ignored —
    a past architecture's failures never steer this one. Tolerant of junk lines
    and of the file being absent."""
    fams: dict[int, str] = {}
    try:
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("arch") != ROUTING_ARCH:
                    continue
                if rec.get("issue") is not None:
                    # always RE-classify (the stored family is telemetry, not
                    # authority) so classifier fixes apply to existing records:
                    # the first CI run stored #66 as undraftable although its
                    # history carries depth evidence.
                    fams[int(rec["issue"])] = classify_refill(rec)
    except OSError:
        pass
    return fams




def load_prior_unknowns(history_path: str) -> dict[int, list[str]]:
    """Issue → union (first-seen order) of `unknown_identifiers` across its
    current-architecture refill-history rows — the missing declarations earlier
    drafts guessed at; they become defs-route hints ("define equivalents where
    sensible"). Foreign-architecture records are ignored, like the families."""
    out: dict[int, list[str]] = {}
    try:
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("issue") is None \
                        or rec.get("arch") != ROUTING_ARCH:
                    continue
                bucket = out.setdefault(int(rec["issue"]), [])
                for row in rec.get("history", []) or []:
                    for u in row.get("unknown_identifiers", []) or []:
                        if u not in bucket:
                            bucket.append(u)
    except OSError:
        pass
    return out




# --- cross-tick lessons + diversity injection (item K) ------------------------
# The Nexus episode-discipline half missing here: refill-history records outcomes,
# but each cross-TICK retry re-drafts nearly blind. load_prior_lessons compresses a
# failed issue's latest record into a post-mortem the next tick's intent prompt cites,
# rotating a diversity instruction so successive ticks steer AWAY from what failed.

# What a fresh tick should try INSTEAD (rotated off the prior-tick count). Nexus's
# stochastic-injection set: new approach / decompose / recombine.
_DIVERSITY = (
    "Try a COMPLETELY DIFFERENT formalization than the prior attempts — do not restate them.",
    "DECOMPOSE the goal: state the core fact as its own lemma and build the target from it.",
    "COMBINE the soundest parts of the prior attempts into one cleaner statement.",
)



# non-verdict families: nothing to LEARN from (a win, a budget cutoff, or retryable infra)
_LESSON_SKIP = {"seeded", "budget", "infra", "infra_indeterminate"}




def load_prior_lessons(history_path: str) -> dict[int, dict]:
    """Issue → a compressed CROSS-TICK post-mortem from its latest current-architecture
    refill record: `{family, last_gate, last_detail, gates_tried, prior_ticks}`. The next
    tick's intent prompt cites it (an informed retry, not a blind one) and rotates a
    diversity nudge off `prior_ticks` (item K). Records whose family is a non-verdict
    (`_LESSON_SKIP` — a win / budget cutoff / retryable infra) yield no lesson and RETIRE
    a stale one for that issue. Foreign-architecture records are ignored, like the
    families; absent/junk-tolerant."""
    out: dict[int, dict] = {}
    ticks: dict[int, int] = {}
    try:
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("issue") is None \
                        or rec.get("arch") != ROUTING_ARCH:
                    continue
                iss = int(rec["issue"])
                fam = classify_refill(rec)
                if fam in _LESSON_SKIP:
                    out.pop(iss, None)        # a later win/non-verdict retires the lesson
                    ticks.pop(iss, None)
                    continue
                ticks[iss] = ticks.get(iss, 0) + 1
                hist = rec.get("history", []) or []
                gates: list[str] = []
                for row in hist:
                    g = row.get("gate")
                    if g and g not in gates:
                        gates.append(g)
                last = hist[-1] if hist else {}
                out[iss] = {"family": fam, "last_gate": last.get("gate", ""),
                            "last_detail": (last.get("detail") or "")[:200],
                            "gates_tried": gates, "prior_ticks": ticks[iss]}
    except OSError:
        pass
    return out




def render_prior_lessons(lesson: dict) -> str:
    """A compact PRIOR-ATTEMPTS note for the drafter's intent prompt: what failed across
    ticks + a rotating diversity nudge (item K). Turns a blind cross-tick retry into an
    informed one."""
    div = _DIVERSITY[lesson.get("prior_ticks", 0) % len(_DIVERSITY)]
    gates = ", ".join(lesson.get("gates_tried") or []) or "?"
    parts = [f"PRIOR ATTEMPTS on this issue failed (family: {lesson.get('family', '?')}; "
             f"gates hit: {gates})."]
    if lesson.get("last_detail"):
        parts.append(f"Last rejection ({lesson.get('last_gate', '?')}): {lesson['last_detail']}.")
    parts.append("Do NOT repeat those. " + div)
    return " ".join(parts)




def route_for(issue: dict, *, def_count: int, family: str | None) -> str:
    """`theorem` (statement can consume existing pointer defs) or `defs` (the
    library lacks the primitives — draft definitions + the theorem). Runtime
    evidence beats static measurement: #53 measures consumable via chooserPrice,
    but its faithful statement can't use it, so its depth-exhaustion routes it."""
    if family == "needs_primitives":
        return "defs"
    if def_count == 0:
        return "defs"
    return "theorem"




def resolve_route(cli_route: str | None, issue: dict, *, def_count: int,
                  family: str | None) -> str:
    """An explicit `--route` overrides the automatic `route_for` classifier
    (operator + test affordance); `None` falls back to the classifier. Lets a
    def-rich-pointer target whose faithful statement needs a NEW primitive (the
    #73 Omega-ratio class) be driven straight to defs, skipping the wasted
    theorem tick the static `def_count` heuristic otherwise forces."""
    return cli_route or route_for(issue, def_count=def_count, family=family)




# families that failed for non-primitives reasons under the CURRENT architecture:
# fresh issues attempt first; these lemons go to the back of their route group
# (the first CI run burned ~100k tokens re-attempting #61's empty-reply furnace
# at position 2 while def-rich #108 sat unattempted at position 6).
_DEMOTED_FAMILIES = {"undraftable", "fidelity", "statement_wrong", "trivial_restatement"}




def order_by_route(issues: list[dict]) -> list[dict]:
    """Attempt order: fresh issues before current-arch lemons, easier difficulty
    first, then MORE pointer consumables (a def-richer context gives the drafter
    more to consume), then issue number. The ROUTE deliberately does NOT rank:
    it selects the PATH, not the priority — a needs_primitives issue carries
    positive evidence + hints (run 2 would otherwise have parked the evidenced
    defs backlog behind ~45 untested theorem issues for ~15 ticks). Stable ties."""
    def key(i: dict):
        return (1 if i.get("family") in _DEMOTED_FAMILIES else 0,
                difficulty_rank(i.get("difficulty")),
                -int(i.get("def_count", 0) or 0),
                i.get("number", 0))
    return sorted(issues, key=key)
