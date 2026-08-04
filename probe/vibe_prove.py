"""Prove a queued target with the trained-for vibe ⇄ lean-lsp-mcp harness.

Replaces the text-loop probe on the cron path (see
`docs/superpowers/specs/2026-07-17-leanstral-vibe-cron-harness-design.md`).
Leanstral 1.5 was RL-trained to DRIVE lean-lsp-mcp tools (live `lean_goal`,
`lean_multi_attempt`, on-demand search); here we run one deep headless vibe
session per target instead of pasting compiler error strings into
`/chat/completions`.

Mechanics (W0-validated):
- vibe edits files on the HOST from its CWD; the MCP reads goals from `/app` in
  the container. So we run vibe with CWD = the main repo and materialize the stub
  as `MathFin/<stem>.lean`, which is the same bind-mounted file on both sides.
- the stub is a throwaway scratch file; the captured proof is gated (daemon) and
  assembled into its real module downstream by open-pr.sh. We delete the scratch
  before the tick flips the Lean slot back to the daemon.

This module does the LSP-phase (produce a raw candidate). The daemon-phase gate
(`gate.gate`) runs separately, after the tick flips back to the daemon.
"""

from __future__ import annotations

import json
import os
import re
import subprocess


def sanitize_stem(target_id: str) -> str:
    """A safe scratch-module stem for a target id (`cal-bk-67` → `_Autoform_cal_bk_67`)."""
    return "_Autoform_" + re.sub(r"[^A-Za-z0-9]", "_", target_id)


def scratch_paths(main_repo: str, target_id: str) -> tuple[str, str]:
    """(host absolute path, path relative to the main repo / container `/app`)."""
    stem = sanitize_stem(target_id)
    host = os.path.join(main_repo, "MathFin", stem + ".lean")
    rel = f"MathFin/{stem}.lean"
    return host, rel


def build_vibe_task(stub_relpath: str, sorry_name: str, context_pack: str = "",
                    state_hints: str = "") -> str:
    """The `-p` task for vibe. leanstral-vibe.sh prepends the house doctrine, so this
    is the per-target instruction + the consume-don't-reprove pointer pack only.

    `state_hints` is the proof-state cache's contribution: `goal -> tactic` pairs that
    closed the same state while proving a DIFFERENT target. Empty unless states have
    actually recurred across targets, so an unproven cache stays silent."""
    parts = [
        f"TASK: The file {stub_relpath} contains `theorem {sorry_name}` with a single `sorry`.",
        f"Prove it. Use `lean_goal` on {stub_relpath} to read the proof state, then edit that "
        "file so it compiles with NO `sorry` and NO errors. Use `lean_multi_attempt` for cheap "
        "tactic fan-out and the search tools (`lean_loogle`/`lean_leansearch`/`lean_state_search`) "
        "to find existing lemmas.",
        "Do NOT change the theorem statement, name, or binders. Consume existing results rather "
        "than reproving them. Stop once the file compiles clean.",
    ]
    if context_pack:
        parts.append("\n── EXISTING RESULTS TO CONSUME (do not reprove) ──\n" + context_pack)
    if state_hints:
        parts.append("\n── PROOF STATES ALREADY SEEN (a tactic that closed each; verify, "
                     "do not trust) ──\n" + state_hints)
    return "\n".join(parts)


def record_proof_states(candidate: str, *, target_id: str, check_fn, cache,
                        log_path: str, log=lambda _m: None) -> dict:
    """Extract the accepted proof's intermediate states and record them.

    Runs in the gate phase, which already owns the Lean slot, and strictly AFTER the
    candidate passed. It is measurement plus cache fill, never a verdict: any failure
    (dead daemon, unparseable proof, a raising socket) degrades to zero states rather
    than turning a green target red.

    Writes one JSONL row per sighting for offline analysis, and returns the counts the
    tick's summary row carries.
    """
    from proof_states import extract_states
    from probe_lib import append_jsonl
    try:
        pairs = extract_states(candidate, check_fn=check_fn, log=log)
    except Exception as exc:                            # never fail a passing target
        log(f"state extraction failed: {type(exc).__name__}: {exc}")
        return {"states": 0, "new_states": 0}
    for pair in pairs:
        append_jsonl(log_path, {"target": target_id, "key": pair["key"],
                                "state": pair["state"], "tactic": pair["tactic"],
                                "step": pair["step"]})
    return {"states": len(pairs), "new_states": cache.ingest(pairs, target=target_id)}


