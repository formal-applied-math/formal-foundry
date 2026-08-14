"""Issue -> stub autoformalizer sub-probe (the self-feeding refill phase).

Turns the next `status:ready`+`type:proof` GitHub issue into a *validated* queue
target (stub `.lean` + `.entry.json` + manifest row) so the existing prover always
has something to prove. Two engines: Claude drafts the statement, formalizes it
agentically (`claude -p` + the lean-lsp MCP, self-validating to elaboration), and
judges faithfulness; the Leanstral leaf-prover runs the kernel gates
(hypothesis-rejection, disproof) and the proof itself.

Design of record: docs/superpowers/specs/2026-07-12-issue-to-stub-autoformalizer-design.md,
extended by 2026-07-17-semantic-repair-cascade-design.md (semantic gate rejections feed a
bounded re-draft loop instead of terminally skipping the issue; triviality gate; obstruction
telemetry). Pure logic here is unit-tested with injected chat_fn/check_fn (no Lean/API/
network). Stdlib only.
"""

from __future__ import annotations

import argparse
import dataclasses
import glob
import json
import os
import re
import subprocess
import sys
import time

import embed as _embed
from house_context import build_drafter_prompt, build_system_prompt, extract_signatures
from issues import difficulty_rank, select_issues
from pipeline_lib import AutoformalizeConfig, DrafterConfig
from gate_cache import GateCache
from probe import daemon_check, mistral_chat, run_target
from prose_slop import prose_slop_report
from probe_lib import DEF_RE, append_jsonl, extract_lean_code, lint_violations

# theorem/lemma decl, line-anchored so prose "theorem ..." in a docstring never
# matches. Captures the declaration name.

from af_parse import *  # noqa: F401,F403 — re-export the extracted parse
from af_prompts import *  # noqa: F401,F403 — re-export the extracted prompts
from af_routing import *  # noqa: F401,F403 — re-export the extracted routing
from af_drafting import *  # noqa: F401,F403 — re-export the extracted drafting
from af_gates import *  # noqa: F401,F403 — re-export the extracted gates




# --- item I phase 2: the agentic drafter (claude -p + lean-lsp MCP) ------------
# When [drafter] mode="agentic", the FORMALIZE stage becomes ONE `claude -p` session
# wired to the same lean-lsp MCP the vibe harness gives Leanstral: Claude drafts the
# stub to a scratch file, self-validates against Lean (lean_diagnostics / lean_goal),
# and iterates until it elaborates — its OWN tool loop instead of the fixed round-based
# completion repair. Needs the lean-lsp container up + the daemon down (one Lean
# process; the tick flips the slot). Design: 2026-07-24-frontier-drafter-design.md.
def _lean_lsp_mcp_config(container: str = "mathfin-lean-lsp", project: str = "/app") -> dict:
    """The `claude --mcp-config` JSON for the lean-lsp MCP — the SAME server (docker exec,
    stdio) the vibe harness wires for Leanstral (scripts/vibe-setup.sh)."""
    return {"mcpServers": {"lean-lsp": {
        "command": "docker",
        "args": ["exec", "-i", container, "lean-lsp-mcp", "--lean-project-path", project]}}}




def _agentic_formalize_args(mcp_config_path: str, *, model: str = "") -> list[str]:
    """`claude -p` argv for the agentic formalize session: the lean-lsp MCP (strict — no
    other MCP) plus the tools to draft a stub file and self-validate it against Lean."""
    argv = ["claude", "-p", "--output-format", "json",
            "--mcp-config", mcp_config_path, "--strict-mcp-config",
            "--allowedTools", "Read,Write,Edit,mcp__lean-lsp"]
    if model:
        argv += ["--model", model]
    return argv




_AGENTIC_SCRATCH_REL = "MathFin/_AutoformAgentic.lean"




def _agentic_formalize_prompt(intent: dict, scaffold_module: str, scratch_rel: str,
                              premises: str = "") -> str:
    """The task prompt for the agentic formalize session: write the module realizing `intent`
    to `scratch_rel`, using the given scaffold, and self-validate to elaboration via the
    lean-lsp tools (leaving the theorem's `sorry`). `premises` are embedding-retrieved
    likely-relevant declarations (whole-corpus semantic recall, complementing the live
    lean_loogle name search)."""
    stmt = intent.get("statement", "")
    defs = [d for d in (intent.get("definitions") or []) if isinstance(d, dict)]
    defspec = "\n".join(
        f"- {d.get('name')} : {d.get('signature', '?')} — {d.get('meaning', '')}" for d in defs)
    return (
        "Formalize the following into Lean 4 (Mathlib) and make it ELABORATE cleanly.\n\n"
        f"INTENDED STATEMENT:\n{stmt}\n\n"
        + (f"NEW DEFINITIONS TO INTRODUCE:\n{defspec}\n\n" if defspec else "")
        + (f"LIKELY-RELEVANT PREMISES (embedding recall; verify they elaborate under our pin):\n"
           f"{premises}\n\n" if premises else "")
        + f"Write the COMPLETE module to the file `{scratch_rel}` (relative to the project root), "
        "using EXACTLY this scaffold — replace only the body between the `open` lines and "
        "`end MathFin` with the definition(s) and the single theorem (ending `:= by sorry`), "
        "plus 1-2 concrete `example ... := by norm_num`/`decide` instance checks per new def:\n\n"
        f"```lean\n{scaffold_module}\n```\n\n"
        f"Then use the lean-lsp MCP tools (e.g. lean_diagnostics on `{scratch_rel}`) to CHECK and "
        "ITERATE — fix every elaboration error — until diagnostics report NO errors and exactly "
        "ONE `sorry` (the main theorem's). Keep the statement FAITHFUL to the intended statement; "
        "do NOT prove the theorem (leave its `sorry`); do not touch the license/module/import/"
        "namespace scaffold lines. Reply DONE when it elaborates cleanly.\n\n"
        + _AGENTIC_PITFALLS
    )




def _extract_core_stub(lean_text: str) -> str:
    """The body an emit-scaffolded module wraps: the text between the `open scoped …` line
    and `end MathFin` (the defs + theorem + examples). '' if the markers are absent."""
    m = re.search(r"open scoped NNReal ENNReal\n+(.*?)\n+end MathFin", lean_text, re.DOTALL)
    return m.group(1).strip() if m else ""




def agentic_formalize(intent: dict, *, issue: dict, main_repo: str, check_fn=None, model: str = "",
                      run_fn=None, mcp_config_path: str | None = None, premises: str = "",
                      scratch_rel: str = _AGENTIC_SCRATCH_REL, log=lambda m: None) -> dict:
    """Item I phase 2: ONE agentic `claude -p` session (lean-lsp MCP) drafts the module for
    `intent` to `scratch_rel` (under `main_repo`, bind-mounted into the lean-lsp container),
    self-validating to elaboration via lean tools. We read it back, daemon-verify, and
    re-emit for a consistent (lean_text, entry). ASSUMES lean-lsp is up + the daemon is up
    for `check_fn` — the caller flips the slot. `run_fn(argv, stdin, cwd)` / `check_fn`
    injectable for tests. Returns the formalize_with_repair shape
    `{ok, stub, lean_text, entry, meta, tokens, reason}`."""
    meta = {"module_name": intent["module_name"], "benchmark_id": intent["benchmark_id"],
            "docstring": intent.get("docstring", ""),
            "definitions": intent.get("definitions") or [], "deferred": intent.get("deferred")}
    scaffold, _e0, _p0 = emit_target_files(issue, "theorem _agentic_placeholder : True := by sorry", meta)
    scratch_abs = os.path.join(main_repo, scratch_rel)

    tmp_cfg = None
    if mcp_config_path is None:
        import tempfile
        fd, mcp_config_path = tempfile.mkstemp(suffix=".json", prefix="lean-lsp-mcp-")
        with os.fdopen(fd, "w") as f:
            json.dump(_lean_lsp_mcp_config(), f)
        tmp_cfg = mcp_config_path

    if run_fn is None:
        def run_fn(argv, stdin, cwd):
            return subprocess.run(argv, input=stdin, capture_output=True, text=True,
                                  timeout=1800, cwd=cwd)
    argv = _agentic_formalize_args(mcp_config_path, model=model)
    prompt = _agentic_formalize_prompt(intent, scaffold, scratch_rel, premises)
    try:
        res = run_fn(argv, prompt, main_repo)
    finally:
        if tmp_cfg:
            try:
                os.unlink(tmp_cfg)
            except OSError:
                pass
    tokens = 0
    try:
        data = json.loads((getattr(res, "stdout", "") or "").strip())
        u = data.get("usage") or {}
        tokens = int(u.get("input_tokens", 0) or 0) + int(u.get("output_tokens", 0) or 0)
    except (ValueError, TypeError):
        pass

    if not os.path.exists(scratch_abs):
        return {"ok": False, "stub": None, "lean_text": None, "entry": None, "meta": meta,
                "tokens": tokens, "reason": "agentic session wrote no file"}
    lean_final = open(scratch_abs, encoding="utf-8").read()
    if check_fn is not None:                         # None ⇒ trust claude's lean-lsp self-validation
        elab = check_fn(lean_final)                  # (the gate battery re-checks elaboration anyway)
        if elab.get("errors") or elab.get("sorry_count", 0) != 1:
            return {"ok": False, "stub": None, "lean_text": lean_final, "entry": None, "meta": meta,
                    "tokens": tokens, "reason": "final module does not elaborate cleanly"}
    core = _extract_core_stub(lean_final)
    if core:                                        # canonicalise via emit → consistent entry
        lean_text, entry, _placement = emit_target_files(issue, core, meta)
    else:                                           # markers absent — ship claude's module as-is
        lean_text, entry = lean_final, None
    return {"ok": True, "stub": core, "lean_text": lean_text, "entry": entry, "meta": meta,
            "tokens": tokens, "reason": ""}




