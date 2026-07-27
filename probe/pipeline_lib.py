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
    # vibe ⇄ lean-lsp-mcp harness (the cron prove path since 2026-07-17): one deep
    # agentic session per target, bounded by turns (depth over breadth) — the lever
    # that replaced the retired text-loop's fanout×tokens budget. (The legacy
    # calibration probe carries its own defaults; it no longer reads this config.)
    max_turns: int = 40

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
    prover_model: str = "labs-leanstral-1-5"   # leanstral: the kernel gate battery
    # pointers-scoped depth gate: reject a true-but-shallow stub whose TYPE consumes
    # no def from its `-- pointers:` MathFin modules (a Mathlib identity in domain
    # clothing). `false` disables it (rely on the kernel/judge gates + human merge).
    depth_gate: bool = True
    formalize_rounds: int = 3   # claude completion-formalize + compiler-feedback repair rounds
    retrieval: bool = True      # loogle-augmented repair on `unknown identifier X`
    formalize_token_budget: int = 40_000   # early-abort a doomed formalization (a hard issue
                                           # like #61 else burns ~77k/draw grinding all rounds)
    # embedding premise retrieval (pin-accurate types.jsonl) with loogle fallback.
    retrieval_backend: str = "embedding"   # "embedding" | "loogle"
    retrieval_k: int = 8                    # top-k premises surfaced per query
    embed_model: str = "mistral-embed"      # Mistral /v1/embeddings model id
    # semantic repair cascade (design: 2026-07-17-semantic-repair-cascade): a semantic
    # gate rejection (shallow/trivial/vacuous/false/unfaithful/drift) re-drafts BOTH
    # stages with the gate verdict as feedback, up to semantic_rounds total attempts
    # per issue (1 = the old terminal-skip behavior). triviality_gate splices
    # `first | rfl | simp` over the sorry at draft time (the cal-bk-67 rfl class).
    semantic_rounds: int = 2
    triviality_gate: bool = True
    # item L: content-address the adversarial gate goals (vacuity/disproof) and substitute
    # the cached verdict on a hit, across attempts + ticks. Off by default (the census is
    # not yet volume-bound); enable once the queue is large enough to pay back.
    gate_cache: bool = False

    @staticmethod
    def load(path: str | None) -> "AutoformalizeConfig":
        if not path or not os.path.isfile(path) or tomllib is None:
            return AutoformalizeConfig()
        with open(path, "rb") as f:
            data = tomllib.load(f)
        section = data.get("autoformalize", {})
        fields = {f.name for f in dataclasses.fields(AutoformalizeConfig)}
        return AutoformalizeConfig(**{k: v for k, v in section.items() if k in fields})


@dataclasses.dataclass(frozen=True)
class DecomposeConfig:
    """Config for the lemma-DAG decompose path (the `[decompose]` block). OFF BY DEFAULT
    and tag-only (R decision 2026-07-18): only a target tagged `decompose` takes the path,
    and only when `enabled`, so the running cron is byte-identical until it is flipped on.
    Design: docs/superpowers/specs/2026-07-18-decomposer-design.md."""
    enabled: bool = False
    max_leaves: int = 3        # tight splits first; a DAG wanting more is usually mis-shaped
    leaf_max_turns: int = 40   # vibe turns per leaf (leaves are individually short by design)
    max_reask: int = 1         # bounded re-decompose rounds on a malformed DAG / failed skeleton gate

    @staticmethod
    def load(path: str | None) -> "DecomposeConfig":
        if not path or not os.path.isfile(path) or tomllib is None:
            return DecomposeConfig()
        with open(path, "rb") as f:
            data = tomllib.load(f)
        section = data.get("decompose", {})
        fields = {f.name for f in dataclasses.fields(DecomposeConfig)}
        return DecomposeConfig(**{k: v for k, v in section.items() if k in fields})


@dataclasses.dataclass(frozen=True)
class DrafterConfig:
    """Config for the DRAFT stage (the `[drafter]` block). Claude drafts the intent,
    formalizes agentically (`claude -p` + lean-lsp, self-validating to elaboration), and
    JUDGES; Leanstral PROVES. The only knob is which Claude model drafts. A subscription cap
    defers the target (there is no fallback drafter — Claude is the only one)."""
    claude_model: str = "claude-sonnet-5"   # `claude -p --model`; the CLI default (fable) is too weak to draft

    @staticmethod
    def load(path: str | None) -> "DrafterConfig":
        if not path or not os.path.isfile(path) or tomllib is None:
            return DrafterConfig()
        with open(path, "rb") as f:
            data = tomllib.load(f)
        section = data.get("drafter", {})
        fields = {f.name for f in dataclasses.fields(DrafterConfig)}
        return DrafterConfig(**{k: v for k, v in section.items() if k in fields})


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
