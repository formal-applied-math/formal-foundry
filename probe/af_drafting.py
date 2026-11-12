"""af_drafting — extracted from autoformalize.py; see it for the pipeline overview."""
from __future__ import annotations

import json
import os
import re
import subprocess

from af_parse import *  # noqa: F401,F403
from af_prompts import *  # noqa: F401,F403
from domain_pack import DomainPack

__all__ = ['_JSON_FENCE_RE', '_extract_json', 'parse_verdict', 'judge_faithfulness', 'intent_reject_reason', 'parse_intent', '_CLAUDE_CAP_MARKERS', 'ClaudeCapError', '_claude_draft_args', 'claude_draft_fn', 'claude_chat_fn', 'claude_auth_present', 'draft_intent', 'intent_contract_reason', 'loogle_candidates']



# --- claude reply parsers ----------------------------------------------------

_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)




def _extract_json(text: str) -> dict | None:
    """First a ```json fenced object, else the first `{…}` span; None if neither
    parses."""
    candidates = []
    m = _JSON_FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1))
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i:
        candidates.append(text[i:j + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            continue
    return None




def parse_verdict(reply: str) -> dict:
    """Parse a judge/roundtrip reply's JSON verdict. Fails CLOSED: an unparseable
    reply (or one lacking `faithful`) is treated as NOT faithful, so an unverified
    statement is never shipped."""
    v = _extract_json(reply)
    if not isinstance(v, dict) or "faithful" not in v:
        return {"faithful": False, "verdict": "unparseable judge reply", "issues": []}
    return v




def judge_faithfulness(pack: DomainPack, issue: dict, stub: str, *, chat_fn,
                       deferred: list[str] | None = None) -> dict:
    """Semantic judge: does the stub say what the issue asks? A declared-subset
    (`deferred` — the facts the drafter intentionally left for follow-up issues) is
    judged against the subset it claims, not dinged for the omission. Returns the
    verdict dict plus `tokens`."""
    content, tokens = chat_fn(judge_messages(pack, issue, stub, deferred))
    v = parse_verdict(content)
    v["tokens"] = tokens
    return v




def intent_reject_reason(reply: str) -> str | None:
    """WHY a Claude intent reply is unusable, or None if it parses. The emit is
    mechanical, so it needs a `statement` plus the naming meta (`module_name`,
    `benchmark_id`); this pinpoints which piece is missing so a rejected draft is
    DIAGNOSABLE in the telemetry — the old blanket "no parseable intent reply"
    fired identically for no-JSON and each missing field (#109 r1; #66/#88/#73)."""
    v = _extract_json(reply)
    if not isinstance(v, dict):
        return "no JSON object in reply"
    for field in ("statement", "module_name", "benchmark_id"):
        if not v.get(field):
            return f"JSON missing '{field}'"
    return None




def parse_intent(reply: str) -> dict | None:
    """Parse a Claude intent reply into a dict, or None if `intent_reject_reason`
    finds it unusable (missing JSON or any required field)."""
    if intent_reject_reason(reply) is not None:
        return None
    v = _extract_json(reply)
    v.setdefault("objects", [])
    v.setdefault("docstring", "")
    v.setdefault("deferred", [])
    v.setdefault("definitions", [])
    return v




# --- the frontier draft engine (item I): a `claude -p` adapter ----------------
# `claude -p --output-format json` runs a headless completion; with tools DISABLED it
# is a pure text generator: `messages -> (text, tokens)`, the chat contract every caller
# here takes. Claude is the drafter (intent + agentic formalize) and the judge. Design:
# docs/superpowers/specs/2026-07-24-frontier-drafter-design.md.
_CLAUDE_CAP_MARKERS = ("usage limit", "rate limit", "limit reached", "quota", "capacity",
                       # auth/expiry — a dead or missing subscription token degrades like a
                       # cap (the target is deferred) instead of erroring the tick
                       "login expired", "please run /login", "invalid api key",
                       "authentication", "unauthorized", "not logged in")




class ClaudeCapError(RuntimeError):
    """The claude.ai subscription hit a usage cap for this window. The tick defers the
    target (requeue, no obstruction) — distinct from a real draft failure."""




#: Linux refuses a single argv string over 128 KiB. A system prompt near that (the house
#: doctrine is 134 KB against the v2 library) goes in as a file instead.
_ARGV_SAFE_BYTES = 96 * 1024


def _claude_draft_args(messages: list[dict], *, model: str = "",
                       system_file: str | None = None) -> tuple[list[str], str]:
    """Build the `claude -p` argv + stdin from chat `messages`. System messages become
    `--append-system-prompt` (or `--append-system-prompt-file system_file`, when the
    caller has written them to one); the user content is the stdin prompt (avoids
    arg-length limits on big context packs). Tools DISABLED (`--allowedTools ""`) → a pure
    completion, no file/bash/agentic side effects. Returns (argv, stdin)."""
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    user = "\n\n".join(m["content"] for m in messages if m.get("role") != "system")
    argv = ["claude", "-p", "--output-format", "json", "--allowedTools", ""]
    if model:
        argv += ["--model", model]
    if system and system_file:
        argv += ["--append-system-prompt-file", system_file]
    elif system:
        argv += ["--append-system-prompt", system]
    return argv, user




def claude_draft_fn(messages: list[dict], *, model: str = "", run_fn=None) -> tuple[str, int]:
    """The `claude -p` chat adapter: `messages -> (text, tokens)`. `run_fn(argv, stdin)` is
    injectable for tests (defaults to `subprocess.run`). Raises `ClaudeCapError` on a
    subscription cap so the tick can defer."""
    if run_fn is None:
        from redact import agent_env

        def run_fn(argv, stdin):
            # only Claude's own credential (the refill runs with GH_TOKEN = MAIN_PR_TOKEN)
            return subprocess.run(argv, input=stdin, capture_output=True, env=agent_env("claude"),
                                  text=True, timeout=600)
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    system_file = None
    if len(system.encode("utf-8")) > _ARGV_SAFE_BYTES:
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".system.txt", delete=False,
                                         encoding="utf-8") as f:
            f.write(system)
            system_file = f.name
    try:
        argv, stdin = _claude_draft_args(messages, model=model, system_file=system_file)
        res = run_fn(argv, stdin)
    finally:
        if system_file:
            try:
                os.unlink(system_file)
            except OSError:
                pass
    out = (getattr(res, "stdout", "") or "").strip()
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        err = (getattr(res, "stderr", "") or out or "")[:400]
        if any(m in err.lower() for m in _CLAUDE_CAP_MARKERS):
            raise ClaudeCapError(err)
        raise RuntimeError(f"claude -p produced no JSON: {err}")
    if data.get("is_error") or data.get("subtype") != "success":
        msg = str(data.get("result") or data.get("subtype") or "")
        if any(m in msg.lower() for m in _CLAUDE_CAP_MARKERS):
            raise ClaudeCapError(msg)
        raise RuntimeError(f"claude -p error: {msg[:400]}")
    usage = data.get("usage") or {}
    tokens = int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0)
    return data.get("result", ""), tokens




