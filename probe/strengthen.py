"""strengthen — the NECESSITY prober (backlog item R).

Complements `autoformalize.strengthen_candidate`, which drops hypotheses the finished
proof never *used*, keyed on the elaborator's unused-variable warnings. That pass is
sound and already shipped — and it does not catch the class measured on
formal-mathfin#161/#162, because in both drafts the proof **does** use the guard:

    (h : 0 < ∑ s ∈ S, max (-(r s)) 0)   …   have hden := h.le ; exact div_nonneg hnum hden
    (h : ∑ s ∈ up, b s ≠ 0)             …   field_simp [h]

No unused-variable warning fires, and deleting the binder while keeping the proof just
breaks it. The hypothesis is used *by this proof* and unnecessary *for the theorem* —
`div_nonneg` needs only `0 ≤` on both legs, and `mul_div_assoc` needs nothing at all.
Distinguishing those two requires re-proving without it, which is what this module does:

    drop the binder → does the statement still elaborate? (free filter, no prover)
                    → can the prover close the reduced statement? (one bounded round)

A hypothesis that survives both is genuinely load-bearing. One that does not yields a
strictly stronger theorem *with a proof in hand*, so this is a strengthening pass rather
than a rejection — a target that trips it is improved, never killed.

Ordering matters for cost: run the warning-driven pass first (free), then this one on
what remains. The free filter means data/dependency binders — `{ι : Type*}` that
`(s : Finset ι)` needs — cost zero prover tokens, since dropping them fails to
elaborate immediately.

Fails open throughout: any daemon error, unparseable header, or red re-gate keeps the
proved original. Refuses to judge a candidate that still contains `sorry`.
"""
from __future__ import annotations

import re

__all__ = ["droppable_hypotheses", "necessity_probe", "unnecessary_hypotheses",
           "tactic_sweep_prover", "SWEEP_TACTICS"]

_SORRY = re.compile(r"\bsorry\b")

# The re-prove runs in the GATE phase, where the daemon holds the Lean slot and the
# vibe harness is down (they are mutually exclusive). So "can the prover close the
# reduced statement?" is answered by a fixed tactic sweep rather than a model call:
# zero tokens, no phase flip, and every tactic here is one the house rules allow in a
# merged source (no `?`-suggestion tactics, no hammer). Ordered cheap → strong.
#
# TRACED against the two cases this gate was built for (2026-07-31, daemon, v4.32.0):
#   #161 `0 ≤ gainToPain s r`         — bare `positivity` FAILS ("failed to prove
#     positivity/nonnegativity"); `unfold gainToPain; positivity` CLOSES it. The unfold
#     is load-bearing: positivity cannot see through the def to the two `posPart`/
#     `negPart` sums underneath.
#   #162 `upCapture up (c • p) b = c * upCapture up p b` — `simp [upCapture]` leaves
#     `(∑ c * p x) / ∑ b i = c * ((∑ p i) / ∑ b i)`: it unfolds the `•` but does not pull
#     the scalar out of the sum or associate the division. Hence the ALGEBRA slot below,
#     carrying the two rewrites the merged proof needs (`← Finset.mul_sum`,
#     `mul_div_assoc`). Without it the sweep did not fire on half the cases that
#     motivated the gate.
# Both rewrites are generic ring/BigOperators facts, so the slot stays general — it is
# not the #162 proof pasted in.
SWEEP_TACTICS = (
    "positivity",
    "simp [{defs}]",
    "unfold {unfold}; positivity",
    "simp [{defs}, ← Finset.mul_sum, mul_div_assoc]",
    "simp [{defs}]; positivity",
    "field_simp [{defs}]; ring",
    "unfold {unfold}; field_simp; ring",
    "grind",
)


