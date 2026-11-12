#!/usr/bin/env bash
# Turn a proven candidate into a ready-for-review PR on the target library.
#
#   scripts/open-pr.sh --id <target-id> --tag <run-tag>
#
# Runs in the FOUNDRY CI runner (16 GB, the domain's verify image, main checked out
# with MAIN_PR_TOKEN) — never on the 10 GB local box. It:
#   1. reads the winning candidate + placement metadata,
#   2. branches on the main checkout, places the proof in its library module,
#      appends the re-export benchmark entry, registers the module in the umbrella,
#   3. VALIDATES + REGENERATES green-or-abort, by the DOMAIN PACK's own recipe
#      (`domains/<name>/regen.sh`, run in the verify image: build → narrow imports →
#      lint → regenerate → ledger → declaration index + ratchet), then commits and runs
#      the pack's host-side gates (`host-gates.sh`) on the COMMITTED tree. Any failure
#      files an `autoform-blocked` issue on the FOUNDRY repo and exits WITHOUT opening
#      a PR (never a red PR),
#   4. promotion-honesty guard: reject an rfl-trivial "full" proof,
#   5. push + gh pr create (label autoform). The commit is authored by the pack's
#      `commit_author` (a `[bot]`, which is how the library's attribution gate tells a
#      machine commit from a contributor's). The body `closes` the source issue only
#      when the proof covers all of it AND the issue names no frontier node; otherwise
#      it `refs` it. R runs the 8-lens review and merges.
#
# Daemon lifecycle is owned by the workflow: this script stops the daemon (the build is
# the one Lean/Lake writer).
set -euo pipefail

ID=""; TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --id) ID="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    *) echo "[open-pr] unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$ID" ] && [ -n "$TAG" ] || { echo "[open-pr] need --id and --tag" >&2; exit 2; }

FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"
# --- domain pack (runbook 06): the ONE place that knows which library we target ---
# `--domain`/`DOMAIN` picks the pack; it defaults to `[domain] name` in pipeline.toml.
# Exports DOMAIN_NAMESPACE, DOMAIN_LAKE_ROOT, MAIN_REPO_SLUG, DOMAIN_VERIFY_IMAGE,
# DOMAIN_LEAN_LSP_CONTAINER, DOMAIN_BENCHMARK, DOMAIN_OWN_NAMESPACES, ...
eval "$(python3 "$FOUNDRY/probe/domain_pack.py" --export-env ${DOMAIN:+"$DOMAIN"})"
# Local default: the target library checked out BESIDE the foundry, named by the
# pack. Both scripts used to hardcode a path, and they disagreed — open-pr.sh
# pointed at `/home/rapha/code/automated_proofs_quantfin`, which does not exist.
# CI always sets MAIN_REPO explicitly, so this default only bites locally, which
# is exactly where a dead path is least likely to be noticed.
MAIN="${MAIN_REPO:-$(dirname "$FOUNDRY")/$DOMAIN_REPO_NAME}"
SLUG="$MAIN_REPO_SLUG"
# QUEUE_MANIFEST: the assembly canary stages its own one-target manifest
QUEUE="${QUEUE_MANIFEST:-$FOUNDRY/targets/queue/manifest.json}"
CAND="$FOUNDRY/runs/$TAG-$ID.lean"
[ -f "$CAND" ] || { echo "[open-pr] no candidate at $CAND" >&2; exit 1; }

# --- 1. placement metadata (from the queue manifest) -------------------------
read_meta() { python3 - "$QUEUE" "$ID" "$1" <<'PY'
import json, sys
q, tid, key = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
for t in (q.get("targets", q) if isinstance(q, dict) else q):
    if t.get("id") == tid:
        v = t.get(key, "")
        print(v if not isinstance(v, (dict, list)) else json.dumps(v)); break
PY
}
MODULE="$(read_meta main_module)"
BENCH="$(read_meta benchmark)"
ISSUE="$(read_meta source_issue)"
DEFERRED="$(read_meta deferred)"   # json list of facts a SUBSET proof left for follow-up (or "")
[ -n "$MODULE" ] && [ -n "$BENCH" ] || {
  echo "[open-pr] target $ID missing main_module/benchmark metadata" >&2; exit 1; }

