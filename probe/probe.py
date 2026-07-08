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

from probe_lib import (
    TokenLedger,
    append_jsonl,
    axiom_guard_block,
    build_initial_prompt,
    build_repair_prompt,
    extract_lean_code,
    slop_report,
    window_messages,
)

DEFAULT_MODEL = "labs-leanstral-1-5"
DEFAULT_BASE_URL = "https://api.mistral.ai/v1"


def mistral_chat(messages, *, api_key, model=DEFAULT_MODEL,
                 base_url=DEFAULT_BASE_URL, max_tokens=16384,
                 temperature=0.7, timeout=600):
    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")
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
            content = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return content, tokens
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError(
                    "401 from Mistral API — check MISTRAL_API_KEY") from e
            if e.code in (429, 500, 502, 503) and attempt < 3:
                wait = 5 * (2 ** attempt)
                print(f"  [chat] HTTP {e.code}, retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("unreachable")


def daemon_check(code: str, *, host="127.0.0.1", port=7878, timeout=600) -> dict:
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
    return json.loads(b"".join(chunks).decode("utf-8"))


def run_target(target: dict, *, budget: int, max_rounds: int,
               chat_fn, check_fn, log_fn) -> dict:
    ledger = TokenLedger(budget)
    code = target["statement"]
    messages = [{"role": "user", "content": build_initial_prompt(code)}]
    t0 = time.time()
    outcome, rounds, last_slop, axioms_clean = "max_rounds", 0, None, None

    for rnd in range(1, max_rounds + 1):
        if ledger.exhausted:
            outcome = "budget_exhausted"
            break
        rounds = rnd
        content, tokens = chat_fn(window_messages(messages))
        ledger.add(tokens)
        candidate = extract_lean_code(content)
        if candidate is None:
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content":
                          "No ```lean block found. Output the COMPLETE file "
                          "in a single ```lean block."}]
            continue
        last_slop = slop_report(candidate)
        if last_slop["forbidden"]:
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content":
                          f"Forbidden constructs used: {last_slop['forbidden']}. "
                          "Rewrite the proof without them. COMPLETE file, one "
                          "```lean block."}]
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
            axioms_clean = bool(guard["success"])
            outcome = "pass" if axioms_clean else "axiom_dirty"
            target["_winning_candidate"] = candidate
            break
        messages += [{"role": "assistant", "content": content},
                     {"role": "user",
                      "content": build_repair_prompt(result["errors"])}]
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
    args = ap.parse_args()

    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("MISTRAL_API_KEY not set", file=sys.stderr)
        return 2

    manifest = json.load(open(args.manifest))
    root = os.path.dirname(os.path.abspath(args.manifest))
    run_dir = os.path.join(os.path.dirname(root), "runs")
    attempts_log = os.path.join(run_dir, f"{args.run_tag}-attempts.jsonl")
    summary_log = os.path.join(run_dir, f"{args.run_tag}-summary.jsonl")

    def chat_fn(messages):
        return mistral_chat(messages, api_key=api_key, model=args.model,
                            base_url=args.base_url)

    for target in manifest["targets"]:
        if args.only and target["id"] != args.only:
            continue
        if target.get("kind") != "prove":
            continue
        target["statement"] = open(os.path.join(root, target["file"])).read()
        print(f"[{target['id']}] budget={args.budget} …", flush=True)
        summary = run_target(target, budget=args.budget,
                             max_rounds=args.max_rounds, chat_fn=chat_fn,
                             check_fn=daemon_check,
                             log_fn=lambda r: append_jsonl(attempts_log, r))
        summary["model"] = args.model
        summary["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        append_jsonl(summary_log, summary)
        print(f"  -> {summary['outcome']} rounds={summary['rounds']} "
              f"tokens={summary['tokens']}", flush=True)
        if "_winning_candidate" in target:
            win_path = os.path.join(run_dir,
                                    f"{args.run_tag}-{target['id']}.lean")
            with open(win_path, "w") as f:
                f.write(target["_winning_candidate"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