# --- kernel-grade faithfulness gates (labs-leanstral via run_target) ----------

# Lightened gates. Each faithfulness-gate attempt is a Lean daemon elaboration, and
# the two gates were the bulk of an issue's daemon load (fanout 2 x 2 rounds each ≈ 8
# checks) — the load that let one spinning candidate wedge the daemon. The gate is a
# cheapest-first SAFETY NET (catch a gross vacuity / falsity), not a proof to
# maximize: one reasoned pass@1 attempt catches a blatant contradiction, and a subtle
# one is left to the semantic judge + the human merge. Default to pass@1 / single
# round (1 check per gate); tunable per-call for a deeper sweep.
_GATE_FANOUT = 1


_GATE_ROUNDS = 1




def _try_prove(goal: str, sorry_name: str, *, chat_fn, check_fn, budget: int,
               fanout: int = _GATE_FANOUT, rounds: int = _GATE_ROUNDS,
               system_prompt=None, cache=None) -> tuple[bool, int]:
    """Short pass@k attempt to prove `goal`. Returns `(proved, tokens)` — `proved`
    is True only on an axioms-clean success (run_target's `pass`) whose winning
    candidate still asserts the PROBED conclusion. `rounds` bounds both the sampling
    rounds and the compiler-feedback repairs (`rounds - 1`). With a `cache` (item L),
    a previously-seen goal returns its stored verdict for 0 tokens (a refutation is
    deterministic, so substituting it is safe within a toolchain generation)."""
    if cache is not None:
        hit = cache.get(goal)
        if hit is not None:
            return hit, 0
    target = {"id": "gate", "stream": "gate", "statement": goal, "sorry_name": sorry_name}
    res = run_target(target, budget=budget, max_rounds=rounds, chat_fn=chat_fn,
                     check_fn=check_fn, log_fn=lambda r: None, system_prompt=system_prompt,
                     fanout=fanout, repair_rounds=max(0, rounds - 1))
    if res["outcome"] != "pass":
        proved = False
    else:
        # Statement-fidelity guard. run_target checks whatever FILE the prover returns,
        # with no pin on the statement — so an adversarial prover (vacuity: prove `False`;
        # disproof: prove `¬C`) that cannot close the probed conclusion instead REVERTS it
        # to the provable original and "passes". That false verdict deterministically kills
        # easy TRUE targets (caught on cal-bk-161: `0 ≤ gainToPain` reverted from `False`).
        # Count the pass only when the winning candidate still asserts the probed conclusion.
        want = _probed_conclusion(goal, sorry_name)
        got = _probed_conclusion(target.get("_winning_candidate", ""), sorry_name)
        proved = (want is not None and got == want)
    if cache is not None:
        cache.put(goal, proved)
    return proved, res["tokens"]




def hypothesis_rejection(lean_text: str, sorry_name: str, *, chat_fn, check_fn,
                         budget: int, fanout: int = _GATE_FANOUT, rounds: int = _GATE_ROUNDS,
                         system_prompt=None, cache=None) -> dict:
    """Try to prove `⊢ False` from the stub's hypotheses. A clean proof ⇒ the
    hypotheses are contradictory ⇒ the theorem is vacuously true. Returns
    `{vacuous, tokens}`."""
    proved, tokens = _try_prove(vacuity_goal(lean_text), sorry_name, chat_fn=chat_fn,
                                check_fn=check_fn, budget=budget, fanout=fanout, rounds=rounds,
                                system_prompt=system_prompt, cache=cache)
    return {"vacuous": proved, "tokens": tokens}




def disproof(lean_text: str, sorry_name: str, *, chat_fn, check_fn,
             budget: int, fanout: int = _GATE_FANOUT, rounds: int = _GATE_ROUNDS,
             system_prompt=None, cache=None) -> dict:
    """Try to prove `⊢ ¬ Concl` under the stub's hypotheses. A clean proof ⇒ the
    statement is false as written. Returns `{false, tokens}`."""
    proved, tokens = _try_prove(disproof_goal(lean_text), sorry_name, chat_fn=chat_fn,
                                check_fn=check_fn, budget=budget, fanout=fanout, rounds=rounds,
                                system_prompt=system_prompt, cache=cache)
    return {"false": proved, "tokens": tokens}




# --- issue preparation --------------------------------------------------------

_POINTER_RE = re.compile(r"MathFin/[\w/]+\.lean")