# The prover, READ from the session that ran — never assumed. The commit and PR body
# name the real prover, and so does the entry's provenance (`stamp_prover`, below).
# Attribution rule: the prover is NAMED in the message; Claude is never a co-author.
# (The autop scout path — draft PRs, `.scout` sidecars, `refs`-not-`closes` — was removed
# with autop itself; every candidate is axiom-guarded by the gate.)
PROVER_JSON="$(cd "$FOUNDRY/probe" && python3 prove_session.py prover-of --run-tag "$TAG" \
  --id "$ID" --config "$FOUNDRY/pipeline.toml")"
PROVER_ENGINE="$(printf '%s' "$PROVER_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["engine"])')"
MODEL="${MODEL:-$(printf '%s' "$PROVER_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["model"])')}"
PR_FLAGS=()
case "$PROVER_ENGINE" in
  claude) PROVER_NAME="Claude (${MODEL})" ;;
  *)      PROVER_NAME="${MODEL}" ;;
esac
PROVER_DESC="Proved by ${PROVER_NAME} via the formal-foundry autoform pipeline; human-reviewed before merge."
PROVENANCE_DESC="$PROVER_ENGINE"
BODY_INTRO="this pr was produced by the autoform pipeline (prover: ${PROVER_NAME}), then assembled and validated green in ci."
PROOF_BULLET="- \`$MODULE\` — the proof (axioms-clean; the probe's axiom guard passed)."

FOUNDRY_SLUG="${FOUNDRY_REPO:-formal-applied-math/formal-foundry}"

REGEN_LOG="$FOUNDRY/runs/$TAG-$ID.assembly.log"