def claude_chat_fn(drafter):
    """The `claude -p` chat fn bound to the configured model. ONE claude does every general-
    reasoner role — intent, the faithfulness JUDGE, the kernel probes and the decompose
    split. A subscription cap raises `ClaudeCapError`, which refill defers."""
    def call(msgs):
        return claude_draft_fn(msgs, model=drafter.claude_model)
    return call




def claude_auth_present(env=None) -> bool:
    """Is Claude usable here? A CI token (`CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY`) or a
    local login (`~/.claude/.credentials.json`). Claude is the only drafter+judge, so when this
    is False the tick warns and every draft/judge call defers — there is no fallback drafter."""
    env = os.environ if env is None else env
    if env.get("CLAUDE_CODE_OAUTH_TOKEN") or env.get("ANTHROPIC_API_KEY"):
        return True
    return os.path.exists(os.path.expanduser("~/.claude/.credentials.json"))




def draft_intent(pack: DomainPack, issue: dict, context_pack: str, *, chat_fn,
                 feedback: str | None = None,
                 route: str = "theorem", prior_unknowns: list[str] | None = None,
                 prior_lessons: str | None = None,
                 drafter_preamble: str = "") -> dict:
    """Stage 1: Claude SPECIFIES the intended statement (prose + objects + naming meta) from the
    issue. No Lean. `feedback` (a `render_gate_feedback` block from a rejected previous attempt)
    turns this into a REVISION round; `route="defs"` adds the new-definitions contract, with
    `prior_unknowns` (declarations earlier drafts guessed at) as hints. `prior_lessons` (item K)
    is a cross-TICK post-mortem + diversity nudge from failed prior ticks. Returns
    `{ok, intent, tokens}`."""
    content, tokens = chat_fn(intent_messages(pack, issue, context_pack, feedback,
                                              route=route, prior_unknowns=prior_unknowns,
                                              prior_lessons=prior_lessons,
                                              drafter_preamble=drafter_preamble))
    intent = parse_intent(content)
    reason = None if intent is not None else intent_reject_reason(content)
    if intent is not None:
        reason = intent_contract_reason(pack, issue, intent)
        if reason is not None:
            intent = None
    return {"ok": intent is not None, "intent": intent, "tokens": tokens, "reason": reason}


def intent_contract_reason(pack: DomainPack, issue: dict, intent: dict) -> str | None:
    """WHY a parsed intent cannot be emitted into THIS library, or None.

    The checks are the target's own gates, asked at the cheapest moment — before a
    formalize session and a prove session are paid for a module its build will refuse:
    the corpus keeps entry ids unique, and a library with a bibliography wants every
    module docstring and corpus entry to cite a work in it. (A missing `title` is not
    refused: the emitter heads the docstring with the issue's own title.) Each is active only
    when `prepare_issues` attached the fact it checks against, so an issue prepared
    without a checkout is not held to a contract nobody could read."""
    bid = str(intent.get("benchmark_id") or "")
    if bid in set(issue.get("taken_ids") or ()):
        return (f"benchmark_id `{bid}` is already an entry in the corpus; ids are unique "
                "— name this result's own id")
    bib = issue.get("bibliography")
    if bib is not None:
        cite = intent.get("citation")
        key = str(cite.get("key") or "").strip() if isinstance(cite, dict) else ""
        if not key:
            return ("JSON missing 'citation': {\"key\": <a bibliography key from the list>, "
                    "\"locator\": <chapter/section, or \"\">}")
        if key not in bib:
            return (f"citation key `{key}` is not in the library's bibliography; choose the "
                    "key of a listed work that states this result")
    return None





def loogle_candidates(name: str, *, main_repo: str, run_fn=None) -> str:
    """Loogle hits for `name` via `scripts/loogle.sh` — UNVERIFIED candidates (the public index
    tracks a newer Mathlib than our pin; the elaborator gates bad ones). Returns candidate text or
    ''. `run_fn` injectable for tests."""
    if run_fn is None:
        def run_fn(nm):
            try:
                out = subprocess.run([os.path.join(main_repo, "scripts", "loogle.sh"), nm],
                                     capture_output=True, text=True, timeout=20)
                return out.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                return ""
    return run_fn(name)




