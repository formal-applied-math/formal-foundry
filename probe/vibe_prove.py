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
  as `<LakeRoot>/<stem>.lean`, the same bind-mounted file on both sides.
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


def scratch_paths(pack, main_repo: str, target_id: str) -> tuple[str, str]:
    """(host absolute path, path relative to the main repo / container `/app`).
    Under the pack's Lake root, so the scratch module lands inside the target
    library rather than beside it."""
    stem = sanitize_stem(target_id)
    host = os.path.join(main_repo, pack.lake_root, stem + ".lean")
    rel = f"{pack.lake_root}/{stem}.lean"
    return host, rel


def build_vibe_task(stub_relpath: str, sorry_name: str, context_pack: str = "",
                    state_hints: str = "", experience: str = "") -> str:
    """The `-p` task for vibe. leanstral-vibe.sh prepends the house doctrine, so this
    is the per-target instruction + the consume-don't-reprove pointer pack only.

    `state_hints` is the proof-state cache's contribution: `goal -> tactic` pairs that
    closed the same state while proving a DIFFERENT target. Empty unless states have
    actually recurred across targets, so an unproven cache stays silent.

    `experience` is the rolling notebook for THIS target (item K) — what previous ticks
    tried and how they died. Empty on a target's first attempt, so a cold memory leaves
    the task byte-identical. It goes LAST, after the premises: it is the weakest-authority
    block in the prompt (a record of failures, not a source of truth), and the statement
    plus the context pack must not be read through it."""
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
    if experience:
        parts.append(experience)
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


def run_vibe_target(pack, target: dict, *, main_repo: str, context_pack: str,
                    max_turns: int,
                    vibe_script: str, run_fn=subprocess.run, state_hints: str = "",
                    experience: str = "") -> str | None:
    """Materialize the stub → one headless vibe session (CWD=main_repo) → capture the
    edited file → delete the scratch. Returns the captured file content (or None).
    `run_fn` is injected (subprocess.run) so this is unit-testable without vibe/docker.
    leanstral-vibe.sh brings the lean-lsp service up (daemon down) and injects the
    house doctrine; the caller flips the Lean slot back to the daemon afterwards."""
    host, rel = scratch_paths(pack, main_repo, target["id"])
    os.makedirs(os.path.dirname(host), exist_ok=True)
    with open(host, "w", encoding="utf-8") as f:
        f.write(target["statement"])
    try:
        task = build_vibe_task(rel, target["sorry_name"], context_pack, state_hints,
                               experience)
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


def _experience_store(run_dir: str, config_path: str | None):
    """The per-target rolling notebook, or None when `[autoformalize].experience` is off.
    Persisted next to the other two stores so the tick's persist step commits it and the
    memory accumulates across ticks (it is worthless within one)."""
    from pipeline_lib import AutoformalizeConfig
    if not AutoformalizeConfig.load(config_path).experience:
        return None
    from experience import ExperienceStore
    return ExperienceStore(os.path.join(run_dir, "experience.json"))


def _summarizer():
    """The chat backend that rolls a notebook, or None to use the mechanical digest.

    Leanstral is already the tick's authenticated model, and this is a short prose call —
    no new secret, no new dependency. `EXPERIENCE_LLM=0` forces the mechanical path, which
    is what the tests and any keyless run take.
    """
    if os.environ.get("EXPERIENCE_LLM", "1") == "0":
        return None
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        return None
    from probe import mistral_chat
    # Small, cheap, and deterministic-ish: this is bookkeeping, not proving.
    return lambda msgs: mistral_chat(msgs, api_key=api_key, max_tokens=800, temperature=0.2)


