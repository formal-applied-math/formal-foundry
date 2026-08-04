"""Proof-state extraction, normalization, and content-addressing.

`gate_cache` content-addresses whole STATEMENTS (the adversarial gate goals). This
module goes one level down, to the INTERMEDIATE states of an accepted proof: the
repeated work in a corpus like ours is not whole theorems (those are unique by
construction) but the short, formulaic subgoals that recur across targets — most of
all on decompose leaves, which are deliberately small.

Two jobs:

1. `state_key` — a canonical address for a pretty-printed Lean goal. This is the part
   that decides whether any of it works. A raw hash of goal text nearly never hits:
   the same state prints with different metavariable numbers (`?m.1234`) and different
   inaccessible-name daggers (`h✝¹`) depending on the path that reached it. We
   normalize exactly those, and deliberately nothing else — see `normalize_goal`.
2. `extract_states` — replay an accepted proof prefix by prefix against the daemon,
   reading the goal at a spliced `sorry`. Each prefix yields a verified
   `(state, tactic-that-came-next)` pair: the state the tactic acted on, and what
   advanced it. That is both the measurement corpus and the cache's contents.

Pure stdlib. The daemon arrives as an injected `check_fn` (the `gate`/`strengthen`
convention), so everything here is unit-testable with no Lean.
"""
from __future__ import annotations

import hashlib
import re

# Inaccessible hypothesis names print with a dagger and an optional superscript index
# (`x✝`, `h✝¹`, `h✝²`). The index counts shadowed binders, so it varies with the route
# taken to the state while the state itself is the same.
_INACCESSIBLE = re.compile(r"✝[¹²³⁴⁵⁶⁷⁸⁹⁰]*")
# Metavariable and universe-metavariable numbering is allocation order, not content.
_METAVAR = re.compile(r"\?(m|u)\.\d+")
# A `sorry` splice needs the block's own indentation to stay inside the tactic block.
_MAX_REPLAY_STEPS = 40


def normalize_goal(goal: str) -> str:
    """Canonicalize a pretty-printed goal so that states differing only by print-time
    accidents share an address.

    Normalized: inaccessible-name daggers, metavariable/universe numbering, trailing
    whitespace, blank lines.

    NOT normalized, on purpose:

    - *accessible* hypothesis names (`h` vs `hp`). Merging them needs real alpha-renaming
      with substitution into the target; a text-level approximation would merge states
      that are genuinely different and then serve a tactic that does not apply. A missed
      hit costs one re-proof; a false hit costs a wrong suggestion in the context pack.
    - hypothesis ORDER, which is semantic under dependent types (`b : Fin a` cannot
      precede `a : ℕ`).
    """
    text = _INACCESSIBLE.sub("", goal)
    text = _METAVAR.sub(r"?\1", text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def state_key(goal: str) -> str | None:
    """The content address of a goal state — sha256 of its normalization. `None` for an
    empty state, so callers never index a cache on nothing."""
    normalized = normalize_goal(goal or "")
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _tactic_block(candidate: str) -> tuple[str, str] | None:
    """Split a candidate into (everything through `:= by`, the tactic block). None when
    the proof is a term, which has no intermediate states to extract."""
    marker = ":= by"
    idx = candidate.find(marker)
    if idx < 0:
        return None
    head = candidate[: idx + len(marker)]
    body = candidate[idx + len(marker):]
    return head, body.lstrip("\n") if body.startswith("\n") else body


def _base_indent(block: str) -> str:
    """The indentation of the tactic block's own top level, as a prefix string. Falls
    back to two spaces for a block whose first line is the tail of the `:= by` line."""
    for line in block.splitlines():
        if line.strip():
            width = len(line) - len(line.lstrip())
            if width:
                return " " * width
    return "  "


def split_tactic_steps(block: str) -> list[str]:
    """Top-level tactic steps of a block. A line indented deeper than the block's base
    belongs to the step above it (a nested `by`, a `case`, a multi-line `calc`), so it is
    folded into that step rather than treated as its own. Comment and blank lines drop."""
    raw = [ln for ln in block.splitlines() if ln.strip()]
    lines = [ln for ln in raw if not ln.strip().startswith("--")]
    if not lines:
        return []
    base = min(len(ln) - len(ln.lstrip()) for ln in lines)
    steps: list[str] = []
    for line in lines:
        indent = len(line) - len(line.lstrip())
        if indent == base or not steps:
            steps.append(line.strip() if indent == base else line)
        else:
            steps[-1] += "\n" + line
    return steps


def extract_states(candidate: str, *, check_fn, max_steps: int = _MAX_REPLAY_STEPS,
                   log=lambda _m: None) -> list[dict]:
    """Replay `candidate` prefix by prefix, reading the goal at a spliced `sorry`.

    For each `k`, elaborating `steps[:k] ++ sorry` reports the state that `steps[k]` is
    about to act on, giving a verified `(state, tactic)` pair. Prefixes the daemon cannot
    elaborate — mid-`have`, a `<;>` combinator that does not survive truncation, a step
    that closed the goal so there is no `sorry` left — yield no goal and are skipped;
    extraction is best-effort by design and never fails the caller.

    Costs one elaboration per step, on the daemon the gate phase already owns, so it is
    capped at `max_steps`.
    """
    split = _tactic_block(candidate)
    if split is None:
        return []
    head, block = split
    steps = split_tactic_steps(block)[:max_steps]
    if not steps:
        return []
    indent = _base_indent(block)
    pairs: list[dict] = []
    for k, tactic in enumerate(steps):
        # Re-indent each replayed step. `split_tactic_steps` strips the base indent off a
        # step's head line (continuation lines keep their absolute indent), so emitting
        # steps verbatim puts them at column 0 — outside the tactic block. Lean then
        # reports a parse error instead of a goal and every prefix after the first is
        # silently lost. Found against the daemon, not in unit tests.
        replayed = [indent + step if not step.startswith(" ") else step
                    for step in steps[:k]]
        probe = head + "\n" + "\n".join(replayed + [indent + "sorry"])
        try:
            result = check_fn(probe) or {}
        except Exception as exc:                       # a wedged daemon must not abort a pass
            log(f"state extraction stopped at step {k}: {type(exc).__name__}: {exc}")
            break
        if result.get("error"):                        # infra sentinel — stop, do not guess
            log(f"state extraction stopped at step {k}: {result['error']}")
            break
        sorries = result.get("sorries") or []
        goal = sorries[0].get("goal") if sorries and isinstance(sorries[0], dict) else None
        key = state_key(goal or "")
        if key is None:
            continue
        pairs.append({"key": key, "state": goal, "tactic": tactic, "step": k})
    return pairs