blocked() {  # file an autoform-blocked issue on the FOUNDRY repo, do NOT open a PR
  echo "[open-pr] BLOCKED: $1" >&2
  # the reason, for the tick: it releases the target with it (`release.py`) so the next
  # attempt is told what the library refused, instead of the issue staying stranded
  python3 - "$FOUNDRY/runs/$TAG-$ID.blocked.json" "$1" "$REGEN_LOG" <<'PY' || true
import json, re, sys
out, why, log = sys.argv[1:4]
try:
    text = open(log, encoding="utf-8", errors="replace").read()
except OSError:
    text = ""
ratchets = re.findall(r"^ratchet: (.+)$", text, re.M)
json.dump({"reason": why, "ratchets": ratchets}, open(out, "w", encoding="utf-8"), indent=2)
PY
  # WHY, kept: the last lines of the recipe's output ride the run's telemetry and the
  # issue, so "did not assemble green" names the gate that said so.
  local tail_txt=""
  [ -f "$REGEN_LOG" ] && tail_txt="$(tail -n 40 "$REGEN_LOG" | cut -c1-400)"
  # The foundry's OWN token files the issue: GH_TOKEN here is the library's PR token,
  # which has no rights on this repository (it filed nothing from 2026-09-28 on).
  if [ -n "${CI:-}" ] && [ -z "${DRY_RUN:-}" ] && command -v gh >/dev/null 2>&1; then
    GH_TOKEN="${FOUNDRY_GH_TOKEN:-${GH_TOKEN:-}}" gh label create autoform-blocked \
      --repo "$FOUNDRY_SLUG" --color B60205 \
      --description "autoform candidate did not assemble green" 2>/dev/null || true
    GH_TOKEN="${FOUNDRY_GH_TOKEN:-${GH_TOKEN:-}}" gh issue create --repo "$FOUNDRY_SLUG" \
      --title "autoform-blocked: $ID ($TAG)" --label "autoform-blocked" \
      --body "candidate for $ID ($TAG) did not assemble green: $1. proof is at runs/$TAG-$ID.lean.

last lines of the assembly log:
\`\`\`
$tail_txt
\`\`\`" \
      2>/dev/null || true
  fi
  exit 3   # CONTENT-deterministic block: the tick RECORDS the target (fail_assembly)
  #        # — retrying the same candidate would block identically every tick.
}

transient() {  # infra/transient failure (network, gh, docker pull): no issue, no PR;
  echo "[open-pr] TRANSIENT: $1" >&2   # exit 4 so the tick keeps the target retryable.
  exit 4
}

pr_unopened() {  # the branch is pushed and green; only `gh pr create` was refused.
  # From 2026-09-28 to 10-21 this happened thirteen times, each a finished proof nobody
  # was told about. The work is on the library now, so say where, once: the body is kept,
  # and one issue HERE carries the link that opens the pull request by hand.
  local url="https://github.com/$SLUG/pull/new/$BRANCH"
  local title="autoform-ready: $ID is pushed, its pull request could not be opened"
  printf '%s\n' "$BODY" > "$FOUNDRY/runs/$TAG-$ID.pr-body.md"
  echo "::error::[open-pr] $BRANCH is pushed and green but the pull request was refused (does the library token have Pull requests: write?). Open it by hand: $url" >&2
  if [ -n "${CI:-}" ] && command -v gh >/dev/null 2>&1; then
    export GH_TOKEN="${FOUNDRY_GH_TOKEN:-${GH_TOKEN:-}}"
    gh label create autoform-ready --repo "$FOUNDRY_SLUG" --color 0E8A16 \
      --description "a green autoform branch is waiting for someone to open its pull request" \
      2>/dev/null || true
    if [ -z "$(gh issue list --repo "$FOUNDRY_SLUG" --state open --label autoform-ready \
                 --search "\"$ID\" in:title" --json number --jq '.[].number' 2>/dev/null)" ]; then
      gh issue create --repo "$FOUNDRY_SLUG" --title "$title" --label autoform-ready \
        --body "\`$BRANCH\` on $SLUG assembled green and is pushed. \`gh pr create\` was refused, so no pull request exists.

open it: $url

title: \`$PR_TITLE\`
the body is in \`runs/$TAG-$ID.pr-body.md\` (labels: autoform).

a later tick finds the open pull request by its branch and stops retrying." \
        2>/dev/null || true
    fi
  fi
  transient "gh pr create failed (candidate is green and pushed — retry next tick)"
}

# --- 2. branch + place -------------------------------------------------------
cd "$MAIN"
# The drafter and the prover worked in scratch modules UNDER the Lake root, and they are
# still there. The library's gates walk that tree on disk, not git's index: a leftover
# scratch file is a module in no taxonomy cell, with a whole-Mathlib import and no
# docstring, and it would fail the contribution for something that is not in it.
rm -f "$MAIN/${DOMAIN_SCRATCH_MODULE:-}" "$MAIN/$DOMAIN_LAKE_ROOT"/_Autoform_*.lean 2>/dev/null || true
# ONE branch per target, force-pushed. A run-tagged branch name left thirteen copies of
# cal-bk-129 on the library (2026-09-28 .. 10-21: every tick pushed, none could open its
# PR); a retry now updates the branch it already has.
BRANCH="autoform/$ID"
git checkout -B "$BRANCH"
set +e
PLACED="$FOUNDRY/runs/$TAG-$ID.placement.json"
python3 - "$CAND" "$QUEUE" "$ID" "$MAIN" "$FOUNDRY/probe" "$FOUNDRY/runs/$TAG-$ID.entry.json" "$DOMAIN_NAME" "$MODEL" "$PLACED" <<'PY'
import json, os, sys
cand_path, queue_path, tid, main, probe_dir, entry_override, domain_name, prover_model, placed = sys.argv[1:10]
sys.path.insert(0, probe_dir)
import domain_pack
from assemble import (apply_contribution, ensure_module_doc, ensure_umbrella_import,
                      record_frontier_core, stamp_prover)
from redact import secret_findings, secret_values
# the pack is passed DOWN (2026-08-16 refactor); this heredoc was missed by it, so the
# first real pass after that date would have crashed here and — treated as transient —
# been re-proved every tick without ever opening a PR.
pack = domain_pack.load(domain_name)
q = json.load(open(queue_path))
target = next(t for t in (q.get("targets", q) if isinstance(q, dict) else q) if t["id"] == tid)
if os.path.exists(entry_override):
    # the gate's strengthen pass dropped unused hypotheses: the run-tagged entry
    # matches the stripped module theorem; the seed-manifest entry no longer does.
    entry = json.load(open(entry_override))
    print("[open-pr] using strengthened entry (unused hypotheses stripped)", file=sys.stderr)
else:
    entry = target["benchmark_entry"]  # authored in the seed manifest
entry, stamped = stamp_prover(entry, model=prover_model)
if stamped:
    print(f"[open-pr] provenance: prover is {prover_model} ({stamped})", file=sys.stderr)
code = open(cand_path, encoding="utf-8").read()
# a decompose-path candidate is rebuilt around its lemma-DAG and has lost the module
# docstring its stub was emitted with; the stub is the source of record for it
try:
    stub_text = open(os.path.join(os.path.dirname(queue_path), target.get("file") or ""),
                     encoding="utf-8").read()
except OSError:
    stub_text = None
code = ensure_module_doc(code, stub_text)
# The last look before anything is written to a PUBLIC pull request: the proof and the
# entry (whose prose a model drafted) must not carry a credential. Kinds only, never values.
leaks = sorted(set(secret_findings(code, secret_values())
                   + secret_findings(json.dumps(entry, ensure_ascii=False), secret_values())))
if leaks:
    print(f"[open-pr] credential-shaped content {leaks} in the candidate or its entry",
          file=sys.stderr)
    sys.exit(3)
written = apply_contribution(pack, code, target, entry, main)
written += ensure_umbrella_import(main, target["main_module"], f"{pack.namespace}.lean")
# The program map: an issue that names a node still on the frontier is not CLOSED by a
# machine. The entry is recorded as proving part of the node (its `core` list — the
# library's own mechanism), and whether the node is delivered stays the reviewer's call.
frontier = record_frontier_core(main, pack.layout.blueprint_frontier,
                                target.get("blueprint") or [], entry["id"])
if frontier:
    written.append(pack.layout.blueprint_frontier)
    print(f"[open-pr] frontier node(s) {frontier}: recorded {entry['id']} in `core`",
          file=sys.stderr)
print("[open-pr] wrote:", ", ".join(written), file=sys.stderr)
with open(placed, "w", encoding="utf-8") as f:
    json.dump({"written": written, "frontier": frontier, "entry_id": entry["id"],
               "blueprint": target.get("blueprint") or [],
               "new_defs": target.get("new_defs") or []}, f)
PY
PLACE_RC=$?
set -e
if [ "$PLACE_RC" = 3 ]; then
  blocked "credential-shaped content in the candidate or its entry (kinds in the tick log; values never printed)"
fi
[ "$PLACE_RC" = 0 ] || exit 1
placed() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2]) or []; print(", ".join(v) if isinstance(v, list) else v)' "$PLACED" "$1"; }
FRONTIER="$(placed frontier)"
BLUEPRINT="$(placed blueprint)"
NEW_DEFS="$(placed new_defs)"

# A SUBSET proof (its `-- deferred:` header → manifest `deferred` list) must NOT
# auto-close its multi-part parent issue on merge: use `refs` (not `closes`) in the
# commit + PR body, and surface the deferred facts as a suggested-follow-up section
# for R to open. So must a proof toward a FRONTIER node. Otherwise → `closes #N`.
FOLLOWUPS="$(python3 - "$DEFERRED" "$ISSUE" <<'PY'
import json, sys
raw, issue = sys.argv[1].strip(), sys.argv[2]
try:
    items = json.loads(raw) if raw else []
except ValueError:
    items = []
if items:
    out = ["", f"this pr is a faithful SUBSET of #{issue}; the drafter deferred the rest.",
           "suggested follow-up issues (open one per remaining fact, then close the parent):"]
    out += [f"- [ ] {it}" for it in items]
    print("\n".join(out))
PY
)"
if [ -n "$FOLLOWUPS" ]; then
  CLOSE_KW="refs"; CLOSE_LINE="refs #$ISSUE (subset — see follow-ups below)"
  TITLE_SUFFIX="(subset of #$ISSUE)"
