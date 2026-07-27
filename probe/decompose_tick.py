"""Decompose-path tick orchestration (Phase 2, Task 2.8).

`scripts/decompose-tick.sh` owns the docker daemon<->lean-lsp flips and the per-leaf vibe
loop; this module owns the python/daemon/API steps around them, as two functions the shell
calls between flips:

  draft     — Magistral splits the hard target into a lemma-DAG; the skeleton gate accepts
              or rejects the split (one bounded re-decompose on failure) BEFORE any leaf
              gets proving budget; on pass, write the DAG + a per-leaf manifest.
  recompose — assemble the proved leaves + the main into one module and run the FULL gate;
              on pass write the candidate open-pr reads, else bank partial + declare remainder.

`do_draft`/`do_recompose` take injected `chat_fn`/`check_fn`, so the file wiring is
unit-testable with no API, daemon, or docker. The CLI wires the real Magistral + daemon.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from decompose import (assemble_skeleton, build_leaf_manifest, dag_to_dict,
                       draft_decomposition, parse_dag, recompose, skeleton_gate)


def _dag_path(runs_dir, tag, tid):
    return os.path.join(runs_dir, f"{tag}-{tid}.dag.json")


def _leafdir(runs_dir, tag, tid):
    return os.path.join(runs_dir, f"{tag}-{tid}-leaves")


def do_draft(tid, tag, runs_dir, *, target_text, context_pack, drafter_preamble,
             cfg_max_leaves, cfg_max_reask, chat_fn, check_fn, toolchain="",
             main_commit="", meta=None) -> dict:
    """Split the target and gate the skeleton. On pass, persist `<tag>-<id>.dag.json` and a
    per-leaf manifest under `<tag>-<id>-leaves/`; return the outcome. Outcomes: `drafted`
    (skeleton passed), `fail_draft` (no valid DAG after re-asks), `fail_skeleton` (split did
    not elaborate after one re-decompose), `indeterminate` (wedged daemon — retryable)."""
    meta = dict(meta or {}); meta.setdefault("id", tid)
    r = draft_decomposition(target_text, context_pack, chat_fn=chat_fn,
                            system_preamble=drafter_preamble, max_leaves=cfg_max_leaves,
                            max_reask=cfg_max_reask)
    if not r["ok"]:
        return {"outcome": "fail_draft", "reason": r["error"], "tokens": r["tokens"]}
    dag, tokens = r["dag"], r["tokens"]

    g = skeleton_gate(assemble_skeleton(dag), len(dag.leaves), check_fn=check_fn)
    if not g["passed"] and not g["indeterminate"]:
        # one bounded re-decomposition, feedback = the elaboration errors (Task 2.3.2)
        r2 = draft_decomposition(target_text, context_pack, chat_fn=chat_fn,
                                 system_preamble=drafter_preamble, max_leaves=cfg_max_leaves,
                                 max_reask=cfg_max_reask,
                                 feedback="the skeleton did not elaborate: " + g["verdict"]
                                 + " re-split so the main theorem's proof elaborates as leaf "
                                 "applications with the leaves left `:= by sorry`.")
        tokens += r2["tokens"]
        if r2["ok"]:
            dag = r2["dag"]
            g = skeleton_gate(assemble_skeleton(dag), len(dag.leaves), check_fn=check_fn)
    if g["indeterminate"]:
        return {"outcome": "indeterminate", "reason": g["verdict"], "tokens": tokens}
    if not g["passed"]:
        return {"outcome": "fail_skeleton", "reason": g["verdict"], "tokens": tokens}

    with open(_dag_path(runs_dir, tag, tid), "w", encoding="utf-8") as f:
        json.dump(dag_to_dict(dag), f, indent=2)
    man = build_leaf_manifest(dag, meta, _leafdir(runs_dir, tag, tid),
                              toolchain=toolchain, main_commit=main_commit)
    return {"outcome": "drafted", "leaves_total": len(dag.leaves),
            "leaf_ids": [t["id"] for t in man["targets"]], "tokens": tokens}


def do_recompose(tid, tag, runs_dir, *, check_fn) -> dict:
    """Gather the proved leaf modules vibe wrote, assemble them + the main, run the full
    gate. On pass write `<tag>-<id>.lean` (the candidate open-pr reads). Outcomes: `pass`,
    `partial` (proved leaves banked, the rest a declared remainder), `fail_gate` (all leaves
    proved but the composition failed)."""
    dag = parse_dag(json.load(open(_dag_path(runs_dir, tag, tid))))
    man = json.load(open(os.path.join(_leafdir(runs_dir, tag, tid), "manifest.json")))
    proved: dict[str, str] = {}
    for t in man["targets"]:
        leaf_lean = os.path.join(runs_dir, f"{tag}-{t['id']}.lean")
        if os.path.isfile(leaf_lean):
            proved[t["sorry_name"]] = open(leaf_lean, encoding="utf-8").read()
    leaves_total, leaves_closed = len(dag.leaves), len(proved)

    r = recompose(dag, proved, check_fn=check_fn)
    base = {"leaves_total": leaves_total, "leaves_closed": leaves_closed}
    if r["ok"]:
        with open(os.path.join(runs_dir, f"{tag}-{tid}.lean"), "w", encoding="utf-8") as f:
            f.write(r["module"])
        return {"outcome": "pass", **base}
    if r["partial"]:
        return {"outcome": "partial", "remainder": r["remainder"],
                "banked": r["banked"], **base}
    return {"outcome": "fail_gate", "reason": r["reason"], **base}


# --- CLI: wire the real Magistral + daemon around the shell's flips -----------

def _read_target(queue_path, tid):
    q = json.load(open(queue_path))
    for t in (q.get("targets", q) if isinstance(q, dict) else q):
        if t.get("id") == tid:
            return t
    raise SystemExit(f"[decompose-tick] target {tid} not in {queue_path}")


def _cmd_draft(args) -> int:
    from house_context import build_drafter_prompt, extract_signatures
    from pipeline_lib import DecomposeConfig, DrafterConfig
    from probe import daemon_check
    from autoformalize import claude_draft_fn
    cfg = DecomposeConfig.load(args.config)
    drafter = DrafterConfig.load(args.config)      # claude splits the target (leanstral proves the leaves)
    target = _read_target(args.queue, args.id)
    qroot = os.path.dirname(os.path.abspath(args.queue))
    target_text = open(os.path.join(qroot, target["file"]), encoding="utf-8").read()
    pointers = target.get("pointers", [])
    context_pack = extract_signatures(args.main_repo, pointers) if pointers else ""
    preamble = build_drafter_prompt(args.main_repo)
    r = do_draft(
        args.id, args.tag, args.runs, target_text=target_text, context_pack=context_pack,
        drafter_preamble=preamble, cfg_max_leaves=cfg.max_leaves, cfg_max_reask=cfg.max_reask,
        chat_fn=lambda msgs: claude_draft_fn(msgs, model=drafter.claude_model),
        check_fn=daemon_check,
        toolchain=open(os.path.join(args.main_repo, "lean-toolchain")).read().strip(),
        meta={k: target[k] for k in ("main_module", "benchmark", "source_issue")
              if k in target} | {"id": args.id})
    print(json.dumps(r))
    return 0


def _cmd_recompose(args) -> int:
    from gate import gate as run_gate
    from probe import daemon_check
    dag = parse_dag(json.load(open(_dag_path(args.runs, args.tag, args.id))))
    r = do_recompose(args.id, args.tag, args.runs,
                     check_fn=lambda m: run_gate(m, dag.main.name, check_fn=daemon_check))
    print(json.dumps(r))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="decompose-path tick orchestration (draft/recompose)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--id", required=True)
    common.add_argument("--tag", required=True)
    common.add_argument("--runs", required=True)
    d = sub.add_parser("draft", parents=[common])
    d.add_argument("--queue", required=True)
    d.add_argument("--main-repo", required=True)
    d.add_argument("--config", default=None)   # the split model is [drafter].claude_model
    r = sub.add_parser("recompose", parents=[common])
    r.add_argument("--queue", default=None)   # unused; kept for a uniform call shape
    args = ap.parse_args()
    return _cmd_draft(args) if args.cmd == "draft" else _cmd_recompose(args)


if __name__ == "__main__":
    sys.exit(main())
