"""assembly_smoke — stage the pack's assembly canary as if a prover had just passed it.

The prover has a canary: a trivial theorem proved through the real path before every
tick, so a launcher that cannot start is a red tick and not fourteen `max_rounds` rows.
Assembly had none. `open-pr.sh` is the longest script in the foundry and the one most
coupled to the target library — it runs that library's whole CI on the placed tree — and
the only way to exercise it was to draft, gate and prove a real target first. So when the
library reorganised in November 2026 the recipe went stale unobserved, and would have
stayed so until the first proof in six weeks reached it.

This writes, for the pack's `smoke/` fixture (a small genuine theorem WITH its proof):

    runs/<tag>-<id>.lean            the candidate, emitted by the real emitter
    runs/<tag>-<id>.session.json    a prover record naming the CONFIGURED prover model
    runs/<tag>-manifest.json        a one-target manifest carrying its placement

`scripts/assembly-smoke.sh` then runs `open-pr.sh` on it with `DRY_RUN=1`: the real
applier, the pack's whole recipe in the verify image, the commit, the host-side gates —
and no push. No model is called and nothing leaves the runner.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import autoformalize
import domain_pack
from build_manifest import parse_meta, parse_pointers
from pipeline_lib import ProverConfig
from vibe_prove import LEANSTRAL_MODEL

SMOKE_ID = "cal-smoke-1"


def stage(pack, main_repo: str, run_dir: str, tag: str, *, prover: dict) -> dict:
    """Emit the pack's canary and write its candidate, session record and manifest.
    Returns the manifest target.

    `prover` is the record a real session of the configured prover would leave
    (`{"engine", "model"}`). It is not decoration: the library derives its public
    AI-disclosure from each entry's `provenance.model` and gates the result, so a canary
    stamped with a made-up name fails a gate no real contribution would (the first canary
    run, 2026-11-12) — and one stamped with the real name tells us, before a proof is paid
    for, whether the library will accept an entry this prover signs."""
    base = os.path.join(domain_pack.default_pack_root(), pack.name, "smoke")
    with open(os.path.join(base, "smoke.json"), encoding="utf-8") as f:
        spec = json.load(f)
    with open(os.path.join(base, "body.lean"), encoding="utf-8") as f:
        body = f.read().strip()

    raw = [{"number": spec["issue"]["number"], "title": spec["issue"]["title"],
            "body": spec["issue"]["body"],
            "labels": [{"name": "status:ready"}, {"name": "type:proof"},
                       {"name": f"difficulty:{spec['issue'].get('difficulty', 'small')}"},
                       *({"name": f"area:{a}"} for a in spec["issue"].get("areas", []))]}]
    issues = autoformalize.prepare_issues(pack, raw, main_repo=main_repo,
                                          log=lambda m: print(f"[smoke] {m}", file=sys.stderr))
    if not issues:
        raise SystemExit("[smoke] the canary issue did not survive prepare_issues")
    issue = issues[0]
    intent = spec["intent"]
    reason = autoformalize.intent_contract_reason(pack, issue, intent)
    if reason:
        raise SystemExit(f"[smoke] the canary's intent breaks the library contract: {reason}")
    lean_text, entry, placement = autoformalize.emit_target_files(pack, issue, body, intent)

    os.makedirs(run_dir, exist_ok=True)
    stem = os.path.join(run_dir, f"{tag}-{SMOKE_ID}")
    with open(stem + ".lean", "w", encoding="utf-8") as f:
        f.write(lean_text)
    with open(stem + ".session.json", "w", encoding="utf-8") as f:
        json.dump({"engine": prover["engine"], "model": prover["model"], "canary": True}, f)
    target = {"id": SMOKE_ID, "stream": "control", "kind": "prove",
              "sorry_name": autoformalize.split_statement(body)[0],
              "file": f"{tag}-{SMOKE_ID}.lean", "pointers": parse_pointers(lean_text),
              **parse_meta(lean_text), "benchmark_entry": entry}
    with open(os.path.join(run_dir, f"{tag}-manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"targets": [target], "quarantined": []}, f, indent=2, ensure_ascii=False)
    return {**target, **{k: v for k, v in placement.items() if k not in target}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="assembly_smoke")
    ap.add_argument("--main-repo", required=True)
    ap.add_argument("--domain", default=None)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--run-dir", default=None)
    args = ap.parse_args(argv)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = os.path.join(root, "pipeline.toml")
    pack = domain_pack.load(args.domain or domain_pack.name_from_config(config))
    cfg = ProverConfig.load(config)
    prover = {"engine": cfg.engine,
              "model": cfg.claude_model if cfg.engine == "claude" else LEANSTRAL_MODEL}
    target = stage(pack, args.main_repo, args.run_dir or os.path.join(root, "runs"), args.tag,
                   prover=prover)
    print(json.dumps({"id": target["id"], "main_module": target["main_module"],
                      "benchmark": target["benchmark"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