elif [ -n "$FRONTIER" ]; then
  # #N names a result the program map still lists as not formalized. Closing it here
  # would leave a frontier node no open issue works toward, which the library's
  # blueprint check refuses on main; and whether this theorem IS that node is a
  # judgment about a statement, which is the reviewer's.
  CLOSE_KW="refs"; CLOSE_LINE="refs #$ISSUE (frontier node $FRONTIER — see the note below)"
  TITLE_SUFFIX="(toward #$ISSUE)"
else
  CLOSE_KW="closes"; CLOSE_LINE="closes #$ISSUE"; TITLE_SUFFIX="(closes #$ISSUE)"
fi


# --- 3. promotion-honesty guard (reject an rfl-trivial "full") ---------------
# H9: use the tested probe_lib.rfl_proof_present (the shell glob missed `:= by rfl`
# before the namespace's `end` and the `:=byrfl` spelling — non-EOF variants slipped).
if python3 -c "import sys; sys.path.insert(0, '$FOUNDRY/probe'); from probe_lib import rfl_proof_present; sys.exit(0 if rfl_proof_present(open('$MAIN/$MODULE', encoding='utf-8').read()) else 1)"; then
  blocked "proof is rfl-trivial (reduced_core-in-disguise; test_values rfl-tripwire would reject)"
