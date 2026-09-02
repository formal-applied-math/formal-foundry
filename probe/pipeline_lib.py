"""Token-paced scheduling logic for the autoformalizer pipeline (pure, stdlib).

The GitHub Actions cron fires `pipeline-tick.sh` on a fixed cadence (one issue
every 2 days). Each tick asks this library three questions, all pure functions
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

# The cron fires at a fixed wall-clock minute, but `last_tick_epoch` is stamped when
# the run RECORDS — i.e. fire time PLUS the run's duration (live ticks take 45-85 min;
# the job's ceiling is 120). Measured against a whole number of days the next firing
# therefore lands just SHORT of the interval and skips, silently halving the cadence
# (a 2-day cron would tick every 4th day). Give the due check the job's full timeout
# as slack. A same-interval manual re-fire is still guarded, and `--force` bypasses
# the check outright.
DUE_GRACE_SECONDS = 4 * 3600


@dataclasses.dataclass(frozen=True)
class PipelineConfig:
    interval_days: int = 2
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

    The tick runs `refill` when the queue has no unattempted target: claude
    drafts+judges a stub from the next ready issue, leanstral gates it. (No
    roundtrip back-translation — that is backlog item G, still unbuilt; both
    docstrings claimed it until 2026-08-06.)
    `enabled=false` reverts the pipeline to a hand-seeded queue.
    """
    enabled: bool = True
    budget: int = 200_000
    max_issues: int = 1
    max_attempt_issues: int = 3
    gate_budget: int = 20_000
    prover_model: str = "labs-leanstral-1-5"   # leanstral: the kernel gate battery
    # pointers-scoped depth gate: reject a true-but-shallow stub whose TYPE consumes
    # no def from its `-- pointers:` library modules (a Mathlib identity in domain
    # clothing). `false` disables it (rely on the kernel/judge gates + human merge).
    depth_gate: bool = True
    formalize_rounds: int = 3   # round-count cited in the formalize-miss telemetry (agentic self-iterates)
    retrieval: bool = True      # feed embedding-retrieved premises into the agentic drafter prompt
    # embedding premise retrieval (pin-accurate types.jsonl) with loogle fallback.
    retrieval_backend: str = "embedding"   # "embedding" | "loogle"
    retrieval_k: int = 8                    # top-k premises surfaced per query
    embed_model: str = "mistral-embed"      # Mistral /v1/embeddings model id
    # semantic repair cascade (design: 2026-07-17-semantic-repair-cascade): a semantic
    # gate rejection (shallow/trivial/vacuous/false/unfaithful) re-drafts BOTH
    # stages with the gate verdict as feedback, up to semantic_rounds total attempts
    # per issue (1 = the old terminal-skip behavior). triviality_gate splices
    # `first | rfl | simp` over the sorry at draft time (the cal-bk-67 rfl class).
    semantic_rounds: int = 2
    triviality_gate: bool = True
    # item L: content-address the adversarial gate goals (vacuity/disproof) and substitute
    # the cached verdict on a hit, across attempts + ticks. Off by default (the census is
    # not yet volume-bound); enable once the queue is large enough to pay back.
    gate_cache: bool = False
    # The same content-addressing one level down: record the INTERMEDIATE states of an
    # accepted proof and the tactic that advanced each. Two phases in one switch — it
    # measures first (recording is unconditional once on, and costs the gate phase one
    # elaboration per tactic step on a daemon it already owns), and only CONSUMES once
    # states have actually recurred across targets, since `StateCache.suggestions()`
    # renders nothing until then. Off by default until the recurrence number comes back.
    state_cache: bool = False
    # item K: a per-target rolling notebook of FAILED attempts, folded across ticks and
    # rendered into the next attempt's prover task. Converts the retries the cron already
    # performs into informed ones; costs one small summariser call per failure and nothing
    # at all on the pass path. Off by default ⇒ prompts stay byte-identical.
    experience: bool = False

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
    """Config for the lemma-DAG decompose path (the `[decompose]` block). The dataclass
    DEFAULT is off, but `pipeline.toml` sets `enabled = true`: the path is LIVE (since
    2026-07-18, `0a2e277`) and takes three triggers — a `decompose` tag, a `decompose=true`
    workflow_dispatch one-shot, and autonomous failure-escalation (a plain prove that hits
    `max_rounds`/`fail_gate` re-routes that target here).
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
    """Has interval_days (less DUE_GRACE_SECONDS) elapsed since the last tick?
    (Guards manual+scheduled double-fires; a zero last_tick — fresh state — is
    always due.)"""
    last = int(state.get("last_tick_epoch", 0))
    if last <= 0:
        return True
    return (now_epoch - last) >= cfg.interval_days * SECONDS_PER_DAY - DUE_GRACE_SECONDS


def attempted_ids(state: dict) -> set[str]:
    """Every target this pipeline has attempted, per BOTH records that hold the fact.

    `record_attempt` appends the id to `attempted_issues` AND a row carrying it to
    `history`, so the two cannot legitimately disagree — they are one fact stored
    twice. Reading their UNION means losing either one no longer loses the answer,
    which is the failure that actually happened: `attempted_issues` dropped the
    2026-07-20 attempts, `history` still had them, nobody asked, and #161/#162 were
    re-drafted into duplicate PRs (formal-mathfin#163/#165, #164/#167).

    Cannot regress: where the two agree the union is either one of them."""
    ids = set(state.get("attempted_issues", []) or [])
    ids |= {h.get("id") for h in (state.get("history") or []) if h.get("id")}
    return ids


def selection_census(candidates: list[dict], state: dict, *, claimed_fn=None,
                     queue_dir: str | None = None) -> dict:
    """Why did selection return what it returned? Counts per exclusion reason, plus
    whether the candidate list actually covers the stubs on disk.

    A null selection has to explain itself. `no_unattempted_targets` was emitted over
    six unattempted targets because the manifest feeding `candidates` was stale — the
    message was true about its inputs and useless about reality, and it cost several
    ticks to notice."""
    attempted = attempted_ids(state)
    n_attempted = n_claimed = 0
    for c in candidates:
        if c.get("id") in attempted:
            n_attempted += 1
            continue
        if claimed_fn is not None:
            try:
                if claimed_fn(c):
                    n_claimed += 1
            except Exception:      # a backstop that raises is not a verdict
                pass
    out = {
        "candidates": len(candidates),
        "excluded_attempted": n_attempted,
        "excluded_claimed": n_claimed,
        "selectable": len(candidates) - n_attempted - n_claimed,
    }
    if queue_dir and os.path.isdir(queue_dir):
        on_disk = {f[:-5] for f in os.listdir(queue_dir) if f.endswith(".lean")}
        listed = {c.get("id") for c in candidates}
        out["stubs_on_disk"] = len(on_disk)
        # the stale-manifest signature: stubs exist that the candidate list omits
        out["missing_from_candidates"] = sorted(on_disk - listed)
    return out


def next_target(candidates: list[dict], state: dict, *, claimed_fn=None) -> dict | None:
    """First candidate that is neither already attempted nor already claimed.

    `candidates` is the tick-assembled, ordered work list (backlog queue first, then
    textbook); each item is a dict carrying at least an 'id'.

    Two guards, deliberately of different kinds. `attempted_issues` is the fast path —
    a set lookup, no I/O — but it lives in a mutable file written *after* the PR is
    opened, with no transactional link to the work it guards, and it has already needed
    one repair for exactly that (`e1df178`, "recover run 29615562257's orphaned state").
    When it lost the 2026-07-20 attempts, targets #161 and #162 were re-drafted five
    days later and the pipeline opened duplicate PRs for both
    (formal-mathfin#163/#165, #164/#167).

    `claimed_fn(candidate) -> bool` is the backstop: it asks reality — is there an open
    PR for this issue, is there already a queue entry — so a target survives a lost
    state file. Optional, and failures are swallowed: an unreachable GitHub must not
    stall the tick, since the fast path is still doing its job. Note the standing
    exposure it covers: a passing tick leaves the issue `status:ready` until a human
    merges, so every target awaiting review is re-selectable for the whole window."""
    attempted = attempted_ids(state)
    for c in candidates:
        if c.get("id") in attempted:
            continue
        if claimed_fn is not None:
            try:
                if claimed_fn(c):
                    continue
            except Exception:      # ground truth is a backstop, never a blocker
                pass
        return c
    return None


# `queue_claimed` lived here and is gone. It answered "is this issue already staged
# in the queue?" — a re-DRAFT guard — but was wired into the PROVE selector, where the
# answer is yes for every candidate, because `_write_target` writes `<id>.entry.json`
# at seed time. A target was therefore born claimed and could never be proved.
#
# The question it asked is still asked, on the side that needs it and with the same
# durability property (reads the queue off disk, not `pipeline_state.json`):
# `autoformalize._already_seeded`. The prove side keeps `pr_claimed` below.


def pr_claimed(candidate: dict, *, repo: str, run_fn=None) -> bool:
    """Is there an OPEN pull request that already closes this candidate's issue?

    The ground-truth half of the duplicate guard. Asks `gh` for open PRs mentioning the
    issue number and matches a closing keyword, so a PR that merely references the issue
    in prose does not block the target. Any failure — no `gh`, no network, unparseable
    output — returns False: this is a backstop, and a broken lookup must not stop work.

    `repo` is required: it is the DOMAIN's target slug (`pack.slug`), and a default
    here would silently ask the flagship whether a second library's issue is
    claimed — always answering no, and always looking like it worked."""
    import json
    import re
    import subprocess

    num = candidate.get("issue") or candidate.get("number")
    if num is None:
        m = re.search(r"(\d+)$", str(candidate.get("id") or ""))
        if not m:
            return False
        num = int(m.group(1))
    run = run_fn or subprocess.run
    try:
        res = run(["gh", "pr", "list", "--repo", repo, "--state", "open",
                   "--json", "number,body,title", "--limit", "100"],
                  capture_output=True, text=True, check=False)
        rows = json.loads((getattr(res, "stdout", "") or "").strip() or "[]")
    except Exception:
        return False
    closes = re.compile(rf"(?i)\b(?:closes|fixes|resolves)\s+#{int(num)}\b")
    return any(closes.search((r.get("body") or "") + " " + (r.get("title") or ""))
               for r in rows)


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