def read_back(host_path: str) -> str | None:
    try:
        with open(host_path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def run_vibe_target(target: dict, *, main_repo: str, context_pack: str, max_turns: int,
                    vibe_script: str, run_fn=subprocess.run, state_hints: str = "") -> str | None:
    """Materialize the stub → one headless vibe session (CWD=main_repo) → capture the
    edited file → delete the scratch. Returns the captured file content (or None).
    `run_fn` is injected (subprocess.run) so this is unit-testable without vibe/docker.
    leanstral-vibe.sh brings the lean-lsp service up (daemon down) and injects the
    house doctrine; the caller flips the Lean slot back to the daemon afterwards."""
    host, rel = scratch_paths(main_repo, target["id"])
    os.makedirs(os.path.dirname(host), exist_ok=True)
    with open(host, "w", encoding="utf-8") as f:
        f.write(target["statement"])
    try:
        task = build_vibe_task(rel, target["sorry_name"], context_pack, state_hints)
        run_fn([vibe_script, "--agent", "lean", "--auto-approve",
                "--max-turns", str(max_turns), "-p", task],
               cwd=main_repo, check=False)
        return read_back(host)
    finally:
        try:
            os.remove(host)
        except OSError:
            pass


# --- CLI: two phases around the tick's daemon↔lsp flip -----------------------
# `run`  (lean-lsp up): produce runs/<tag>-<id>.candidate  [heavy deps lazy-imported]
# `gate` (daemon up):   verify it → runs/<tag>-<id>.lean + summary row

def _iter_targets(manifest_path, only):
    import json
    manifest = json.load(open(manifest_path))
    root = os.path.dirname(os.path.abspath(manifest_path))
    for target in manifest["targets"]:
        if only and target["id"] != only:
            continue
        if target.get("kind") != "prove":
            continue
        yield target, root


def _run_dir():
    foundry_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(foundry_root, "runs")
    os.makedirs(d, exist_ok=True)
    return foundry_root, d


def _state_cache(run_dir: str, config_path: str | None):
    """The proof-state cache, or None when `[autoformalize].state_cache` is off.
    Persisted next to gate-cache.json so the tick's persist step commits it and the
    store accumulates across ticks."""
    from pipeline_lib import AutoformalizeConfig
    if not AutoformalizeConfig.load(config_path).state_cache:
        return None
    from state_cache import StateCache
    return StateCache(os.path.join(run_dir, "state-cache.json"))


def _cmd_run(args) -> int:
    from house_context import extract_signatures
    foundry_root, run_dir = _run_dir()
    vibe_script = os.path.join(foundry_root, "scripts", "leanstral-vibe.sh")
    cache = _state_cache(run_dir, getattr(args, "config", None))
    # Cross-target `goal -> tactic` pairs. Empty string until states have recurred, so
    # a cold or unproductive cache changes the prompt not at all.
    state_hints = cache.suggestions() if cache is not None else ""
    if state_hints:
        print(f"[vibe-run] state cache: offering {state_hints.count('-- goal:')} "
              f"cross-target suggestion(s)", flush=True)
    for target, root in _iter_targets(args.manifest, args.only):
        target["statement"] = open(os.path.join(root, target["file"]), encoding="utf-8").read()
        pointers = target.get("pointers", [])
        context_pack = extract_signatures(args.main_repo, pointers) if pointers else ""
        cand = run_vibe_target(target, main_repo=args.main_repo, context_pack=context_pack,
                               max_turns=args.max_turns, vibe_script=vibe_script,
                               state_hints=state_hints)
        cand_path = os.path.join(run_dir, f"{args.run_tag}-{target['id']}.candidate")
        with open(cand_path, "w", encoding="utf-8") as f:
            f.write(cand or "")
        removed = bool(cand) and "sorry" not in cand
        print(f"[vibe-run] {target['id']}: captured {len(cand or '')} bytes, "
              f"sorry_removed={removed}", flush=True)
    return 0


def _cmd_gate(args) -> int:
    import time

    from autoformalize import (golf_candidate, strengthen_candidate,
                                trim_unused_imports, trim_unused_opens)
    from gate import gate as run_gate
    from probe import daemon_check, mistral_chat
    from probe_lib import append_jsonl
    _, run_dir = _run_dir()
    summary_log = os.path.join(run_dir, f"{args.run_tag}-summary.jsonl")
    state_cache = _state_cache(run_dir, getattr(args, "config", None))
    for target, _root in _iter_targets(args.manifest, args.only):
        cand_path = os.path.join(run_dir, f"{args.run_tag}-{target['id']}.candidate")
        candidate = read_back(cand_path)
        # item J: the ORIGINAL stub statement, to pin the accepted proof to what was asked
        # (the prover is told not to touch the statement/binders; this enforces it). None
        # if the scratch stub is gone — the pin then fails open to the kernel bar.
        stub = read_back(os.path.join(_root, target["file"]))
        summary = {"target": target["id"], "stream": target.get("stream", ""),
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "harness": "vibe",
                   "arm": getattr(args, "arm", "cron"), "tokens": 0}
        if not candidate:
            summary["outcome"] = "error"          # infra miss (no capture) → retryable
        elif "sorry" in candidate:
            summary["outcome"] = "max_rounds"      # vibe ran but didn't close it → record, move on
        else:
            g = run_gate(candidate, target["sorry_name"], check_fn=daemon_check, statement=stub)
            if g["passed"]:
                # strengthen: drop hypotheses the proof never used (elaborator
                # warnings), re-gate; fail-open keeps the proved original. The
                # stripped re-export entry becomes a RUN artifact open-pr prefers
                # — the queue stays immutable (zombie doctrine).
                entry = target.get("benchmark_entry") or {}
                snippet = (entry.get("code") or {}).get("lean")
                s = strengthen_candidate(
                    candidate, snippet, target["sorry_name"], g.get("warnings"),
                    regate_fn=lambda c: run_gate(c, target["sorry_name"],
                                                 check_fn=daemon_check),
                    log=lambda m: print(f"[vibe-gate] {target['id']}: {m}", flush=True))
                if s["stripped"]:
                    candidate = s["candidate"]
                    summary["stripped_hypotheses"] = s["stripped"]
                    if s["entry_code"]:
                        e2 = json.loads(json.dumps(entry))
                        e2["code"]["lean"] = s["entry_code"]
                        e2.setdefault("metadata", {}).setdefault(
                            "provenance", {})["stripped_hypotheses"] = s["stripped"]
                        override = os.path.join(
                            run_dir, f"{args.run_tag}-{target['id']}.entry.json")
                        with open(override, "w", encoding="utf-8") as f:
                            json.dump(e2, f, ensure_ascii=False, indent=2)
                # drop pointer imports the module never needed (elab-verified per
                # necessity (item R): the pass above drops hypotheses the proof never
                # USED — elaborator warnings. This one drops hypotheses the theorem
                # does not NEED, which is a different set: on formal-mathfin#161/#162
                # all four drafts genuinely consumed their guard (`h.le`,
                # `field_simp [h]`), so no warning fired, and the statement was true
                # without it anyway. Re-proves the reduced statement with a tactic
                # sweep — the gate phase owns the Lean slot and the vibe harness is
                # down, so this stays daemon-only and costs zero prover tokens.
                if os.environ.get("NECESSITY", "1") != "0":
                    from strengthen import tactic_sweep_prover, unnecessary_hypotheses
                    defs = list((target.get("new_defs") or []))
                    nec = unnecessary_hypotheses(
                        candidate, target["sorry_name"], check_fn=daemon_check,
                        prove_fn=tactic_sweep_prover(daemon_check, defs),
                        regate_fn=lambda c: run_gate(c, target["sorry_name"],
                                                     check_fn=daemon_check),
                        log=lambda m: print(f"[vibe-gate] {target['id']}: {m}", flush=True))
                    if nec["changed"]:
                        candidate = nec["candidate"]
                        summary["unnecessary_hypotheses"] = nec["dropped"]
                # drop pointer imports the module never needed (elab-verified per
                # removal); one full re-gate guards against instance-resolution
                # drift, reverting the trim wholesale if anything changed.
                t = trim_unused_imports(candidate, check_fn=daemon_check)
                if t["removed"]:
                    g3 = run_gate(t["candidate"], target["sorry_name"],
                                  check_fn=daemon_check)
                    if g3["passed"]:
                        candidate = t["candidate"]
                        summary["trimmed_imports"] = t["removed"]
                        print(f"[vibe-gate] {target['id']}: trimmed unused "
                              f"import(s) {t['removed']}", flush=True)
                # item V: the house preamble is opened unconditionally at emit (a
                # missing open is a silent bare-name death, an unused one is not).
                # The module has elaborated by now, so the trade is settled — prune
                # what it demonstrably does not use, same subtractive shape.
                o = trim_unused_opens(candidate, check_fn=daemon_check)
                if o["removed"]:
                    g4 = run_gate(o["candidate"], target["sorry_name"],
                                  check_fn=daemon_check)
                    if g4["passed"]:
                        candidate = o["candidate"]
                        summary["trimmed_opens"] = o["removed"]
                        print(f"[vibe-gate] {target['id']}: trimmed unused "
                              f"open(s) {o['removed']}", flush=True)
                # golf: the prover polishes its own accepted proof to the house
                # register (proof-only edits enforced by signature equality + a
                # full re-gate; fail-open). GOLF=0 disables the experiment.
                if os.environ.get("GOLF", "1") != "0" and os.environ.get("MISTRAL_API_KEY"):
                    gf = golf_candidate(
                        candidate,
                        chat_fn=lambda msgs: mistral_chat(
                            msgs, api_key=os.environ["MISTRAL_API_KEY"]),
                        regate_fn=lambda c: run_gate(c, target["sorry_name"],
                                                     check_fn=daemon_check),
                        log=lambda m: print(f"[vibe-gate] {target['id']}: {m}",
                                            flush=True))
                    if gf["golfed"]:
                        candidate = gf["candidate"]
                        summary["golfed"] = True
                summary["outcome"] = "pass"
                summary["axioms_clean"] = True
                win = os.path.join(run_dir, f"{args.run_tag}-{target['id']}.lean")
                with open(win, "w", encoding="utf-8") as f:
                    f.write(candidate)
                # Proof-state recording: the candidate is final and gated, and this
                # phase owns the Lean slot. Strictly additive — it cannot change the
                # verdict already written above.
                if state_cache is not None:
                    counts = record_proof_states(
                        candidate, target_id=target["id"], check_fn=daemon_check,
                        cache=state_cache,
                        log_path=os.path.join(run_dir, "proof-states.jsonl"),
                        log=lambda m: print(f"[vibe-gate] {target['id']}: {m}", flush=True))
                    summary.update(proof_states=counts["states"],
                                   new_proof_states=counts["new_states"])
                    print(f"[vibe-gate] {target['id']}: recorded {counts['states']} "
                          f"proof state(s), {counts['new_states']} new", flush=True)
            else:
                summary["outcome"] = "fail_gate"
                summary["gate_reason"] = g["reason"]
        append_jsonl(summary_log, summary)
        print(f"[vibe-gate] {target['id']}: {summary['outcome']}"
              + (f" ({summary.get('gate_reason')})" if summary.get("gate_reason") else ""),
              flush=True)
    return 0


def _cmd_states(args) -> int:
    """The measurement, read straight off the store. `cross_target_states` is the number
    that decides whether consuming the cache is justified: states reached while proving
    two or more DIFFERENT targets are reusable work; everything else is a proof
    revisiting its own goal."""
    from state_cache import StateCache
    _, run_dir = _run_dir()
    cache = StateCache(os.path.join(run_dir, "state-cache.json"))
    report = cache.report()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print("proof-state recurrence")
    print("======================")
    print(f"distinct states      : {report['distinct_states']}")
    print(f"total sightings      : {report['total_sightings']}")
    print(f"recurring (>1 sight) : {report['recurring_states']}")
    print(f"CROSS-TARGET states  : {report['cross_target_states']}")
    if not report["distinct_states"]:
        print("\nstore empty — run some ticks with [autoformalize].state_cache = true")
    elif not report["cross_target_states"]:
        print("\nno state has been reached by two different targets yet, so there is no "
              "reusable work to serve. suggestions() stays silent until there is.")
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="vibe ⇄ lean-lsp-mcp prove harness (two phases)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--manifest", required=True)
    common.add_argument("--only", default=None)
    common.add_argument("--run-tag", required=True)
    common.add_argument("--main-repo", default="/home/rapha/code/automated_proofs_quantfin")
    # A/B scoreboard arm (Task 2.6): the plain cron path is "cron"; the decompose driver
    # passes "decompose" for its leaf runs. Both Mistral — there is no centaur/claude arm.
    common.add_argument("--arm", default="cron", choices=["cron", "decompose"])
    # pipeline.toml, for `[autoformalize].state_cache`. Absent ⇒ the feature is off and
    # both phases behave byte-identically to before it existed.
    common.add_argument("--config", default=None)
    pr = sub.add_parser("run", parents=[common], help="LSP phase: headless vibe → .candidate")
    pr.add_argument("--max-turns", type=int, default=40)
    sub.add_parser("gate", parents=[common], help="daemon phase: verify .candidate → .lean + summary")
    rp = sub.add_parser("states", help="proof-state recurrence report (no daemon, no tokens)")
    rp.add_argument("--config", default=None)
    rp.add_argument("--json", action="store_true", help="emit the raw report dict")
    args = ap.parse_args()
    if args.cmd == "states":
        return _cmd_states(args)
    return _cmd_run(args) if args.cmd == "run" else _cmd_gate(args)


if __name__ == "__main__":
    import sys
    sys.exit(main())