fi

# --- 4. validate + regenerate (green-or-abort) -------------------------------
# The Lean toolchain lives in the domain's verify IMAGE, not on the runner host, so the
# build and everything that needs an elaborated library runs INSIDE the image against
# this fresh checkout. The daemon must be DOWN for it (one Lake writer).
#
# WHAT runs is the PACK's: `domains/<name>/regen.sh` is that library's CI, step for
# step. It used to be a block in this script, written against the flagship as it was in
# July; by November the library's build gated a declaration index, a ratchet, a doc
# standard and a narrowed import list this script had never heard of, and every
# candidate it "validated green" would have opened red. A pack with no recipe gets the
# generic one below.
IMAGE="${VERIFY_IMAGE:-$DOMAIN_VERIFY_IMAGE}"
PACK_DIR="$FOUNDRY/domains/$DOMAIN_NAME"
MODULE_NAME="$(printf '%s' "${MODULE%.lean}" | tr '/' '.')"
# the proving daemon is done; stop it (all possible names: docker-run `lean-repl`,
# compose `docker-lean-repl-1`, plus the lean-lsp) so the build has the memory
# headroom AND is the sole Lake writer to the shared olean volume mounted below.
docker stop lean-repl docker-lean-repl-1 "$DOMAIN_LEAN_LSP_CONTAINER" >/dev/null 2>&1 || true
# SINGLE-quoted on purpose: `$DOMAIN_NAMESPACE` must survive to the CONTAINER's
# shell, which means the container needs it in its env (`-e` below). Left
# unpassed it expands to empty and this silently becomes `lake build ""`.
REGEN='set -e
       if [ -f /pack/regen.sh ]; then exec bash /pack/regen.sh; fi
       lake exe cache get >/dev/null 2>&1 || true
       lake build "$DOMAIN_NAMESPACE"
       lake lint'
if command -v docker >/dev/null 2>&1; then
  # --entrypoint bash overrides the image's default entrypoint (the verify
  # CLI); without it the shell command is fed as args to that CLI and errors.
  # Mount the checkout at /app (the daemon's Lake root) + the SHARED olean volume at
  # /app/.lake, so the library build REUSES the daemon-built Mathlib + library
  # oleans and rebuilds only the new module — not the whole library from scratch
  # (that from-scratch library rebuild blew the 2h job timeout on the first live PR,
  # 2026-07-17: 8558 Mathlib modules came from `cache get`, then heavy library
  # modules like DoobLpMaximalInequality rebuilt at ~400s each).
  # The build's progress lines are dropped from the log that is kept (a library build
  # prints ~9000 of them); what is left is the recipe's own output and every error.
  set +e
  docker run --rm --entrypoint bash \
    -e DOMAIN_NAMESPACE="$DOMAIN_NAMESPACE" \
    -e DOMAIN_SHAKE_EXE="${DOMAIN_SHAKE_EXE:-}" \
    -e MODULE="$MODULE" -e MODULE_NAME="$MODULE_NAME" \
    -v "$MAIN":/app \
    -v "$PACK_DIR":/pack:ro \
    -v "${COMPOSE_PROJECT_NAME:-docker}_lake_build_cache":/app/.lake \
    -w /app "$IMAGE" -lc "$REGEN" 2>&1 \
    | grep --line-buffered -v -E '^(✔|ℹ|⚠) \[|Replayed |^warning: .*declaration uses .sorry.' \
    | tee "$REGEN_LOG"
  REGEN_RC="${PIPESTATUS[0]}"   # the CONTAINER's status, not grep's or tee's
  set -e
  [ "$REGEN_RC" = 0 ] \
    || blocked "in-image build/regen failed rc=$REGEN_RC (domains/$DOMAIN_NAME/regen.sh: build / shake / lint / regen / ledger / index + ratchet)"