def extract_pointers(body: str) -> list[str]:
    """Repo-relative `MathFin/…/X.lean` paths named in an issue body (its Pointers
    section), de-duplicated in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for p in _POINTER_RE.findall(body or ""):
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out




_LOCATION_RE = re.compile(r"(?im)^\s*location:\s*(MathFin/[\w/]+\.lean)")


def extract_location(body: str) -> str | None:
    """The module an issue's `location:` line names, or None if it has no such line.

    R writes these deliberately — "location: MathFin/Performance/RatiosExtended.lean
    (beside sortinoRatio / informationRatio)" — to say the new result belongs with its
    siblings rather than in a module of its own. Emit honours it via `placement.append`
    (backlog S); before that it was captured into the `-- pointers:` header and then
    silently overridden."""
    m = _LOCATION_RE.search(body or "")
    return m.group(1) if m else None


def prepare_issues(raw: list[dict], *, max_difficulty: str = "medium") -> list[dict]:
    """Filter+order the raw `gh issue list` output to the tractable
    `status:ready`+`type:proof` queue (via `issues.select_issues`) and enrich each
    with its `body` + extracted `pointers` for drafting."""
    by_num = {r.get("number"): r for r in raw}
    out = []
    for s in select_issues(raw, max_difficulty=max_difficulty):
        body = by_num.get(s["number"], {}).get("body") or ""
        out.append({**s, "body": body, "pointers": extract_pointers(body)})
    return out




def semantic_verdict(*, lean_text: str, stub: str, name: str, intent: dict, issue: dict,
                     deferred: list[str], reason_fn, prove_fn, check_fn, gate_budget: int,
                     depth_gate: bool = True, triviality_gate: bool = True,
                     route: str = "theorem", def_names: list[str] | None = None,
                     system_prompt=None, cache=None) -> tuple[dict | None, int]:
    """Run the semantic gate battery on an ELABORATING draft, cheapest-first:
    depth (theorem route) / defs consumption+grounding (defs route) → triviality
    (structural, zero tokens) → hypothesis-rejection → disproof (kernel,
    leanstral) → issue-faithfulness (Claude judge).
    Returns `(failure, tokens)`: failure is None when every gate passes, else
    `{gate, detail}` for `render_gate_feedback`. The battery's DIVERSITY is the
    anti-Goodhart defense of the repair loop: a re-draft that games one gate still
    faces five others (and open-pr's honesty guards + the human merge after that)."""
    tokens = 0
    if route == "defs":
        dr = defs_rejection(lean_text, name, def_names or [], check_fn=check_fn)
        tokens += dr["tokens"]
        if dr.get("indeterminate"):   # H5: infra, not a verdict — retryable
            return {"gate": "indeterminate", "detail": dr.get("verdict", "")}, tokens
        if dr["failed"]:
            return {"gate": dr["gate"], "detail": dr.get("verdict", "")}, tokens
        ip = instance_probe_rejection(lean_text, def_names or [])
        tokens += ip["tokens"]
        if ip["failed"]:                    # item M: each new def needs a concrete probe
            return {"gate": ip["gate"], "detail": ip.get("verdict", "")}, tokens
    elif depth_gate:
        dep = depth_rejection(lean_text, name, issue.get("pointers", []), check_fn=check_fn)
        tokens += dep["tokens"]
        if dep.get("indeterminate"):
            return {"gate": "indeterminate", "detail": dep.get("verdict", "")}, tokens
        if dep["shallow"]:
            return {"gate": "depth", "detail": dep.get("verdict", "")}, tokens
    if triviality_gate:
        triv = triviality_rejection(lean_text, check_fn=check_fn)
        tokens += triv["tokens"]
        if triv.get("indeterminate"):
            return {"gate": "indeterminate", "detail": triv.get("verdict", "")}, tokens
        if triv["trivial"]:
            return {"gate": "trivial", "detail": triv.get("verdict", "")}, tokens
    vac = hypothesis_rejection(lean_text, name, chat_fn=prove_fn, check_fn=check_fn,
                               budget=gate_budget, system_prompt=system_prompt, cache=cache)
    tokens += vac["tokens"]
    if vac["vacuous"]:
        return {"gate": "vacuous", "detail": "False is provable from the hypotheses"}, tokens
    dis = disproof(lean_text, name, chat_fn=prove_fn, check_fn=check_fn,
                   budget=gate_budget, system_prompt=system_prompt, cache=cache)
    tokens += dis["tokens"]
    if dis["false"]:
        return {"gate": "false", "detail": "the negated conclusion was proved"}, tokens
    j = judge_faithfulness(issue, stub, chat_fn=reason_fn, deferred=deferred)
    tokens += j["tokens"]
    if not j.get("faithful"):
        detail = j.get("verdict", "")
        if j.get("issues"):
            detail += "; issues: " + "; ".join(str(x) for x in j["issues"][:4])
        return {"gate": "unfaithful", "detail": detail}, tokens
    return None, tokens




# --- the refill orchestrator --------------------------------------------------

def _write_target(queue_dir: str, n: int, lean_text: str, entry: dict) -> list[str]:
    """Write the stub + its `.entry.json` sidecar into the queue dir. Returns the
    two paths written."""
    os.makedirs(queue_dir, exist_ok=True)
    stub_path = os.path.join(queue_dir, f"cal-bk-{n}.lean")
    entry_path = os.path.join(queue_dir, f"cal-bk-{n}.entry.json")
    with open(stub_path, "w", encoding="utf-8") as f:
        f.write(lean_text)
    with open(entry_path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return [stub_path, entry_path]




def route_feasibility(intent: dict, pointers: list[str], *, lookup_fn) -> dict:
    """Feasibility census at intent time (H12): the `MathFin.*` primitives the intent
    NAMES but that exist neither in the pointer modules nor in the pin index.
    `lookup_fn(name) -> bool` reports existence (scout index, fallback grep). ≥1
    missing ⇒ `feasible=False` — drafting is doomed (the #1 recorded death family,
    `needs_primitives`), so record `blocked_on_infra` with the missing list and a
    suggested-defs note rather than burning a formalize budget on a hallucinated
    constant. Mathlib names are not checked (out of our authority; the elaborator
    gates those). Returns `{feasible, missing, note}`."""
    named = [o for o in (intent.get("objects") or [])
             if isinstance(o, str) and o.startswith("MathFin.")]
    missing = [o for o in named if not lookup_fn(o)]
    if not missing:
        return {"feasible": True, "missing": [], "note": ""}
    note = ("intent names MathFin primitives that do not exist yet: "
            + ", ".join(missing) + " — route to the defs stage (introduce them) or "
            "leave a human issue comment; do not draft against invented constants.")
    return {"feasible": False, "missing": missing, "note": note}




def _record_refill_experience(store, rec: dict, *, summarize_fn=None) -> None:
    """Fold one failed refill record into its issue's rolling notebook (item K, draft side).

    Skips the non-verdict families for the same reason `load_prior_lessons` does — a win, a
    budget cutoff, or retryable infra has no lesson in it, and recording one would burn a
    rotation on noise. A seeded issue additionally has its notebook RETIRED: the issue is
    off the queue, and a later regression should start from a clean sheet rather than
    inherit the notes of the attempts that eventually succeeded.

    Never raises. This runs on the failure path of a tick that is already going badly.
    """
    if store is None:
        return
    try:
        issue = rec.get("issue")
        if issue is None:
            return
        key = f"issue-{issue}"
        family = rec.get("family")
        if family in _LESSON_SKIP:
            store.forget(key)
            return
        history = rec.get("history") or []
        last = history[-1] if history else {}
        store.record(key, {"outcome": rec.get("outcome", ""),
                           "reason": last.get("gate", ""),
                           "errors": [last.get("detail", "")] if last.get("detail") else [],
                           "notes": f"family: {family}" if family else ""},
                     summarize_fn=summarize_fn)
    except Exception:      # noqa: BLE001 — memory must never fail a tick
        pass


def refill(issues: list[dict], *, reason_fn, prove_fn, check_fn, context_fn, intent_fn=None,
           agentic_formalize_fn=None, slot_switch_fn=None,
           queue_dir: str, budget: int, max_issues: int = 1,
           max_attempt_issues: int = 3, gate_budget: int = 20_000, formalize_rounds: int = 3,
           proactive_fn=None, depth_gate: bool = True, triviality_gate: bool = True,
           semantic_rounds: int = 2, system_prompt=None,
           feasibility_fn=None, gate_cache=None, experience=None, summarize_fn=None,
           log=lambda m: None) -> dict:
    """Draft + gate + stage up to `max_issues` targets from `issues`.

    For each candidate (up to `max_attempt_issues`): intent (`reason_fn`, Claude
    SPECIFIES the statement) → agentic formalize (`agentic_formalize_fn`, Claude +
    the lean-lsp MCP writes elaborating Lean, self-validating to elaboration — the
    only drafter) → the semantic gate battery (`semantic_verdict`: depth →
    triviality → vacuity → disproof → judge, leanstral proving the
    kernel gates). A gate rejection is NOT terminal: it becomes a
    `render_gate_feedback` block and the issue is RE-DRAFTED — both stages see the
    verdict — up to `semantic_rounds` total attempts (the repair cascade; design:
    2026-07-17-semantic-repair-cascade). `semantic_rounds=1` is the old single-shot
    behavior. An issue that exhausts its rounds stays `status:ready` (never
    auto-closed). A passing target's stub + `.entry.json` are written to `queue_dir`.

    Returns `{seeded, tokens, attempted}` — `attempted` is the obstruction telemetry:
    one `{issue, attempts, outcome: "seeded"|<last gate>|"error", history}` record per
    issue tried, so a zero-seed tick says exactly which gate ate each issue."""
    intent_fn = intent_fn or reason_fn   # the intent DRAFT chat_fn (claude); judge keeps reason_fn
    seeded, attempted, spent = [], [], 0
    for issue in issues[:max_attempt_issues]:
        if len(seeded) >= max_issues or spent >= budget:
            break
        n = issue.get("number")
        route = issue.get("route", "theorem")
        # item K: a cross-tick post-mortem + rotating diversity nudge from failed prior
        # ticks (attached in main() from refill-history). Rendered once; rides every
        # attempt's intent prompt alongside this tick's own intra-tick `feedback`.
        _lesson = issue.get("prior_lessons")
        lesson_note = render_prior_lessons(_lesson) if _lesson else None
        # `render_prior_lessons` is derived FRESH from refill-history each tick and keeps
        # only the latest record: after four failed ticks the drafter sees tick 4's gate
        # names and its last_detail (≤200 chars), and ticks 1-3 are gone. The rolling
        # notebook is the accumulating half — it carries what each earlier tick actually
        # tried, bounded by re-summarisation rather than by truncation. `nudge=False`:
        # `render_prior_lessons` already owns the diversity rotation here.
        if experience is not None:
            _nb = experience.render(f"issue-{n}", nudge=False)
            if _nb:
                lesson_note = f"{lesson_note}\n{_nb}" if lesson_note else _nb
        history, feedback, staged = [], None, False
        tele = {"advised_bundle": False, "lint_repaired": 0, "retrieval_backend": None,
                "prose_slop": 0}  # H11; prose_slop (SP4): AI-slop markers in the drafted docstring
        # A transient error on ONE issue (e.g. an HTTP 429 that exhausts retries,
        # a daemon hiccup, a malformed draft) must not kill the tick — log it and
        # move to the next candidate.
        try:
            ctx = context_fn(issue)
            for attempt in range(1, max(1, semantic_rounds) + 1):
                if spent >= budget:
                    history.append({"attempt": attempt, "gate": "budget",
                                    "detail": f"refill budget exhausted ({spent} >= {budget})"})
                    break
                di = draft_intent(issue, ctx, chat_fn=intent_fn, feedback=feedback,
                                  route=route, prior_unknowns=issue.get("prior_unknowns"),
                                  prior_lessons=lesson_note)
                spent += di["tokens"]
                if not di["ok"]:
                    fail = {"gate": "intent",
                            "detail": di.get("reason") or "no parseable intent reply"}
                    history.append({"attempt": attempt, **fail})
                    log(f"#{n}: intent rejected — {fail['detail']} (attempt {attempt})")
                    feedback = render_gate_feedback(fail["gate"], fail["detail"], None)
                    continue
                intent = di["intent"]
                ps = prose_slop_report(intent.get("docstring") or "")   # SP4: surface docstring slop
                tele["prose_slop"] = ps["count"]
                if ps["count"]:
                    log(f"#{n}: docstring prose-slop {ps['flags']} (attempt {attempt})")
                if route == "defs" and not (intent.get("definitions") or []):
                    fail = {"gate": "intent",
                            "detail": "defs route: the intent must name 1-3 new definitions"}
                    history.append({"attempt": attempt, **fail})
                    log(f"#{n}: intent named no definitions (attempt {attempt})")
                    feedback = render_gate_feedback(fail["gate"], fail["detail"], None)
                    continue
                # H12: feasibility census — if the intent names MathFin primitives that
                # don't exist yet (the #1 death family), record blocked_on_infra + the
                # missing list and STOP, never burning a formalize budget on a doomed
                # draft. The defs route is exempt (it is allowed to introduce new defs).
                if feasibility_fn is not None and route != "defs":
                    feas = route_feasibility(intent, issue.get("pointers", []),
                                             lookup_fn=feasibility_fn)
                    if not feas["feasible"]:
                        row = {"attempt": attempt, "gate": "blocked_on_infra",
                               "detail": feas["note"], "missing": feas["missing"]}
                        history.append(row)
                        log(f"#{n}: blocked_on_infra — missing {feas['missing']} "
                            f"(attempt {attempt})")
                        break   # doomed target — surface for the defs route / a human

                # FORMALIZE: the agentic claude + lean-lsp session (the only drafter). The tick
                # flips the single Lean slot to lean-lsp around it and back to the daemon for the
                # gates. An infra failure (lean-lsp/flip/docker) or a ClaudeCapError propagates to
                # the issue-level handler (error/deferred, retryable) — there is no completion path.
                proactive = proactive_fn(intent["statement"]) if proactive_fn else ""
                if agentic_formalize_fn is None:
                    raise RuntimeError("refill requires an agentic_formalize_fn (the only drafter)")
                try:
                    if slot_switch_fn is not None:
                        slot_switch_fn("lean-lsp")
                    fr = agentic_formalize_fn(intent, ctx, issue, premises=proactive)
                finally:
                    if slot_switch_fn is not None:
                        try:
                            slot_switch_fn("daemon")
                        except Exception as e:  # noqa: BLE001
                            log(f"#{n}: slot flip back to daemon failed ({e})")
                spent += fr["tokens"]
                unknowns = fr.get("unknowns") or []
                if fr.get("advised_bundle"):
                    tele["advised_bundle"] = True
                tele["lint_repaired"] += fr.get("lint_repaired", 0)
                if fr.get("retrieval_backend"):
                    tele["retrieval_backend"] = fr["retrieval_backend"]
                if not fr["ok"]:
                    fail = {"gate": "formalize",
                            "detail": f"no elaborating Lean after {formalize_rounds} rounds"}
                    row = {"attempt": attempt, **fail}
                    if unknowns:
                        row["unknown_identifiers"] = unknowns
                    history.append(row)
                    log(f"#{n}: {fail['detail']} (attempt {attempt})")
                    feedback = render_gate_feedback(fail["gate"], fail["detail"], None)
                    continue
                stub, lean_text, entry = fr["stub"], fr["lean_text"], fr["entry"]
                name = split_statement(stub)[0]
                deferred = normalize_deferred((fr.get("meta") or {}).get("deferred"))

                fail, gate_tokens = semantic_verdict(
                    lean_text=lean_text, stub=stub, name=name, intent=intent, issue=issue,
                    deferred=deferred, reason_fn=reason_fn, prove_fn=prove_fn,
                    check_fn=check_fn, gate_budget=gate_budget, depth_gate=depth_gate,
                    triviality_gate=triviality_gate, route=route,
                    def_names=drafted_def_names(stub) if route == "defs" else None,
                    system_prompt=system_prompt, cache=gate_cache)
                spent += gate_tokens
                if fail is None:
                    paths = _write_target(queue_dir, n, lean_text, entry)
                    seeded.append({"id": f"cal-bk-{n}", "issue": n, "paths": paths})
                    staged = True
                    log(f"#{n}: staged cal-bk-{n} (attempt {attempt})")
                    break
                row = {"attempt": attempt, **fail}
                if unknowns:
                    row["unknown_identifiers"] = unknowns
                history.append(row)
                log(f"#{n}: {fail['gate']} — {fail['detail']} (attempt {attempt})\n"
                    f"  statement: {stub}")
                if fail["gate"] == "indeterminate":
                    # H5: a wedged daemon is not a verdict — stop re-drafting (futile
                    # against the same wedged daemon) and leave the issue UNSEEDED and
                    # retryable for the next tick, never a false seed or rejection.
                    log(f"#{n}: indeterminate (daemon infra) — deferring to next tick")
                    break
                feedback = render_gate_feedback(fail["gate"], fail["detail"], stub)
            if staged:
                outcome = "seeded"
            elif history and history[-1]["gate"] == "indeterminate":
                outcome = "indeterminate"   # retryable infra hiccup, not a real rejection
            elif history:
                outcome = history[-1]["gate"]
            else:
                outcome = "error"
            rec = {"issue": n, "attempts": attempt, "outcome": outcome, "history": history,
                   "arch": ROUTING_ARCH, "telemetry": tele}
            rec["family"] = classify_refill(rec)
            _record_refill_experience(experience, rec, summarize_fn=summarize_fn)
            attempted.append(rec)
        except ClaudeCapError as e:   # item I: subscription cap under [drafter] on_cap="defer"
            log(f"#{n}: claude drafter cap — deferring (requeue, no obstruction)")
            rec = {"issue": n, "attempts": len(history), "outcome": "deferred",
                   "history": history + [{"gate": "deferred", "detail": str(e)[:120]}],
                   "arch": ROUTING_ARCH}
            attempted.append(rec)          # deferred is NOT classified as an obstruction
            continue
        except Exception as e:  # noqa: BLE001 — resilience: skip the issue, not the tick
            log(f"#{n}: error ({type(e).__name__}: {e}) — skipping")
            history.append({"attempt": len(history) + 1, "gate": "error",
                            "detail": f"{type(e).__name__}: {e}"})
            rec = {"issue": n, "attempts": len(history), "outcome": "error",
                   "history": history, "arch": ROUTING_ARCH}
            rec["family"] = classify_refill(rec)
            attempted.append(rec)
            continue
    return {"seeded": seeded, "tokens": spent, "attempted": attempted}




# --- CLI (the refill entrypoint pipeline-tick.sh calls) -----------------------

def _fetch_issues(slug: str) -> list[dict]:
    out = subprocess.run(
        ["gh", "issue", "list", "--repo", slug, "--state", "open", "--limit", "100",
         "--json", "number,title,labels,body"],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)




def _already_seeded(queue_dir: str) -> set[int]:
    """Issue numbers already staged as `cal-*-<N>.lean` in the queue."""
    nums: set[int] = set()
    for p in glob.glob(os.path.join(queue_dir, "cal-*.lean")):
        m = re.search(r"cal-\w+-(\d+)\.lean$", os.path.basename(p))
        if m:
            nums.add(int(m.group(1)))
    return nums




def _foundry_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))




