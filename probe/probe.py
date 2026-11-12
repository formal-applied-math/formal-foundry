"""The Lean daemon client and the chat-driven prove loop.

`daemon_check` sends a file to the lean-repl daemon and returns its verdict; every gate
in the pipeline takes it as `check_fn`. `run_target` is the completion-style prove loop
(pass@k sampling + bounded compiler-feedback repair) over any `chat_fn(messages) ->
(text, tokens)`: the refill's two kernel probes use it for a short attempt at `False` or
at the negated conclusion. The prove path proper is `prove_session.py`, an agentic
session with the lean-lsp tools.

The daemon must be up (from the MAIN repo root):
  docker compose -f docker/docker-compose.yml up -d lean-repl
"""

from __future__ import annotations

import json
import socket
import time

from probe_lib import (
    TokenLedger,
    axiom_guard_block,
    best_failure,
    build_initial_prompt,
    build_repair_prompt,
    extract_lean_code,
    slop_report,
    window_messages,
)


def _parse_daemon_response(raw: bytes) -> dict:
    """Parse the daemon's JSON reply. A degraded daemon (OOM-restarted mid-request
    under load) can return an empty or truncated payload — surface that as an error
    dict rather than raising, so callers (run_target / formalize_with_repair) treat it
    as a failed check and retry instead of crashing the target."""
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        # A malformed/empty payload is INFRASTRUCTURE, not a verdict about the code, so
        # it carries the `error` sentinel exactly like a socket failure does. This is
        # what a killed daemon looks like from the client: the container dies mid-reply
        # and the socket closes early. Most often that is another session flipping the
        # Lean slot (`docker compose stop lean-repl` SIGKILLs a container that does not
        # exit in time, so exit 137 says nothing about memory). Without the sentinel this
        # came back as `errors`
        # alone with `sorry_count: 0`, i.e. indistinguishable from a well-formed
        # rejection with real-looking messages — so every caller keying on `error` to
        # return INDETERMINATE instead scored a kill as a judgement on the submitted
        # Lean. `errors` is kept so the repair loop still treats it as a failed check.
        msg = f"daemon returned an empty or malformed response: {e}"
        return {"success": False, "sorry_count": 0, "error": msg, "errors": [msg]}
    # The daemon's own catch-all (a crashed REPL, its elaboration timeout, a respawn that
    # failed) answers WELL-FORMED — `{"success": false, "errors": ["daemon error: …"]}` —
    # with no `error` key, so every caller that keys on `error` read it as a real Lean
    # rejection of the submitted code. It is the daemon talking about itself.
    if isinstance(parsed, dict) and not parsed.get("error"):
        infra = [str(m) for m in (parsed.get("errors") or [])
                 if str(m).startswith(_DAEMON_SELF_REPORTS)]
        if infra:
            parsed["error"] = infra[0]
    return parsed


#: Messages the daemon uses about ITSELF (see the target library's `lean_repl.handle`),
#: as opposed to diagnostics about the code it was sent.
_DAEMON_SELF_REPORTS = ("daemon error:", "empty input")


def daemon_check(code: str, *, host="127.0.0.1", port=7878, timeout=300) -> dict:
    try:
        with socket.create_connection((host, port), timeout=30) as sock:
            sock.settimeout(timeout)
            sock.sendall(code.encode("utf-8"))
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
    except (socket.timeout, TimeoutError, OSError) as e:
        # A wedged daemon (a spinning elaboration killed server-side) or one busy
        # respawning a fresh REPL can blow the socket deadline or refuse the
        # connection. `error` (singular) is the INFRA sentinel the structural gates
        # key on to return an INDETERMINATE (retryable) verdict instead of failing
        # open and seeding a bad draft (H5); `errors` (plural) is kept so the draft
        # repair loop still treats it as a failed candidate and never sees an
        # uncaught TimeoutError that would skip the whole issue mid-tick.
        msg = f"daemon check did not complete: {type(e).__name__}: {e}"
        return {"success": False, "sorry_count": 0, "error": msg, "errors": [msg]}
    return _parse_daemon_response(b"".join(chunks))