def tactic_sweep_prover(check_fn, def_names=(), tactics=SWEEP_TACTICS):
    """A `prove_fn` for `unnecessary_hypotheses` that tries a fixed tactic sweep
    against the daemon instead of calling a model. `def_names` are the module's own
    definitions, spliced into the `simp`/`unfold` slots so a statement about a new
    def can be unfolded to the Mathlib lemmas underneath. Zero tokens by
    construction; returns the first closing attempt, else the untouched probe."""
    defs = ", ".join(def_names)
    unfold = " ".join(def_names)

    def prove(probe: str) -> dict:
        for t in tactics:
            if ("{defs}" in t and not defs) or ("{unfold}" in t and not unfold):
                continue
            attempt = _SORRY.sub(t.format(defs=defs, unfold=unfold), probe, count=1)
            res = check_fn(attempt)
            if res.get("error"):
                break                      # daemon trouble — stop, keep the original
            if not res.get("errors") and res.get("sorry_count", 0) == 0:
                return {"lean_text": attempt, "tokens": 0}
        return {"lean_text": probe, "tokens": 0}

    return prove


def necessity_probe(candidate: str, thm_name: str, names: set[str]) -> str | None:
    """`candidate` with the named explicit hypotheses dropped and the theorem's proof
    replaced by `sorry` — the statement alone, for the prover to attempt afresh.
    None when the declaration cannot be located or nothing was dropped."""
    from autoformalize import _locate_named, remove_explicit_binders
    try:
        bstart, sep, end = _locate_named(candidate, thm_name)
    except ValueError:
        return None
    new_binders, dropped = remove_explicit_binders(candidate[bstart:sep], set(names))
    if not dropped:
        return None
    return candidate[:bstart] + new_binders + candidate[sep:end] + ":= by sorry\n"


def droppable_hypotheses(candidate: str, thm_name: str, *, check_fn) -> list[str]:
    """The explicit hypotheses whose removal leaves a statement that still ELABORATES
    (with `sorry` for a proof). This is the free filter: a binder the rest of the
    signature depends on fails here and never reaches the prover. Returns names in
    signature order; empty on any parse or daemon trouble."""
    from autoformalize import _binder_groups, _locate_named
    try:
        bstart, sep, _end = _locate_named(candidate, thm_name)
    except ValueError:
        return []
    out = []
    for _s, _e, opener, names in _binder_groups(candidate[bstart:sep]):
        if opener != "(":            # implicit/instance binders are not hypotheses
            continue
        for nm in names:
            probe = necessity_probe(candidate, thm_name, {nm})
            if probe is None:
                continue
            res = check_fn(probe)
            if res.get("error"):     # daemon trouble is not a verdict
                return []
            if not res.get("errors"):
                out.append(nm)
        continue
    return out


def unnecessary_hypotheses(candidate: str, thm_name: str, *, check_fn, prove_fn,
                           regate_fn, log=lambda m: None) -> dict:
    """Drop each droppable hypothesis and ask the prover to close the reduced statement.

    `prove_fn(lean_text) -> {"lean_text": …, "tokens": n}` is one bounded prover round
    on the `sorry`-carrying probe. A hypothesis is *unnecessary* when the prover closes
    it AND the full gate passes on the result — the same bar the original had to clear,
    so a strengthened statement is never held to a weaker standard.

    Greedy and re-verified: each accepted drop becomes the new baseline, so the returned
    candidate is always a state that passed the gate. Returns
    `{candidate, dropped, tokens, changed}`."""
    out = {"candidate": candidate, "dropped": [], "tokens": 0, "changed": False}
    if _SORRY.search(candidate):
        log("necessity: candidate still has a sorry — skipped")
        return out
    current, dropped, tokens = candidate, [], 0
    for nm in droppable_hypotheses(candidate, thm_name, check_fn=check_fn):
        probe = necessity_probe(current, thm_name, {nm})
        if probe is None:
            continue
        try:
            attempt = prove_fn(probe)
        except Exception as exc:                      # infra, never a verdict
            log(f"necessity: prover call failed on {nm} ({exc}); keeping it")
            break
        tokens += int(attempt.get("tokens") or 0)
        proved = attempt.get("lean_text") or ""
        if not proved or _SORRY.search(proved):
            continue                                  # genuinely load-bearing
        g = regate_fn(proved)
        if not g.get("passed"):
            log(f"necessity: {nm} looked droppable but the re-gate went red "
                f"({g.get('reason')}); keeping it")
            continue
        log(f"necessity: `{nm}` was not needed — re-proved without it")
        current, dropped = proved, dropped + [nm]
    return {"candidate": current, "dropped": dropped, "tokens": tokens,
            "changed": bool(dropped)}