def build_retrieve_fns(*, backend, main_repo, index_dir, k, embed_model, api_key):
    """(reactive_retrieve_fn, proactive_fn). Embedding backend ranks the whole
    MathFin corpus; proactive_fn retrieves on the intent STATEMENT. Falls open to
    loogle (reactive only) when the embedding cache is absent."""
    loogle_fn = lambda nm: loogle_candidates(nm, main_repo=main_repo)  # noqa: E731
    loogle_fn.backend = "loogle"   # H11 telemetry label
    if backend != "embedding":
        return loogle_fn, None
    premises = _embed.load_premises(index_dir)
    cache = _embed.cache_path(index_dir, embed_model)
    idx = _embed.EmbeddingIndex.load(cache, premises, embed_model) if premises else None
    if idx is None:
        return loogle_fn, None   # fails-open — no index/cache ⇒ loogle
    embed_fn = lambda texts: _embed.mistral_embed(texts, api_key=api_key, model=embed_model)  # noqa: E731
    reactive = _embed.make_embedding_retrieve_fn(idx, k, embed_fn)
    try:
        reactive.backend = "embedding"   # H11 telemetry label (skip if not settable)
    except (AttributeError, TypeError):
        pass
    proactive = lambda stmt: idx.retrieve(stmt, k, embed_fn)  # noqa: E731
    return reactive, proactive