def run_target(target: dict, *, budget: int, max_rounds: int,
               chat_fn, check_fn, log_fn, system_prompt=None, context_pack="",
               fanout: int = 1, repair_rounds: int | None = None) -> dict:
    """Prove `target` with pass@`fanout` sampling + bounded compiler-feedback
    repair. Each round samples up to `fanout` whole-proof candidates from the
    current context, checks each, and passes on the first axioms-clean success;
    otherwise it keeps the fewest-error failure (`best_failure`) and repairs it
    for up to `repair_rounds` further rounds. `fanout=1` with `repair_rounds`
    defaulting to `max_rounds` reproduces the original sequential single-sample
    loop exactly (the Kimina knee says most value is by pass@~32; the Goedel
    ablation says ~2 repair rounds is where compiler feedback pays)."""
    if repair_rounds is None:
        repair_rounds = max_rounds
    ledger = TokenLedger(budget)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    initial = build_initial_prompt(target["statement"])
    if context_pack:
        # per-target "consume, don't reprove" context: real signatures of the
        # modules the stub points at, from the lean_scout index. Kept in the
        # first user message so window_messages preserves it across repair rounds.
        initial = context_pack + "\n" + initial
    messages.append({"role": "user", "content": initial})
    t0 = time.time()
    outcome, rounds, last_slop, axioms_clean = "max_rounds", 0, None, None
    max_total_rounds = min(max_rounds, 1 + repair_rounds)

    for rnd in range(1, max_total_rounds + 1):
        if ledger.exhausted:
            outcome = "budget_exhausted"
            break
        rounds = rnd
        base = window_messages(messages)
        checked: list[dict] = []   # compiled-but-not-clean / failed: {content, errors}
        rejected: list[tuple] = []  # no-code / forbidden: (content, feedback)
        won = False

        for _ in range(fanout):
            if ledger.exhausted:
                break
            content, tokens = chat_fn(base)
            ledger.add(tokens)
            candidate = extract_lean_code(content)
            if candidate is None:
                rejected.append((content,
                                 "No ```lean block found. Output the COMPLETE file "
                                 "in a single ```lean block."))
                continue
            last_slop = slop_report(candidate)
            if last_slop["forbidden"]:
                rejected.append((content,
                                 f"Forbidden constructs used: {last_slop['forbidden']}. "
                                 "Rewrite the proof without them. COMPLETE file, one "
                                 "```lean block."))
                continue
            result = check_fn(candidate)
            log_fn({
                "target": target["id"], "stream": target["stream"], "round": rnd,
                "tokens_cum": ledger.spent, "success": result["success"],
                "errors_head": result["errors"][:3],
                "sorry_count": result.get("sorry_count", 0),
            })
            if result["success"] and result.get("sorry_count", 0) == 0:
                guard = check_fn(axiom_guard_block(candidate, target["sorry_name"]))
                if guard["success"]:
                    axioms_clean = True
                    outcome = "pass"
                    target["_winning_candidate"] = candidate
                    won = True
                    break
                axioms_clean = False
                outcome = "axiom_dirty"
                checked.append({"content": content, "errors":
                                ["proof depends on a disallowed axiom; stay within "
                                 "propext, Classical.choice, Quot.sound"]})
            else:
                checked.append({"content": content, "errors": result["errors"]})

        if won:
            break
        # set up the next repair round on the most promising failure
        if checked:
            idx = best_failure(checked)
            messages += [{"role": "assistant", "content": checked[idx]["content"]},
                         {"role": "user",
                          "content": build_repair_prompt(checked[idx]["errors"])}]
        elif rejected:
            content, feedback = rejected[-1]
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": feedback}]
        if ledger.exhausted:
            outcome = "budget_exhausted"
            break

    return {
        "target": target["id"], "stream": target["stream"],
        "outcome": outcome, "rounds": rounds, "tokens": ledger.spent,
        "budget": budget, "wall_s": round(time.time() - t0, 1),
        "axioms_clean": axioms_clean, "slop": last_slop,
    }
