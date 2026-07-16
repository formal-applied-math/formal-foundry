"""Leanstral calibration probe: prove targets via Mistral API + lean-repl daemon.

Usage:
  export MISTRAL_API_KEY=...   # or put in ../.env and `source` it
  python3 probe.py prove --manifest ../targets/manifest.json --budget 50000 \
      --run-tag tier1 [--only cal-bk-1] [--max-rounds 8]

The daemon must be up (from the MAIN repo root):
  docker compose -f docker/docker-compose.yml up -d lean-repl
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

import embed as _embed
from autop import autop_prove, scout_rescue
from house_context import build_system_prompt, extract_signatures
from probe_lib import (
    TokenLedger,
    append_jsonl,
    axiom_guard_block,
    best_failure,
    build_initial_prompt,
    build_repair_prompt,
    extract_lean_code,
    normalize_content,
    slop_report,
    window_messages,
)
from scout_index import default_index_dir

# Reconfirmed 2026-07-11 against docs.mistral.ai: `labs-leanstral-1-5` is the
# live Leanstral 1.5 id on api.mistral.ai/v1 (free until 2026-09-30). The older
# `labs-leanstral-2603` was retired 2026-06-30 — do not target it.
DEFAULT_MODEL = "labs-leanstral-1-5"
DEFAULT_BASE_URL = "https://api.mistral.ai/v1"


def mistral_chat(messages, *, api_key, model=DEFAULT_MODEL,
                 base_url=DEFAULT_BASE_URL, max_tokens=16384,
                 temperature=0.7, timeout=240, reasoning_effort=None):
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # Leanstral supports reasoning_effort ("high" for hard proofs, "none" for
    # speed); only send it when set so non-reasoning models aren't disturbed.
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = normalize_content(data["choices"][0]["message"]["content"])
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return content, tokens
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8")[:500]
            except Exception:
                pass
            if e.code == 401:
                raise RuntimeError(
                    f"401 from Mistral API — check MISTRAL_API_KEY. {body}") from e
            if e.code in (429, 500, 502, 503) and attempt < 3:
                wait = 5 * (2 ** attempt)
                print(f"  [chat] HTTP {e.code}, retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code} from Mistral API: {body}") from e
        except (ValueError, KeyError) as e:
            # empty / truncated / malformed response body (seen on the free tier
            # under rate pressure: a 200 with an empty payload → json.loads(''))
            # — treat like a transient 5xx and retry with backoff.
            if attempt < 3:
                wait = 5 * (2 ** attempt)
                print(f"  [chat] bad response body ({type(e).__name__}), retry in {wait}s",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            raise RuntimeError(f"unparseable Mistral response after retries: {e}") from e
        except (TimeoutError, urllib.error.URLError) as e:
            # a hung / timed-out call or transient network error — retry with backoff,
            # so ONE slow call can't stall the whole tick (a hard issue must fail fast
            # and yield to easier ones, not eat the run).
            if attempt < 3:
                wait = 5 * (2 ** attempt)
                print(f"  [chat] {type(e).__name__}, retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Mistral API {type(e).__name__} after retries: {e}") from e
    raise RuntimeError("unreachable")


def _parse_daemon_response(raw: bytes) -> dict:
    """Parse the daemon's JSON reply. A degraded daemon (OOM-restarted mid-request
    under load) can return an empty or truncated payload — surface that as an error
    dict rather than raising, so callers (run_target / draft_with_repair) treat it
    as a failed check and retry instead of crashing the target."""
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return {"success": False, "sorry_count": 0,
                "errors": [f"daemon returned an empty or malformed response: {e}"]}


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
        # connection. Surface it as a FAILED check — exactly like a malformed
        # payload — so the gate/draft treats it as a failed candidate and moves on,
        # never an uncaught TimeoutError that skips the whole issue mid-tick.
        return {"success": False, "sorry_count": 0,
                "errors": [f"daemon check did not complete: {type(e).__name__}: {e}"]}
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


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prove")
    p.add_argument("--manifest", required=True)
    p.add_argument("--budget", type=int, required=True)
    p.add_argument("--max-rounds", type=int, default=8)
    p.add_argument("--only", default=None)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--run-tag", required=True)
    p.add_argument("--main-repo", default="/home/rapha/code/automated_proofs_quantfin",
                   help="main repo root, for the live house-doctrine system prompt + pins")
    p.add_argument("--reasoning-effort", default=None,
                   choices=[None, "none", "low", "medium", "high"],
                   help="Leanstral reasoning_effort; 'high' for hard targets")
    p.add_argument("--fanout", type=int, default=1,
                   help="pass@k whole-proof candidates sampled per round (1 = sequential)")
    p.add_argument("--repair-rounds", type=int, default=None,
                   help="max compiler-feedback repair rounds (default: --max-rounds)")
    p.add_argument("--max-tokens", type=int, default=16384,
                   help="max_tokens per attempt (Leanstral's lever is tokens-per-attempt)")
    p.add_argument("--retrieval-backend", default="embedding", choices=["embedding", "loogle"])
    p.add_argument("--retrieval-k", type=int, default=8)
    p.add_argument("--embed-model", default="mistral-embed")
    p.add_argument("--autop", dest="autop", action="store_true", default=True)
    p.add_argument("--no-autop", dest="autop", action="store_false")
    args = ap.parse_args()

    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("MISTRAL_API_KEY not set", file=sys.stderr)
        return 2

    system_prompt = build_system_prompt(args.main_repo)
    manifest = json.load(open(args.manifest))
    root = os.path.dirname(os.path.abspath(args.manifest))
    # runs/ lives at the FOUNDRY root (where pipeline-tick.sh writes + reads the
    # candidate), not relative to the manifest — the manifest can sit at any depth
    # (targets/queue/manifest.json). Derive it from this file's location + ensure it.
    foundry_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run_dir = os.path.join(foundry_root, "runs")
    os.makedirs(run_dir, exist_ok=True)
    attempts_log = os.path.join(run_dir, f"{args.run_tag}-attempts.jsonl")
    summary_log = os.path.join(run_dir, f"{args.run_tag}-summary.jsonl")

    def chat_fn(messages):
        return mistral_chat(messages, api_key=api_key, model=args.model,
                            base_url=args.base_url, max_tokens=args.max_tokens,
                            reasoning_effort=args.reasoning_effort)

    index_dir = default_index_dir()
    _premises = _embed.load_premises(index_dir)
    _eidx = (_embed.EmbeddingIndex.load(_embed.cache_path(index_dir, args.embed_model),
                                        _premises, args.embed_model)
             if (args.retrieval_backend == "embedding" and _premises) else None)
    if args.retrieval_backend == "embedding" and _eidx is None:
        print("[prove] embedding index absent → no prove-side premise retrieval", flush=True)
    elif args.retrieval_backend != "embedding":
        print(f"[prove] retrieval-backend={args.retrieval_backend} → prove-side premise "
              "retrieval is embedding-only; none injected", flush=True)

    def _retrieve_premises(statement):
        if _eidx is None:
            return ""
        ef = lambda t: _embed.mistral_embed(t, api_key=api_key, model=args.embed_model)  # noqa: E731
        return _eidx.retrieve(statement, args.retrieval_k, ef)

    for target in manifest["targets"]:
        if args.only and target["id"] != args.only:
            continue
        if target.get("kind") != "prove":
            continue
        target["statement"] = open(os.path.join(root, target["file"])).read()
        pointers = target.get("pointers", [])
        context_pack = extract_signatures(args.main_repo, pointers) if pointers else ""
        premises = _retrieve_premises(target["statement"])
        if premises:
            context_pack += ("\n── LIKELY-RELEVANT PREMISES (cosine-ranked; consume, "
                             "don't reprove) ──\n" + premises)
        print(f"[{target['id']}] budget={args.budget} pointers={len(pointers)} "
              f"premises={'y' if premises else 'n'} autop={'y' if args.autop else 'n'} …",
              flush=True)

        # autop probe (evidence + scout safety net); leanstral still runs for an
        # AUTHOR proof — autop never reduces leanstral effort, only rescues misses.
        autop_res = autop_prove(target["statement"], check_fn=daemon_check) if args.autop else None

        summary = run_target(target, budget=args.budget,
                             max_rounds=args.max_rounds, chat_fn=chat_fn,
                             check_fn=daemon_check,
                             log_fn=lambda r: append_jsonl(attempts_log, r),
                             system_prompt=system_prompt, context_pack=context_pack,
                             fanout=args.fanout, repair_rounds=args.repair_rounds)
        summary["model"] = args.model
        summary["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        summary["autop"] = autop_res["tactic"] if autop_res else None   # prove-wall evidence
        summary["scout"] = False
        rescue = scout_rescue(summary["outcome"] == "pass", autop_res)
        if rescue:
            # leanstral missed but a cheap tactic closes it → SCOUT rescue (draft PR)
            target["_winning_candidate"] = rescue["winning_candidate"]
            target["_proof_source"] = rescue["proof_source"]
            summary["outcome"] = "pass_scout"
            summary["scout"] = True
        append_jsonl(summary_log, summary)
        print(f"  -> {summary['outcome']} rounds={summary['rounds']} "
              f"tokens={summary['tokens']} autop={summary['autop']}", flush=True)
        if "_winning_candidate" in target:
            win_path = os.path.join(run_dir, f"{args.run_tag}-{target['id']}.lean")
            with open(win_path, "w") as f:
                f.write(target["_winning_candidate"])
            if target.get("_proof_source"):
                with open(win_path + ".scout", "w") as f:
                    f.write(target["_proof_source"])
            else:
                try:
                    os.remove(win_path + ".scout")   # author proof: clear any stale scout sidecar
                except FileNotFoundError:
                    pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