def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("refill", help="draft+gate+stage the next ready issue")
    p.add_argument("--main-repo", required=True)
    p.add_argument("--config", default=None,
                   help="pipeline.toml for [autoformalize] defaults (default: <foundry>/pipeline.toml)")
    p.add_argument("--slug", default="formal-applied-math/formal-mathfin")
    p.add_argument("--queue-dir", default=None, help="default: <foundry>/targets/queue")
    p.add_argument("--only", type=int, default=None, help="attempt only this issue number")
    p.add_argument("--route", choices=["theorem", "defs"], default=None,
                   help="force the route, overriding the auto classifier (default: auto)")
    # the rest override the [autoformalize] config only when given (default: None)
    p.add_argument("--budget", type=int, default=None)
    p.add_argument("--max-issues", type=int, default=None)
    p.add_argument("--max-attempt-issues", type=int, default=None)
    p.add_argument("--gate-budget", type=int, default=None)
    p.add_argument("--prover-model", default=None)   # leanstral: the gate battery
    p.add_argument("--formalize-rounds", type=int, default=None)
    p.add_argument("--depth-gate", dest="depth_gate", action=argparse.BooleanOptionalAction,
                   default=None, help="pointers-scoped depth gate (default: config)")
    p.add_argument("--triviality-gate", dest="triviality_gate",
                   action=argparse.BooleanOptionalAction, default=None,
                   help="rfl/simp triviality gate (default: config)")
    p.add_argument("--semantic-rounds", type=int, default=None,
                   help="total draft attempts per issue incl. feedback re-drafts (default: config)")
    p.add_argument("--retrieval", dest="retrieval", action=argparse.BooleanOptionalAction,
                   default=None, help="loogle-augmented repair retrieval (default: config)")
    args = ap.parse_args()

    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("MISTRAL_API_KEY not set", file=sys.stderr)
        return 2

    # pipeline.toml [autoformalize] is authoritative; CLI flags override per-field.
    cfg = AutoformalizeConfig.load(args.config or os.path.join(_foundry_root(), "pipeline.toml"))
    pick = lambda a, c: a if a is not None else c
    budget = pick(args.budget, cfg.budget)
    max_issues = pick(args.max_issues, cfg.max_issues)
    max_attempt = pick(args.max_attempt_issues, cfg.max_attempt_issues)
    gate_budget = pick(args.gate_budget, cfg.gate_budget)
    prover_model = pick(args.prover_model, cfg.prover_model)   # leanstral: the gate battery
    depth_gate = pick(args.depth_gate, cfg.depth_gate)
    triviality_gate = pick(args.triviality_gate, cfg.triviality_gate)
    semantic_rounds = pick(args.semantic_rounds, cfg.semantic_rounds)
    formalize_rounds = pick(args.formalize_rounds, cfg.formalize_rounds)
    retrieval = pick(args.retrieval, cfg.retrieval)

    queue_dir = args.queue_dir or os.path.join(_foundry_root(), "targets", "queue")

    seeded_nums = _already_seeded(queue_dir)
    issues = [i for i in prepare_issues(_fetch_issues(args.slug))
              if i["number"] not in seeded_nums
              and (args.only is None or i["number"] == args.only)]
    if not issues:
        print(json.dumps({"seeded": [], "tokens": 0, "reason": "no unseeded ready issues"}))
        return 0

    # primitives-aware routing: measure each issue's consumables, consult the
    # refill history's runtime evidence, route theorem/defs, cheap wins first.
    hist_path = os.path.join(_foundry_root(), "runs", "refill-history.jsonl")
    families = load_refill_families(hist_path)
    prior_unknowns = load_prior_unknowns(hist_path)
    prior_lessons = load_prior_lessons(hist_path)   # item K: cross-tick post-mortems
    for i in issues:
        i["def_count"] = count_pointer_defs(args.main_repo, i.get("pointers", []))
        i["family"] = families.get(i["number"])
        i["route"] = resolve_route(args.route, i, def_count=i["def_count"], family=i["family"])
        i["prior_unknowns"] = prior_unknowns.get(i["number"], [])
        i["prior_lessons"] = prior_lessons.get(i["number"])
    issues = order_by_route(issues)
    print(f"[refill] routes: " + ", ".join(f"#{i['number']}→{i['route']}" for i in issues[:8]),
          file=sys.stderr)

    prove_system = build_system_prompt(args.main_repo)   # the leaf-prover gate doctrine
    set_drafter_prompt(args.main_repo)   # H1: pins + statement-design reach the drafter too

    def prove_fn(msgs):   # leanstral: the kernel gate battery (vacuity / disproof)
        return mistral_chat(msgs, api_key=api_key, model=prover_model,
                            reasoning_effort="high")

    # the drafter: ONE claude does intent + agentic formalize + the faithfulness/intent-
    # fidelity JUDGE; leanstral PROVES. no magistral, no completion mode, no fallback drafter.
    drafter = DrafterConfig.load(args.config or os.path.join(_foundry_root(), "pipeline.toml"))
    if not claude_auth_present():
        print("[refill] no Claude auth (CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY / "
              "~/.claude login) — every draft/judge defers this tick", file=sys.stderr)
    claude_fn = claude_chat_fn(drafter)      # intent draft + judge (one claude, one model)
    draft_intent_fn = claude_fn
    judge_fn = claude_fn
    print(f"[refill] drafter=claude model={drafter.claude_model} mode=agentic | judge=claude",
          file=sys.stderr)

    # FORMALIZE is always the agentic claude -p + lean-lsp session (self-validates to
    # elaboration); the tick flips the single Lean slot to lean-lsp around it and back to the
    # daemon for the gates. check_fn=None defers the elaboration check to the gate battery.
    def agentic_fn(intent, ctx, issue, premises=""):
        return agentic_formalize(intent, issue=issue, main_repo=args.main_repo,
                                 model=drafter.claude_model, check_fn=None, premises=premises,
                                 log=lambda m: print(f"[agentic] {m}", file=sys.stderr))

    _slot_script = os.path.join(_foundry_root(), "scripts", "slot-switch.sh")

    def slot_switch_fn(slot):        # flip the single Lean slot around the agentic formalize
        subprocess.run(["bash", _slot_script, slot], check=True,
                       env={**os.environ, "MAIN_REPO": args.main_repo})

    def context_fn(issue):
        ptrs = issue.get("pointers", [])
        return extract_signatures(args.main_repo, ptrs) if ptrs else ""

    from scout_index import default_index_dir
    index_dir = default_index_dir()
    if retrieval:
        # only the PROACTIVE (embedding) half is used — fed to the agentic prompt as premises;
        # the reactive retrieve_fn is gone (agentic searches live via lean_loogle).
        _reactive, proactive_fn = build_retrieve_fns(
            backend=cfg.retrieval_backend, main_repo=args.main_repo, index_dir=index_dir,
            k=cfg.retrieval_k, embed_model=cfg.embed_model, api_key=api_key)
    else:
        proactive_fn = None

    # H12 feasibility census: does a named MathFin.* primitive exist? scout index
    # first (authoritative), then a grep of the main-repo MathFin/ sources. Fail-open
    # (return True) whenever neither can confidently decide, so the census only ever
    # blocks a target it is SURE names a missing primitive — never a good one.
    from scout_index import ScoutIndex
    _feas_idx = ScoutIndex(index_dir)

    def feasibility_fn(name: str) -> bool:
        if _feas_idx.available and _feas_idx.signature_of(name) is not None:
            return True
        short = re.escape(name.rsplit(".", 1)[-1])
        try:
            out = subprocess.run(
                ["grep", "-rlE", rf"(def|theorem|lemma|abbrev|structure)[[:space:]]+{short}\b",
                 os.path.join(args.main_repo, "MathFin")],
                capture_output=True, text=True, timeout=15)
            return bool(out.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            return True   # can't check ⇒ fail-open (never block a good target)

    # item L: the adversarial-gate goal cache (opt-in via [autoformalize].gate_cache).
    # vacuity/disproof verdicts are content-addressed + reused across attempts and ticks.
    gate_cache = (GateCache(os.path.join(_foundry_root(), "runs", "gate-cache.json"))
                  if cfg.gate_cache else None)
    if gate_cache is not None:
        print("[refill] gate cache ON", file=sys.stderr)

    # item K, draft side: the rolling notebook, sharing the store the prove path writes
    # (one file, disjoint key spaces — `issue-<n>` here, target ids there). This is where
    # the obstruction census says the failures are: 19 of 22 obstructions are drafter-side.
    experience = summarize_fn = None
    if cfg.experience:
        from experience import ExperienceStore
        experience = ExperienceStore(os.path.join(_foundry_root(), "runs", "experience.json"))
        if os.environ.get("EXPERIENCE_LLM", "1") != "0" and os.environ.get("MISTRAL_API_KEY"):
            _key = os.environ["MISTRAL_API_KEY"]
            summarize_fn = lambda msgs: mistral_chat(  # noqa: E731
                msgs, api_key=_key, max_tokens=800, temperature=0.2)
        print("[refill] experience memory ON", file=sys.stderr)

    res = refill(issues, reason_fn=judge_fn, intent_fn=draft_intent_fn, prove_fn=prove_fn,
                 agentic_formalize_fn=agentic_fn, slot_switch_fn=slot_switch_fn, check_fn=daemon_check,
                 context_fn=context_fn, queue_dir=queue_dir, budget=budget,
                 max_issues=max_issues, max_attempt_issues=max_attempt, gate_budget=gate_budget,
                 formalize_rounds=formalize_rounds, proactive_fn=proactive_fn,
                 depth_gate=depth_gate, triviality_gate=triviality_gate,
                 semantic_rounds=semantic_rounds, system_prompt=prove_system,
                 feasibility_fn=feasibility_fn, gate_cache=gate_cache,
                 experience=experience, summarize_fn=summarize_fn,
                 log=lambda m: print(f"[refill] {m}", file=sys.stderr))
    if gate_cache is not None:
        print(f"[refill] gate cache stats: {gate_cache.stats}", file=sys.stderr)

    # obstruction telemetry: one row per issue tried, so a zero-seed tick says which
    # gate ate each issue and whether feedback moved the draft between rounds
    # (triage.py's analogue for the drafter).
    hist = os.path.join(_foundry_root(), "runs", "refill-history.jsonl")
    os.makedirs(os.path.dirname(hist), exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    for rec in res.get("attempted", []):
        append_jsonl(hist, {"ts": stamp, **rec})

    print(json.dumps(res))
    return 0




def explicit_arg_names(binders: str) -> list[str]:
    """Names of the EXPLICIT `(…)` binders, in order — the arguments a re-export
    passes to the module lemma. `{…}` implicit and `[…]` instance binders are
    inferred, so they are omitted."""
    names: list[str] = []
    depth, start = 0, -1
    for i, c in enumerate(binders):
        if c == "(":
            if depth == 0:
                start = i + 1
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0 and start != -1:
                group = binders[start:i]
                colon = group.find(":")
                head = group[:colon] if colon != -1 else group
                names.extend(head.split())
                start = -1
    return names




# --- strengthen: post-proof unused-hypothesis stripping ------------------------
# 2/2 production PRs shipped a hypothesis the finished proof never used (#123
# hTn, #124 hσ_eq). Unused-ness is a property of the PROOF, so it is only
# knowable after the vibe prover closes the goal (Lean suppresses the
# unusedVariables linter under `sorry`) — hence a gate-time transform, not a
# draft-time gate. Dropping an unused hypothesis can only STRENGTHEN the
# theorem, so the fidelity direction is safe by construction; the full kernel
# gate re-runs on the stripped statement before it is accepted.


def _locate_named(text: str, name: str) -> tuple[int, int, int]:
    """`_locate` spans `(bstart, sep, end)` for the SPECIFIC decl `name` — the
    proved candidate may hold vibe-added helper lemmas before the main theorem."""
    m = re.search(rf"^\s*(?:@\[[^\]]*\]\s*)?(?:theorem|lemma)\s+{re.escape(name)}(?![A-Za-z0-9_'.])",
                  text, re.MULTILINE)
    if not m:
        raise ValueError(f"declaration `{name}` not found")
    off = m.start()
    _n, bstart, sep, end = _locate(text[off:])
    return off + bstart, off + sep, off + end




def _binder_groups(binders: str) -> list[tuple[int, int, str, list[str] | None]]:
    """Top-level binder groups: `(start, end, opener, names)`, end exclusive;
    `names` is None for `{…}`/`[…]` groups (inferred binders — never stripped)."""
    groups: list[tuple[int, int, str, list[str] | None]] = []
    depth, start, opener = 0, -1, ""
    for i, c in enumerate(binders):
        if c in _OPEN:
            if depth == 0:
                start, opener = i, c
            depth += 1
        elif c in _CLOSE:
            depth -= 1
            if depth == 0 and start != -1:
                names = None
                if opener == "(":
                    group = binders[start + 1:i]
                    colon = group.find(":")
                    names = (group[:colon] if colon != -1 else group).split()
                groups.append((start, i + 1, opener, names))
                start = -1
    return groups




def remove_explicit_binders(binders: str, drop: set[str]) -> tuple[str, list[str]]:
    """Drop the named EXPLICIT `(…)` binders from a signature's binder string.
    A multi-name group `(a b : T)` loses just the named ones; a group emptied of
    its names is removed whole. Implicit/instance groups are never touched.
    Returns `(new_binders, dropped_names)`."""
    parts, dropped, last = [], [], 0
    for start, end, opener, names in _binder_groups(binders):
        parts.append(binders[last:start])
        last = end
        seg = binders[start:end]
        if opener == "(" and names:
            hit = [x for x in names if x in drop]
            if hit:
                dropped += hit
                kept = [x for x in names if x not in drop]
                if kept:
                    group = binders[start + 1:end - 1]
                    seg = "(" + " ".join(kept) + " " + group[group.find(":"):].strip() + ")"
                else:
                    seg = ""
        parts.append(seg)
    parts.append(binders[last:])
    return re.sub(r"[ \t]{2,}", " ", "".join(parts)), dropped




def unused_theorem_hypotheses(warnings, binders: str) -> list[str]:
    """Names the elaborator flagged `unused variable` that are EXPLICIT binders of
    the theorem — the strippable set (proof-internal unused vars are not statement
    surgery). `_`-prefixed names are deliberate; skipped."""
    flagged: list[str] = []
    for w in warnings or []:
        flagged += re.findall(r"[Uu]nused variable `([^`]+)`", str(w))
    explicit = set(explicit_arg_names(binders))
    seen: set[str] = set()
    out = []
    for x in flagged:
        if x in explicit and not x.startswith("_") and x not in seen:
            seen.add(x)
            out.append(x)
    return out




def _rebuild_snippet(snippet: str, candidate: str, thm_name: str) -> str | None:
    """Rebuild the re-export snippet against the stripped module theorem: same
    binders, application re-derived from them (emit's own formula). None if the
    snippet's shape is unexpected — the caller must then revert the whole strip,
    because a module/snippet signature mismatch would block open-pr regen. A
    snippet that applies a DIFFERENT theorem (the corollary shape re-exports the
    corollary while `thm_name` is the sorry-carrying core) is refused outright."""
    if f"MathFin.{thm_name}" not in snippet:
        return None
    try:
        cb, cs, _ce = _locate_named(candidate, thm_name)
        new_binders = candidate[cb:cs].strip()
        _sn, sb, ss, send = _locate(snippet)
        app = f"MathFin.{thm_name} {' '.join(explicit_arg_names(new_binders))}".rstrip()
        return (snippet[:sb] + f" {new_binders} " + snippet[ss:send + 2]
                + f"\n  {app}\n")
    except ValueError:
        return None




_MATHFIN_IMPORT_RE = re.compile(r"^public import (MathFin\.\S+)[ \t]*\n", re.MULTILINE)




def trim_unused_imports(candidate: str, *, check_fn) -> dict:
    """Drop `public import MathFin.X` lines the proved candidate does not need.
    Emit imports EVERY issue pointer, and 'an unused import is harmless' is false
    by the coherence lens: both production PRs carried unused pointer imports,
    one adding a spurious FixedIncome→Futures edge. Subtractive and fail-open:
    each removal is kept only if the file still elaborates clean without it.
    `public import Mathlib` is never touched. Returns `{candidate, removed}`."""
    removed: list[str] = []
    for m in list(_MATHFIN_IMPORT_RE.finditer(candidate)):
        line, mod = m.group(0), m.group(1)
        trial = candidate.replace(line, "", 1)
        r = check_fn(trial)
        if r and r.get("success") and not r.get("errors") and r.get("sorry_count", 0) == 0:
            candidate = trial
            removed.append(mod)
    return {"candidate": candidate, "removed": removed}



_OPEN_LINE_RE = re.compile(r"(?m)^open(?:[ \t]+scoped)?[ \t]+\S.*\n")


def trim_unused_opens(candidate: str, *, check_fn) -> dict:
    """Drop `open …` / `open scoped …` lines the proved candidate does not need.

    Emit deliberately opens the house preamble (`MeasureTheory ProbabilityTheory`,
    `scoped NNReal ENNReal`) because at DRAFT time an unused open is harmless while a
    missing one is a silent bare-name death. By the gate phase that trade is settled —
    the module has elaborated, so we can just ask. Every draft of #161/#162 shipped
    both lines on targets with no measure theory in sight, and two also carried
    `open scoped BigOperators`, a no-op on the current Mathlib.

    Subtractive and fail-open, exactly like `trim_unused_imports`: a removal is kept
    only if the file still elaborates clean without it. Returns `{candidate, removed}`."""
    removed: list[str] = []
    for m in list(_OPEN_LINE_RE.finditer(candidate)):
        line = m.group(0)
        if line not in candidate:            # consumed by an earlier accepted trial
            continue
        trial = candidate.replace(line, "", 1)
        r = check_fn(trial)
        if r and r.get("success") and not r.get("errors") and r.get("sorry_count", 0) == 0:
            candidate = trial
            removed.append(line.strip())
    return {"candidate": candidate, "removed": removed}


_GOLF_DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:noncomputable\s+)?(?:theorem|lemma|def|abbrev)\s",
    re.MULTILINE,
)