else
  echo "[open-pr] WARN: no docker — the library was NOT built; PR CI will gate it" >&2
fi

# --- 5. commit, then the host-side gates on the COMMITTED tree ---------------
# specific adds only (never -A): the proof, the benchmark entry, the umbrella
# import, and whichever regenerated artifacts exist.
git add "$MODULE" "$BENCH" "$DOMAIN_NAMESPACE.lean"
for f in formalization.yaml "$DOMAIN_LAKE_ROOT/AxiomAuditGen.lean" verification_ledger.json \
         "${DOMAIN_BLUEPRINT_FRONTIER:-}"; do
  [ -n "$f" ] && [ -e "$f" ] && git add "$f"
done
# anything else the regen changed is a generated artifact this script does not know
# about yet — say so, because a PR that leaves it out opens with a stale-artifact gate red
LEFT="$(git status --porcelain --untracked-files=no | grep -v '^[MADR] ' \
        | grep -v -e 'docker/docker-compose.lean-lsp.yml' || true)"
[ -z "$LEFT" ] || echo "[open-pr] WARN: modified and NOT in this commit: $(printf '%s' "$LEFT" | tr '\n' ' ')" >&2
# Attribution rule (honest provenance): the PR credits the prover that did the
# mathematical work, matching the benchmark entry's metadata.provenance and the
# formalization.yaml automation count. The AUTHOR is the pack's bot identity: the
# library reads authorship from git, and a non-bot name here is a contributor its
# header does not credit and its README does not list.
git -c user.name="${DOMAIN_COMMIT_AUTHOR:-$DOMAIN_NAME-autoform[bot]}" \
    -c user.email="autoform@users.noreply.github.com" \
    commit -q \
    -m "feat(autoform): $ID — prove $(basename "$MODULE" .lean) ($CLOSE_KW #$ISSUE)" \
    -m "$PROVER_DESC"
# The OTHER half of CI parity: the library's host-side gates, run exactly as its
# build runs them — and AFTER the commit, because the attribution gate reads who
# added each file from git. (They ran before it until 2026-11: the new module was
# untracked, the gate could not see it, and the first thing the PR's own build would
# have said is that `<pack>-autoform` is an uncredited contributor.)
#
# Red here is the candidate's doing ONLY IF the same gates are green on the commit it
# was placed on. Some of them read live state the contribution does not touch (the
# blueprint check lists the library's open issues), and a library whose own main is
# red for an hour must not cost a proof a `fail_assembly` verdict: the second canary
# run (2026-11-12) was refused for three issues naming labels main retired 20 minutes
# later. So a red result is re-asked of the base, in a throwaway worktree.
base_is_red() {
  local wt rc
  wt="$(mktemp -d)"
  git -C "$MAIN" worktree add -q --detach "$wt" HEAD~1 || { rm -rf "$wt"; return 1; }
  ( cd "$wt" && bash "$PACK_DIR/host-gates.sh" ) >"$FOUNDRY/runs/$TAG-$ID.base-gates.log" 2>&1
  rc=$?
  git -C "$MAIN" worktree remove --force "$wt" 2>/dev/null || rm -rf "$wt"
  [ "$rc" -ne 0 ]
}
if [ -f "$PACK_DIR/host-gates.sh" ]; then
  if ! ( cd "$MAIN" && bash "$PACK_DIR/host-gates.sh" ); then
    if base_is_red; then
      echo "::warning::[open-pr] the library's host-side gates are red on its OWN base commit $(git rev-parse --short HEAD~1) — not this contribution:" >&2
      tail -n 12 "$FOUNDRY/runs/$TAG-$ID.base-gates.log" >&2 || true
      transient "the library's base commit fails its own host-side gates (see runs/$TAG-$ID.base-gates.log) — retry when its main is green"
    fi
    blocked "the library's host-side gates are red on the committed contribution and green on its base (domains/$DOMAIN_NAME/host-gates.sh)"
  fi
else
  python3 -m pip show pytest >/dev/null 2>&1 || python3 -m pip install -q pytest
  ( cd "$MAIN" && python3 -m pytest tests/ -q ) \
    || blocked "python gates red after placement (pytest tests/)"
fi

