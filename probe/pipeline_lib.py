"""Token-paced scheduling logic for the autoformalizer pipeline (pure, stdlib).

The GitHub Actions cron fires `pipeline-tick.sh` on a fixed cadence (one issue
every 3 days). Each tick asks this library three questions, all pure functions
of a config + a small JSON state so they are unit-testable with no Lean / GitHub:

  1. Is it due?              `due(state, cfg, now_epoch)`
  2. Can we afford it?       `can_afford(state, cfg)`  (monthly token allowance)
  3. What's the target?      `next_target(candidates, state)`

and after the run records what happened: `record_attempt(...)`.

Time is always passed in explicitly (never read here) so the logic is
deterministic under test. `now_epoch` is Unix seconds; `now_ym` is "YYYY-MM".

Config lives in `pipeline.toml`; state in `pipeline_state.json`.
"""

from __future__ import annotations

import dataclasses
import json
import os

try:  # py3.11+ stdlib
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


SECONDS_PER_DAY = 86400


@dataclasses.dataclass(frozen=True)
class PipelineConfig:
    interval_days: int = 3
    monthly_token_allowance: int = 8_000_000
    tokens_per_issue_cap: int = 500_000
    escalate_hard_cap: int = 2_000_000
    max_issues_per_tick: int = 1
    reasoning_effort: str = "high"
    # pass@k harness knobs (research: Kimina knee ~pass@32, Goedel ~2 repair
    # rounds, Leanstral's lever is tokens-PER-attempt). The per-issue cap is
    # spent as ~fanout attempts x tokens_per_attempt, then <=repair_rounds
    # compiler-feedback repairs on the best failure.
    fanout: int = 8
    repair_rounds: int = 2
    tokens_per_attempt: int = 60_000

    @staticmethod
    def load(path: str | None) -> "PipelineConfig":
        if not path or not os.path.isfile(path) or tomllib is None:
            return PipelineConfig()
        with open(path, "rb") as f:
            data = tomllib.load(f)
        section = data.get("pipeline", data)
        fields = {f.name for f in dataclasses.fields(PipelineConfig)}
        return PipelineConfig(**{k: v for k, v in section.items() if k in fields})


@dataclasses.dataclass(frozen=True)
class AutoformalizeConfig:
    """Config for the issue->stub refill phase (the `[autoformalize]` block).

    The tick runs `refill` when the queue has no unattempted target: magistral
    drafts+judges+roundtrips a stub from the next ready issue, leanstral gates it.
    `enabled=false` reverts the pipeline to a hand-seeded queue.
    """
    enabled: bool = True
    budget: int = 200_000
    max_issues: int = 1
    max_attempt_issues: int = 3
    gate_budget: int = 20_000
    draft_model: str = "magistral-medium-latest"
    prover_model: str = "labs-leanstral-1-5"
    draft_max_tokens: int = 8_000

    @staticmethod
    def load(path: str | None) -> "AutoformalizeConfig":
        if not path or not os.path.isfile(path) or tomllib is None:
            return AutoformalizeConfig()
        with open(path, "rb") as f:
            data = tomllib.load(f)
        section = data.get("autoformalize", {})
        fields = {f.name for f in dataclasses.fields(AutoformalizeConfig)}
        return AutoformalizeConfig(**{k: v for k, v in section.items() if k in fields})


def new_state(now_ym: str = "", now_epoch: int = 0) -> dict:
    return {
        "month": now_ym,
        "tokens_spent_this_month": 0,
        "attempted_issues": [],
        "last_tick_epoch": 0,
        "history": [],
    }


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        return new_state()
    # tolerate partial states written by older ticks
    base = new_state()
    base.update(s)
    return base


def save_state(path: str, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def roll_month(state: dict, now_ym: str) -> dict:
    """Reset the monthly token counter when the calendar month rolls over."""
    if state.get("month") != now_ym:
        state = dict(state)
        state["month"] = now_ym
        state["tokens_spent_this_month"] = 0
    return state


def budget_remaining(state: dict, cfg: PipelineConfig) -> int:
    return max(0, cfg.monthly_token_allowance - int(state.get("tokens_spent_this_month", 0)))


def issue_budget(cfg: PipelineConfig, difficulty: str | None = None) -> int:
    """Per-issue token cap; difficulty:hard escalates to the hard cap."""
    if difficulty and "hard" in difficulty.lower():
        return cfg.escalate_hard_cap
    return cfg.tokens_per_issue_cap


def can_afford(state: dict, cfg: PipelineConfig, difficulty: str | None = None) -> bool:
    """Enough monthly allowance left to run at least one issue at its cap."""
    return budget_remaining(state, cfg) >= issue_budget(cfg, difficulty)


def due(state: dict, cfg: PipelineConfig, now_epoch: int) -> bool:
    """Has interval_days elapsed since the last tick? (Guards manual+scheduled
    double-fires; a zero last_tick — fresh state — is always due.)"""
    last = int(state.get("last_tick_epoch", 0))
    if last <= 0:
        return True
    return (now_epoch - last) >= cfg.interval_days * SECONDS_PER_DAY


def next_target(candidates: list[dict], state: dict) -> dict | None:
    """First candidate whose id is not already attempted. `candidates` is the
    tick-assembled, ordered work list (backlog queue first, then textbook);
    each item is a dict carrying at least an 'id'."""
    attempted = set(state.get("attempted_issues", []))
    for c in candidates:
        if c.get("id") not in attempted:
            return c
    return None


def record_attempt(state: dict, target_id: str, tokens: int, outcome: str,
                   now_epoch: int, now_ym: str) -> dict:
    """Charge the tokens, mark the target attempted, stamp the tick. Returns a
    new state (rolls the month first so a month-boundary tick starts clean)."""
    state = roll_month(state, now_ym)
    state = dict(state)
    state["attempted_issues"] = list(state.get("attempted_issues", [])) + [target_id]
    state["tokens_spent_this_month"] = int(state.get("tokens_spent_this_month", 0)) + int(tokens)
    state["last_tick_epoch"] = int(now_epoch)
    state["history"] = list(state.get("history", [])) + [
        {"id": target_id, "tokens": int(tokens), "outcome": outcome, "epoch": int(now_epoch)}
    ]
    return state