def _decl_signatures(text: str) -> list[str]:
    """Whitespace-normalized decl signatures (decl keyword up to the depth-0 `:=`),
    in order — the golf invariant: a polish may touch only what follows `:=`."""
    sigs = []
    n = len(text)
    for m in _GOLF_DECL_RE.finditer(text):
        depth, k = 0, m.end()
        while k < n - 1:
            c = text[k]
            if c in _OPEN:
                depth += 1
            elif c in _CLOSE:
                depth -= 1
            elif c == ":" and depth == 0 and text[k + 1] == "=":
                sigs.append(re.sub(r"\s+", " ", text[m.start():k]).strip())
                break
            k += 1
    return sigs




def golf_candidate(candidate: str, *, chat_fn, regate_fn, log=lambda m: None) -> dict:
    """One post-gate polish round: the prover golfs its own accepted proof toward
    the house register (the repo contract: a machine-found proof is refactored to
    the certificate that shows why before it merges). Accepted only if every decl
    signature is byte-equivalent (proof-only edits) AND the full gate passes again;
    any miss keeps the proved original (fail-open). Returns {candidate, golfed}."""
    try:
        content, _tk = chat_fn([{"role": "system", "content": GOLF_SYSTEM},
                                {"role": "user", "content": f"```lean\n{candidate}\n```"}])
    except Exception:  # noqa: BLE001 — polish is optional; never lose the proof
        return {"candidate": candidate, "golfed": False}
    golfed = extract_lean_code(content or "")
    if not golfed or golfed.strip() == candidate.strip() or "sorry" in golfed:
        return {"candidate": candidate, "golfed": False}
    if _decl_signatures(golfed) != _decl_signatures(candidate):
        log("golf: statement drift — rejected before re-gating")
        return {"candidate": candidate, "golfed": False}
    g = regate_fn(golfed)
    if not (isinstance(g, dict) and g.get("passed")):
        log(f"golf: re-gate failed ({(g or {}).get('reason')}); keeping the original")
        return {"candidate": candidate, "golfed": False}
    log("golf: accepted (proof-only edit, full gate green)")
    return {"candidate": golfed, "golfed": True}