if [ -n "${DRY_RUN:-}" ]; then
  # the assembly canary stops here: everything a real PR's build would check has been
  # checked on the committed tree, and nothing leaves the runner
  echo "[open-pr] DRY RUN: $ID assembled green on $(git rev-parse --short HEAD) — not pushed" >&2
  git show --stat --format='%an <%ae>%n%s' HEAD >&2
  exit 0
fi

# --- 6. push + PR ------------------------------------------------------------
# The checkout does not persist credentials (the prover can read the runner's files), so
# this one command carries the token, as an extra header that is never written to disk.
[ -n "${GH_TOKEN:-}" ] || transient "no GH_TOKEN to push with"
git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)" \
    push -f origin "$BRANCH" || transient "the push of $BRANCH was refused"

# what the proof cost: every row of the target and its leaves this run, and, for a proof
# reused from an earlier run, that run's session (#500 said "~0 tokens" for a reused one)
TOKENS="$(python3 - "$FOUNDRY/runs/$TAG-summary.jsonl" "$ID" "$FOUNDRY/runs/$TAG-$ID.session.json" <<'PY'
import json, sys
tok = 0
try:
    for line in open(sys.argv[1]):
        r = json.loads(line)
        t = r.get("target") or ""
        if t == sys.argv[2] or t.startswith(sys.argv[2] + "__"):
            tok += int(r.get("tokens") or 0)
except (OSError, ValueError): pass
try:
    sess = json.load(open(sys.argv[3]))
    if sess.get("reused_from"):
        tok += int(sess.get("original_tokens") or 0)
except (OSError, ValueError, TypeError): pass
print(tok)
PY
)"

# statement-fidelity notes (R7): the reviewer's map from the informal claim to the
# Lean statement, embedded in the PR body so the fidelity judgment is auditable by
# someone who is not the pipeline (the kernel checks the proof, not the statement).
POINTERS_JSON="$(read_meta pointers)"; [ -n "$POINTERS_JSON" ] || POINTERS_JSON="[]"
PROV_JSON="$(python3 -c "import json,sys; print(json.dumps({'source': sys.argv[1], 'model': sys.argv[2]}))" "$PROVENANCE_DESC" "$MODEL")"
FIDELITY_NOTES="$(python3 "$FOUNDRY/probe/fidelity_notes.py" \
  --target-id "$ID" --lean-file "$CAND" --issue-number "$ISSUE" \
  --pointers "$POINTERS_JSON" --provenance "$PROV_JSON" 2>/dev/null \
  || echo "(fidelity notes unavailable)")"

# first-pass refinery punch list (Task 2.7): a soft Claude review over the proven
# candidate — the MECHANICAL half of the 8-lens pass (unused constructs, wrapper smell,
# register, obvious golf) as a checklist the human refiner starts from. NEVER gates:
# refinery_notes.py falls back to a skip line without a key or on any API error.
REFINERY_NOTES="$(python3 "$FOUNDRY/probe/refinery_notes.py" --lean-file "$CAND" \
  --domain "$DOMAIN_NAME" 2>/dev/null \
  || echo "_(first-pass refinery notes unavailable)_")"

# Phase 2 (Task 2.5): a decompose-path candidate carries a DAG sidecar — list the split in
# the PR body so the reviewer sees the leaf structure. Absent for a plain cron PR (no change).
DAG_SIDE="$FOUNDRY/runs/$TAG-$ID.dag.json"
DAG_SECTION=""
if [ -f "$DAG_SIDE" ]; then
  DAG_SECTION="$(python3 - "$DAG_SIDE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
out = ["", "## lemma-DAG (decomposition)", "",
       f"main `{d['main']['name']}` is proved by applying:"]
for leaf in d.get("leaves", []):
    dep = (" — depends on " + ", ".join(f"`{x}`" for x in leaf["depends_on"])) if leaf.get("depends_on") else ""
    out.append(f"- `{leaf['name']}`{dep}")
print("\n".join(out))
PY
)"
fi

# The program map. The PR names the node(s) its issue names, so the blueprint links it
# as it links the issue; and a frontier node gets the one decision a machine does not
# make spelled out for the reviewer.
BLUEPRINT_LINE=""; FRONTIER_NOTE=""
[ -z "$BLUEPRINT" ] || BLUEPRINT_LINE="Blueprint: $BLUEPRINT"
if [ -n "$FRONTIER" ]; then
  FRONTIER_NOTE="