def _cmd_run(args) -> int:
    import domain_pack
    from house_context import extract_signatures
    foundry_root, run_dir = _run_dir()
    pack = domain_pack.load(getattr(args, "domain", None)
                            or domain_pack.name_from_config(
                                getattr(args, "config", None) or ""))
    vibe_script = os.path.join(foundry_root, "scripts", "leanstral-vibe.sh")
    cache = _state_cache(run_dir, getattr(args, "config", None))
    memory = _experience_store(run_dir, getattr(args, "config", None))
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
        notebook = memory.render(target["id"]) if memory is not None else ""
        if notebook:
            print(f"[vibe-run] {target['id']}: carrying {memory.attempts(target['id'])} "
                  f"prior attempt(s) of experience", flush=True)
        cand = run_vibe_target(pack, target, main_repo=args.main_repo,
                               context_pack=context_pack,
                               max_turns=args.max_turns, vibe_script=vibe_script,
                               state_hints=state_hints, experience=notebook)
        cand_path = os.path.join(run_dir, f"{args.run_tag}-{target['id']}.candidate")
        with open(cand_path, "w", encoding="utf-8") as f:
            f.write(cand or "")
        removed = bool(cand) and "sorry" not in cand
        print(f"[vibe-run] {target['id']}: captured {len(cand or '')} bytes, "
              f"sorry_removed={removed}", flush=True)
    return 0


def _cmd_gate(args) -> int:
    import time

    import domain_pack
    from autoformalize import (golf_candidate, strengthen_candidate,
                                trim_unused_imports, trim_unused_opens)
    from gate import gate as run_gate
    from probe import daemon_check, mistral_chat
    from probe_lib import append_jsonl
    _, run_dir = _run_dir()
    pack = domain_pack.load(getattr(args, "domain", None)
                            or domain_pack.name_from_config(
                                getattr(args, "config", None) or ""))
    summary_log = os.path.join(run_dir, f"{args.run_tag}-summary.jsonl")
    state_cache = _state_cache(run_dir, getattr(args, "config", None))
    memory = _experience_store(run_dir, getattr(args, "config", None))
    summarizer = _summarizer() if memory is not None else None
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
        attempt_errors: list = []                 # gate errors, for the experience notebook
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
                    pack,
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
                # does not NEED, which is a different set: on the flagship's #161/#162
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
                t = trim_unused_imports(pack, candidate, check_fn=daemon_check)
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
                        pack,
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
                attempt_errors = g.get("errors") or []
        # item K: fold this attempt into the target's notebook so the NEXT tick starts
        # informed. Failures only — a pass ends the target. `error` is excluded too: a
        # missing capture is an infra miss with no proof lesson in it, and recording it
        # would burn a diversity rotation on noise. Strictly after the verdict is
        # written, and fail-open inside `record`, so memory cannot change an outcome.
        if memory is not None and summary["outcome"] in ("max_rounds", "fail_gate"):
            memory.record(target["id"],
                          {"outcome": summary["outcome"],
                           "reason": summary.get("gate_reason", ""),
                           "errors": attempt_errors},
                          summarize_fn=summarizer)
            summary["experience_attempts"] = memory.attempts(target["id"])
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


def _cmd_experience(args) -> int:
    """Is the notebook earning its tokens? `retried_targets` is the number that decides:
    memory only pays on a target the cron attempts more than once, so if every target is
    one-and-done the feature is ceremony and comes back out (same bar as `states`)."""
    from experience import ExperienceStore
    _, run_dir = _run_dir()
    store = ExperienceStore(os.path.join(run_dir, "experience.json"))
    report = store.report()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print("experience memory")
    print("=================")
    print(f"targets carried   : {report['targets']}")
    print(f"retried targets   : {report['retried_targets']}")
    print(f"total attempts    : {report['total_attempts']}")
    print(f"deepest chain     : {report['max_attempts']}")
    print(f"stored characters : {report['chars']}")
    if not report["targets"]:
        print("\nstore empty — run some ticks with [autoformalize].experience = true")
    elif not report["retried_targets"]:
        print("\nno target has failed twice yet, so no attempt has ever READ a notebook. "
              "The memory is write-only until that number moves.")
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
    xp = sub.add_parser("experience", help="experience-memory report (no daemon, no tokens)")
    xp.add_argument("--json", action="store_true", help="emit the raw report dict")
    args = ap.parse_args()
    if args.cmd == "states":
        return _cmd_states(args)
    if args.cmd == "experience":
        return _cmd_experience(args)
    return _cmd_run(args) if args.cmd == "run" else _cmd_gate(args)


if __name__ == "__main__":
    import sys
    sys.exit(main())