def _protected_from_strip(binders: str, body: str, drop: list[str]) -> set[str]:
    """Flagged-unused binders the strengthen pass must NOT strip — pure-parse
    pre-checks that avoid a wasted re-gate round / a broken PR before the full
    re-gate backstop even runs (H8):
    - (a) sole-implicit-pin: the binder is the only use of an implicit `{B}` — its
      type mentions `B`, and `B` appears nowhere else in the binders, so dropping it
      would orphan the implicit;
    - (b) a `≠` (disequality) side-condition when the proof uses a context-pulling
      tactic (`grind`/`nlinarith`/…): the classic `A ≠ 0` field/division side-condition
      the linter reports `unused` although the tactic consumes it from context, not by
      name. (Order hypotheses `0 ≤ …` are left to the re-gate — the linter is reliable
      there, and over-protecting would keep genuinely-dead binders.)"""
    protected: set[str] = set()
    uses_ctx = bool(re.search(r"\b(?:grind|nlinarith|positivity|bound|gcongr|omega|polyrith)\b",
                              body))
    implicits: set[str] = set()
    for m in re.finditer(r"[{⦃]\s*([^:{}⦃⦄]+?)\s*:", binders):
        implicits.update(m.group(1).split())
    for name in drop:
        bm = re.search(r"\(\s*" + re.escape(name) + r"\b[^:()]*:\s*[^()]*\)", binders)
        if not bm:
            continue
        btext = bm.group(0)
        typ = btext.split(":", 1)[1] if ":" in btext else ""
        if uses_ctx and "≠" in typ:
            protected.add(name)
            continue
        for iv in implicits:
            iv_re = r"(?<![\w'])" + re.escape(iv) + r"(?![\w'])"
            # occurrences outside this binder; ≤1 means only the implicit's own
            # `{iv : …}` declaration remains → stripping orphans it.
            if re.search(iv_re, typ) and \
                    len(re.findall(iv_re, binders)) - len(re.findall(iv_re, btext)) <= 1:
                protected.add(name)
                break
    return protected




def strengthen_candidate(candidate: str, snippet: str | None, thm_name: str,
                         warnings, *, regate_fn, max_passes: int = 3,
                         log=lambda m: None) -> dict:
    """Drop theorem hypotheses the finished proof never used and re-gate. Keeps
    the stronger statement only if the FULL gate passes again; any failure —
    re-gate red, unlocatable decl, unrebuildable snippet — reverts to the
    original (fail-open: never lose a good proof to the optimizer). Returns
    `{candidate, entry_code, stripped}`; `entry_code` is the rebuilt re-export
    (None when nothing was stripped or no snippet was supplied)."""
    original = candidate
    stripped: list[str] = []
    for _ in range(max_passes):
        try:
            bstart, sep, _end = _locate_named(candidate, thm_name)
        except ValueError:
            break
        binders = candidate[bstart:sep]
        drop = unused_theorem_hypotheses(warnings, binders)
        prot = _protected_from_strip(binders, candidate[sep:], drop)  # H8 pure-parse guard
        drop = [d for d in drop if d not in prot]
        if not drop:
            break
        new_binders, dropped = remove_explicit_binders(binders, set(drop))
        if not dropped:
            break
        cand2 = candidate[:bstart] + new_binders + candidate[sep:]
        g2 = regate_fn(cand2)
        if not g2.get("passed"):
            log(f"strengthen: dropping {dropped} failed the re-gate "
                f"({g2.get('reason')}); keeping the proved original")
            break
        log(f"strengthen: dropped unused hypothesis(es) {dropped}")
        candidate = cand2
        warnings = g2.get("warnings") or []
        stripped += dropped
    if not stripped:
        return {"candidate": original, "entry_code": None, "stripped": []}
    entry_code = None
    if snippet is not None:
        entry_code = _rebuild_snippet(snippet, candidate, thm_name)
        if entry_code is None:
            log("strengthen: snippet rebuild failed; reverting to the original statement")
            return {"candidate": original, "entry_code": None, "stripped": []}
    return {"candidate": candidate, "entry_code": entry_code, "stripped": stripped}




# --- placement + mechanical emit ---------------------------------------------

# issue area label -> MathFin subdirectory. Areas without a directory yet (fx)
# map to a new one the umbrella import + lake build absorb; anything unmapped
# falls back to a CamelCase of the area.
_AREA_TO_SECTION = {
    "fixed-income": "FixedIncome", "actuarial": "Actuarial", "fx": "FX",
    "black-scholes": "BlackScholes", "futures": "Futures", "binomial": "Binomial",
    "portfolio": "Portfolio", "performance": "Performance", "risk": "RiskMeasures",
    "defi": "DeFi", "credit": "FixedIncome", "execution": "Portfolio",
}



_LICENSE = (
    "/-\n"
    "Copyright (c) 2026 Raphael Coelho. All rights reserved.\n"
    "Released under Apache 2.0 license as described in the file LICENSE.\n"
    "Authors: Raphael Coelho\n"
    "-/"
)


_BENCHMARK = "benchmarks/mathematical_finance.json"


_DOMAIN = "mathematical_finance"




def section_for_area(area: str) -> str:
    """Map an issue's `area:` label to a `MathFin/<Section>/` subdirectory."""
    if area in _AREA_TO_SECTION:
        return _AREA_TO_SECTION[area]
    return "".join(p.capitalize() for p in re.split(r"[-_ ]+", area or "") if p)




def normalize_deferred(val) -> list[str]:
    """The drafter's declared-deferred facts (json `deferred`) as a clean list of
    one-line phrases: the parts of a multi-fact issue this subset does NOT prove, to
    become follow-up issues. Accepts a list (json array) or a single string; drops
    blanks. `[]` (covers the whole issue) is the common case."""
    if val is None:
        return []
    items = val if isinstance(val, list) else [val]
    out = []
    for x in items:
        s = str(x).strip()
        if s:
            out.append(s)
    return out




# A5: a `/-- … -/` decl docstring immediately followed by an `omit …/set_option …
# in` modifier is a parse error (`unexpected token 'omit'`) — the modifier must sit
# ABOVE the docstring. This regex captures (docstring)(modifier-lines) to swap them.
_MODIFIER_AFTER_DOC_RE = re.compile(
    r"(/--.*?-/)\n((?:[ \t]*(?:omit|set_option)\b[^\n]*?\bin\b[ \t]*\n)+)", re.DOTALL)


# A7: a capital `Σ`/`Π` glued into an identifier collides with sigma/pi-type
# notation (a recurring drafter slip — girsanov era). Match one adjacent to an
# ASCII identifier char so the standalone `Σ x, …` type-former is NOT flagged.
_SIGMA_PI_IDENT_RE = re.compile(r"[A-Za-z0-9_'][ΣΠ]|[ΣΠ][A-Za-z0-9_']")




def _prelint_stub(stub: str) -> str:
    """Emit-time deterministic fixes on a drafted stub, before assembly:
    - A13: STRIP any model-emitted `import` line. Leanstral (RL-trained on complete
      files) prepends `import Mathlib`; but `emit_target_files` inserts the stub AFTER
      the module header's own imports, so a stub import lands mid-file and the module
      system rejects it (`invalid 'import' command, it must be used in the beginning of
      the file` — recurred live on #109/#60, 2026-07-19). The header already imports
      Mathlib + the pointer modules, so the stub never needs one. Deterministic beats the
      soft repair hint the model kept ignoring.
    - A5: move an `omit …/set_option … in` modifier ABOVE an immediately preceding
      decl docstring (else `unexpected token 'omit'; expected 'lemma'`).
    - A7: reject a capital `Σ`/`Π` inside an identifier (sigma/pi-type collision);
      raised so `formalize_with_repair`'s try/except surfaces it to the model."""
    stub = re.sub(r"(?m)^[ \t]*(?:public[ \t]+)?import[ \t]+\S.*\n?", "", stub)
    # A14: rewrite an autobound universe variable (`Type u`, `Sort u_1`, `Type v`) to the
    # Mathlib idiom `Type*`/`Sort*`. emit pins `autoImplicit false` for build-parity, so an
    # explicit `u` is unbound → "unknown universe level u" (recurred live on #109/#60). Only
    # u/v/w-prefixed vars (Lean's autobound naming) are touched; numeric levels are left alone.
    stub = re.sub(r"\b(Type|Sort)[ \t]+([uvw][A-Za-z0-9_']*)\b", r"\1*", stub)
    # A15: MathFin is a proof-only library, so every real-valued def is effectively
    # noncomputable (ℝ division and order are). The drafter routinely omits the
    # modifier and then burns every repair round on "consider marking it as
    # 'noncomputable'" (the #1 recurring defs-route error, seen on every #73 probe).
    # Prepend it deterministically: marking a def noncomputable never breaks a proof
    # (it only forbids code generation, which the library never does) and is lint-clean.
    # `abbrev`/`instance`/`structure` are left alone; an existing modifier is not doubled.
    stub = re.sub(r"(?m)^(private[ \t]+)?def ", r"\1noncomputable def ", stub)
    # A16: an attribute on an `example` is inert — examples are anonymous, so there is
    # nothing for `@[simp]` to tag. #165 shipped two of them. Drop the attribute rather
    # than the example: the example is a real sanity check, the attribute is noise.
    stub = re.sub(r"(?m)^[ \t]*@\[[^\]]*\][ \t]*\n([ \t]*example\b)", r"\1", stub)
    # A17: bind the type argument implicitly. `def gainToPain (S : Type*) (s : Finset S)`
    # (#165) forces `gainToPain S s r` at every call site, where Mathlib writes
    # `{ι : Type*}` and lets unification recover it from the Finset. Only rewritten when
    # the type variable is USED by a later binder, which is what makes it inferable.
    def _implicit_type(m: re.Match) -> str:
        var = m.group(1)
        return m.group(0) if not re.search(rf"[({{\[][^)}}\]]*\b{re.escape(var)}\b",
                                           m.string[m.end():]) else f"{{{var} : Type*}}"
    stub = re.sub(r"\((\w+) : Type\*\)", _implicit_type, stub)
    bad = _SIGMA_PI_IDENT_RE.search(stub)
    if bad:
        raise ValueError(
            f"identifier uses `{bad.group()}` — a capital Σ/Π collides with sigma/pi-type "
            "notation; rename it with an ASCII/lowercase identifier (e.g. `sigma`).")
    return _MODIFIER_AFTER_DOC_RE.sub(lambda m: m.group(2) + m.group(1) + "\n", stub)