frontier: #$ISSUE names \`$FRONTIER\`, which the program map still lists as not formalized. this pr records the entry under the node's \`core\` in \`${DOMAIN_BLUEPRINT_FRONTIER:-the frontier file}\` and leaves the node where it is. if this theorem delivers the node: tag the declaration in the blueprint under the same label, delete the frontier entry, and close #$ISSUE; otherwise split what is left into follow-up issues."
fi

BODY="$(cat <<EOF
$BODY_INTRO $CLOSE_LINE.

what it adds:
$PROOF_BULLET
- a re-export entry in \`$BENCH\`.
- regenerated \`$DOMAIN_LAKE_ROOT/AxiomAuditGen.lean\` + \`formalization.yaml\`.
$DAG_SECTION$FRONTIER_NOTE

$BLUEPRINT_LINE

provenance: $PROVENANCE_DESC, run tag \`$TAG\`, ~$TOKENS tokens.

review checklist (8-lens, before merge):
- [ ] the statement faithfully formalizes (its stated subset of) issue #$ISSUE — no vacuity, no weaker restatement of what it states; a declared subset is fine (see follow-ups).
- [ ] no DERIVABLE hypothesis survives (the #123 \`hP\` class: a positivity/side condition provable from the concrete defs — the strengthen pass only removes proof-UNUSED ones; this one needs an eye).
- [ ] the proof is idiomatic and consumes existing lemmas, not a wrapper.
- [ ] the module docstring's reference and its locator are the source that states this result (the citation key was chosen by the drafter from the bibliography).
- [ ] ledger row present (run \`ledger verify\` if ci is red on it).
- [ ] axioms clean; no slop.
- [ ] coverage.md journal block authored at merge (reviewer-written — the narrative voice stays human).$FOLLOWUPS

<details><summary>statement-fidelity notes</summary>

$FIDELITY_NOTES

</details>

<details><summary>first-pass refinery punch list (mechanical lenses — soft; the human owns the rewrite)</summary>

$REFINERY_NOTES

</details>
EOF
)"
gh label create autoform --repo "$SLUG" --color 0E8A16 \
  --description "opened by the autoform pipeline; review before merge" 2>/dev/null || true
# defs-route modules introduce new definitions — the architecture-heavy review
# class (library design, not just proof correctness). Flag them.
if [ -n "$NEW_DEFS" ]; then
  gh label create new-defs --repo "$SLUG" --color 5319E7 \
    --description "autoform PR introducing new definitions — review the design, not just the proof" 2>/dev/null || true
  PR_FLAGS+=(--label new-defs)
fi
PR_TITLE="autoform: $(basename "$MODULE" .lean) $TITLE_SUFFIX"
# A pull request already open from this branch (opened by hand after an `autoform-ready`
# note, or by an earlier tick whose bookkeeping failed) is the deliverable: the push
# above updated it, and a second one would be refused.
EXISTING_PR="$(gh pr list --repo "$SLUG" --head "$BRANCH" --state open \
                 --json number --jq '.[0].number // empty' 2>/dev/null || true)"
if [ -n "$EXISTING_PR" ]; then
  echo "[open-pr] pull request #$EXISTING_PR is already open from $BRANCH — updated by the push" >&2
else
  gh pr create --repo "$SLUG" --head "$BRANCH" --label autoform "${PR_FLAGS[@]}" \
    --title "$PR_TITLE" --body "$BODY" || pr_unopened
fi
echo "[open-pr] PR opened for $ID ($CLOSE_KW #$ISSUE)" >&2

# The ISSUE is the state machine; move it to `status:review` now that a PR exists.
# Without this the issue stays `status:ready` for the whole review window and the
# refill can re-draft work that is already awaiting a human — the exposure
# `next_target`'s docstring names. FAIL-OPEN: the PR is the deliverable and this is
# bookkeeping about it, so a missing `issues: write` scope must not fail the run.
python3 "$FOUNDRY/probe/issue_state.py" --repo "$SLUG" --issue "$ISSUE" \
  --status status:review >&2 || \
  echo "[open-pr] could not mark #$ISSUE status:review — PR is open regardless" >&2