def emit_target_files(issue: dict, stub: str, meta: dict) -> tuple[str, dict, dict]:
    """Assemble a queue target from a drafted stub — MECHANICAL, no model call.

    Returns `(stub_lean_text, entry_json, placement)`:
    - `stub_lean_text`: the full `cal-bk-<N>.lean` module (license, module header,
      `public import Mathlib`, placement comment headers `build_manifest` reads, the
      `meta.docstring` as a `/-! -/` doc, `@[expose] public section`, `namespace
      MathFin`, the drafted theorem with its `sorry`, `end MathFin`);
    - `entry_json`: the re-export benchmark entry (`import` the module + apply the
      lemma, carrying `metadata.provenance`);
    - `placement`: `{main_module, benchmark, benchmark_id, source_issue}`.
    """
    n = issue["number"]
    section = section_for_area(issue.get("area") or "")
    benchmark_id = meta["benchmark_id"]
    docstring = (meta.get("docstring") or "").strip()
    deferred = normalize_deferred(meta.get("deferred"))
    pointers = issue.get("pointers", [])
    new_defs = [str(d) for d in (meta.get("definitions") or [])]
    stub = _prelint_stub(stub)   # A5 modifier-order fix + A7 Σ/Π-identifier reject
    name, binders, concl = split_statement(stub)

    # main-module placement. When the issue's `location:` names a MathFin module, that
    # is the answer (backlog S) — #161/#162 both said `Performance/RatiosExtended.lean`,
    # beside the four ratios that already share an algebraic master, and the pipeline
    # made four new one-lemma modules instead. `append` tells
    # `assemble.apply_contribution` to SPLICE rather than write, which is what makes
    # honouring it safe: the old failure was a whole-file write deleting the existing
    # module's theorems (#162 -> AxiomAuditGen "unknown constant", PR blocked).
    #
    # Absent a location, a new-object contribution creates its OWN module. Trusting the
    # drafter's free-text module_name let it pick an EXISTING module the target also
    # imports as a pointer; heal deterministically by naming the module for the object
    # it introduces (def `upCapture` -> module `UpCapture`), and reject loudly if it
    # still collides (a module cannot import itself).
    append = False
    location = extract_location(issue.get("body") or "")
    module_name = meta["module_name"]
    if location:
        main_module, append = location, True
        module_name = os.path.basename(location)[:-len(".lean")]
    else:
        main_module = f"MathFin/{section}/{module_name}.lean"
        if main_module in pointers:
            primary = new_defs[0].split(".")[-1].strip() if new_defs else ""
            if primary:
                module_name = primary[:1].upper() + primary[1:]
                main_module = f"MathFin/{section}/{module_name}.lean"
            if main_module in pointers:
                raise ValueError(
                    f"main-module {main_module} collides with a pointer import; a "
                    "new-object contribution must create a fresh module named for the "
                    "object it introduces (set meta.definitions / meta.module_name).")
    header_lines = [
        f"-- pointers: {', '.join(pointers)}",
        f"-- main-module: {main_module}",
        f"-- benchmark: {_BENCHMARK}",
        f"-- benchmark-id: {benchmark_id}",
        f"-- source-issue: {n}",
    ]
    if append:
        # backlog S: the issue's `location:` named an existing module, so open-pr must
        # SPLICE the proven declarations in rather than write the file (which would
        # delete what is already there). Rides the header like every other placement
        # fact, so build_manifest carries it to the queue target.
        header_lines.append("-- append: true")
    if deferred:
        # this proof is a faithful SUBSET of the issue; the deferred facts ride the
        # header (build_manifest → manifest → open-pr surfaces them as follow-ups).
        header_lines.append(f"-- deferred: {'; '.join(deferred)}")
    if new_defs:
        # this module INTRODUCES definitions (the defs route) — open-pr labels the
        # PR `new-defs`, the architecture-heavy review class.
        header_lines.append(f"-- new-defs: {', '.join(new_defs)}")
    headers = "\n".join(header_lines)
    # coherence-first: import the pointer modules so the drafted statement can
    # consume existing MathFin defs (a path 'MathFin/FixedIncome/ZCB.lean' becomes
    # 'public import MathFin.FixedIncome.ZCB'). An unused pointer import is NOT
    # harmless (it adds a spurious cross-module edge) — trim_unused_imports drops any
    # the proved candidate does not need, post-proof.
    imports = "\n".join(
        ["public import Mathlib"]
        + [f"public import {p[:-5].replace('/', '.')}"
           for p in pointers if p.endswith(".lean")]
    )
    lean_text = (
        f"{_LICENSE}\n"
        "module\n\n"
        f"{imports}\n\n"
        f"{headers}\n\n"
        f"/-!\n{docstring}\n-/\n\n"
        # lake-parity: the lakefile sets autoImplicit false, but the DAEMON that
        # gates drafts elaborates with Lean's default (true) — a drafted
        # `{Ω : Type u}` auto-binds `u`, passes every gate, then fails the
        # open-pr regen build with `unknown universe level` (run-4 PR blocker).
        # Pinning the option in the stub makes draft-time elaboration enforce
        # exactly what the build enforces, so the compile-repair loop fixes it.
        "set_option autoImplicit false\n\n"
        "@[expose] public section\n\n"
        "namespace MathFin\n\n"
        # the house preamble (Girsanov.lean:50-51 — 155/262 MathFin modules do this):
        # the drafter emits bare `IsProbabilityMeasure`/`IntegrableOn`/`Measure`
        # exactly as a MathFin author would, so the module must open the namespaces
        # that carry them. Without this, every measure-theory target died `unknown
        # identifier` even after a FAITHFUL draft (run 29667784310, #60 + #109). An
        # unused open is harmless; a missing one is a silent bare-name death.
        "open MeasureTheory ProbabilityTheory\n"
        "open scoped NNReal ENNReal\n\n"
        f"{stub.strip()}\n\n"
        "end MathFin\n"
    )

    mf_name = benchmark_id.replace("-", "_")
    app = f"MathFin.{name} {' '.join(explicit_arg_names(binders))}".rstrip()
    reexport = (
        f"import MathFin.{section}.{module_name}\n\n"
        "open MathFin\n\n"
        f"/-- {docstring} -/\n"
        f"theorem {mf_name} {binders.strip()} :{concl.rstrip()} :=\n"
        f"  {app}\n"
    )
    scope = (
        f"Full formal proof in {main_module} (autoformalized statement, "
        "leanstral proof). Re-export from MathFin. Axioms-clean."
    )
    # drafter-agnostic provenance: the statement drafter is not named (it's Claude, and
    # Claude is never attributed per the repo's attribution rule); the PROVER stays
    # credited (leanstral).
    provenance = {
        "statement_source": "autoform",
        "statement_model": "autoform",
        "source": "leanstral-autoform",
        "model": "labs-leanstral-1-5",
        "issue": n,
    }
    if deferred:
        # honest disclosure in the entry itself: `full` proof of a SUBSET of the issue.
        scope += (f" Faithful SUBSET of issue #{n}; deferred to follow-up issues: "
                  f"{'; '.join(deferred)}.")
        provenance["deferred"] = deferred
    if new_defs:
        scope += f" Introduces definitions: {', '.join(new_defs)} (new-defs review class)."
        provenance["new_defs"] = new_defs
    entry = {
        "id": benchmark_id,
        "name": issue.get("title", benchmark_id),
        "description": docstring,
        "domain": _DOMAIN,
        "code": {"lean": reexport},
        "metadata": {
            "chapter": 0,
            "reference": issue.get("title", ""),
            "difficulty": issue.get("difficulty", "medium"),
            "formalization_status": "full",
            "formalization_scope": scope,
            "provenance": provenance,
        },
    }
    placement = {
        "main_module": main_module,
        "benchmark": _BENCHMARK,
        "benchmark_id": benchmark_id,
        "source_issue": n,
        "deferred": deferred,
        "append": append,
    }
    return lean_text, entry, placement



if __name__ == "__main__":
    sys.exit(main())
