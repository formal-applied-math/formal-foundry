"""Turn a proven candidate into edits on the target library's checkout: the real
proof in a library module + a re-export benchmark entry. Pure file ops (no git,
no network) so it is testable on a temp tree. `scripts/open-pr.sh` owns
branch/commit/PR.

Benchmark JSON is appended with a minimal-diff round-trip: the corpus is
indent=2 / ensure_ascii=False, so re-dumping keeps existing entries byte-stable;
we only add the trailing comma + the new object and preserve the file's own
trailing-newline convention (some files have it, some do not).
"""

from __future__ import annotations

import json
import os
import re


def _module_name(main_module: str) -> str:
    """'<LakeRoot>/FX/Siegel.lean' -> '<Namespace>.FX.Siegel'."""
    p = main_module[:-5] if main_module.endswith(".lean") else main_module
    return p.replace("/", ".")


def ensure_umbrella_import(main_repo: str, main_module: str,
                           umbrella: str) -> list[str]:
    """Append `import <Module>` to the `<Namespace>.lean` umbrella so `lake build`
    puts the new module in the build graph (a benchmark snippet importing an
    unbuilt module gets a silently-empty environment — the umbrella header even
    warns about this). No-op if already present. Returns [umbrella] if it
    changed, else []."""
    mod_name = _module_name(main_module)
    umbrella_abs = os.path.join(main_repo, umbrella)
    text = open(umbrella_abs, encoding="utf-8").read()
    if re.search(rf"^import {re.escape(mod_name)}\s*$", text, re.MULTILINE):
        return []
    sep = "" if text.endswith("\n") or not text else "\n"
    with open(umbrella_abs, "w", encoding="utf-8") as f:
        f.write(text + sep + f"import {mod_name}\n")
    return [umbrella]


# Drafters that have left the pipeline. A queue entry authored while one was live
# carries its name baked in, because provenance used to be stamped at ENQUEUE rather
# than by the stage that ran (backlog U). Magistral was removed 2026-07-29; both
# `cal-bk-161` and `cal-bk-162` were still queued with `magistral-autoform` after it,
# so a re-pick would have emitted that claim for a Claude-drafted artifact.
_RETIRED_DRAFTERS = ("magistral",)

# What a drafter-agnostic entry says instead. The prover stays credited (leanstral);
# the drafter is not named, per the repo's standing attribution rule.
_ANON_DRAFTER = "autoform"


def sanitize_provenance(entry: dict) -> tuple[dict, list[str]]:
    """Strip retired drafter names from a benchmark entry's provenance.

    The last gate before a claim about *how this artifact was produced* lands in the
    public corpus. A stale queue entry naming a drafter that is no longer in the
    pipeline is a falsified record, and it is the kind that survives review because it
    looks like ordinary metadata. Only the DRAFTER fields are touched — `source` and
    `model` name the prover, which is unchanged and correctly credited.

    Returns `(entry, changed_keys)`; the entry is copied, never mutated in place."""
    out = json.loads(json.dumps(entry))
    meta = out.get("metadata")
    if not isinstance(meta, dict):
        return out, []
    changed = []
    prov = meta.get("provenance")
    if isinstance(prov, dict):
        for key in ("statement_source", "statement_model"):
            val = str(prov.get(key) or "")
            if any(d in val.lower() for d in _RETIRED_DRAFTERS):
                prov[key] = _ANON_DRAFTER
                changed.append(key)
    # the same claim is also made in prose — "(magistral-drafted statement, leanstral
    # proof)" — and that string is what a reader of docs/coverage.md actually sees
    scope = meta.get("formalization_scope")
    if isinstance(scope, str):
        new = re.sub(r"(?i)\b(" + "|".join(_RETIRED_DRAFTERS) + r")-drafted\b",
                     "autoformalized", scope)
        if new != scope:
            meta["formalization_scope"] = new
            changed.append("formalization_scope")
    return out, changed


def append_entry(benchmark_text: str, entry: dict) -> str:
    """Append `entry` to a benchmark file's theorem list, preserving the file's
    indent=2 / ensure_ascii=False formatting and its trailing-newline choice."""
    had_newline = benchmark_text.endswith("\n")
    data = json.loads(benchmark_text)
    if isinstance(data, dict) and "theorems" in data:
        data["theorems"].append(entry)
    elif isinstance(data, list):
        data.append(entry)
    else:  # dict without a theorems list — treat the doc itself as the container
        raise ValueError("benchmark file has no 'theorems' list and is not a bare list")
    out = json.dumps(data, indent=2, ensure_ascii=False)
    return out + "\n" if had_newline else out


# The splice's anchors are namespace-keyed, so they are DERIVED from the pack
# rather than stored — see `probe/domain_pack.py`. A module that does not open and
# close the pack's namespace is not a module we will splice into.
def _namespace_res(pack) -> tuple[re.Pattern[str], re.Pattern[str]]:
    ns = re.escape(pack.namespace)
    return (re.compile(rf"(?m)^namespace\s+{ns}\s*$"),
            re.compile(rf"(?m)^end\s+{ns}\s*$"))
_IMPORT_RE = re.compile(r"(?m)^(?:public\s+)?import\s+\S+\s*$")


def splice_into_module(pack, existing: str, candidate: str) -> str | None:
    """Merge a candidate module's declarations into an EXISTING module.

    Backlog S: both #161 and #162 named `Performance/RatiosExtended.lean` as the
    home for their ratio — beside the four ratios that already share an algebraic
    master — and the pipeline created a new one-lemma module instead, because the
    applier could only write whole files and a plain write would have deleted the
    existing module's theorems. Splicing is what makes honouring the issue safe.

    Keeps the existing file's licence header, module docstring, options and
    namespace; adds the candidate's imports that are not already present (in the
    header, above the docstring, where Lean requires them) and its declarations
    just before the namespace's `end`. Returns None when either side is not a
    recognisable `namespace … end` module — the caller must then refuse rather
    than guess."""
    ns_re, end_re = _namespace_res(pack)
    for text in (existing, candidate):
        if not (ns_re.search(text) and end_re.search(text)):
            return None
    cand_ns = ns_re.search(candidate)
    cand_end = list(end_re.finditer(candidate))[-1]
    body = candidate[cand_ns.end():cand_end.start()].strip("\n")
    if not body.strip():
        return None

    have = set(_IMPORT_RE.findall(existing))
    missing = [i for i in _IMPORT_RE.findall(candidate) if i not in have]
    out = existing
    if missing:
        last = list(_IMPORT_RE.finditer(out))
        if not last:
            return None
        at = last[-1].end()
        out = out[:at] + "\n" + "\n".join(m.rstrip() for m in missing) + out[at:]

    end = list(end_re.finditer(out))[-1]
    return out[:end.start()].rstrip("\n") + "\n\n" + body + "\n\n" + out[end.start():]


def apply_contribution(pack, candidate_code: str, target: dict, benchmark_entry: dict,
                       main_repo: str) -> list[str]:
    """Write the proven candidate to `target['main_module']` and append
    `benchmark_entry` to `target['benchmark']`, both under `main_repo`.
    Returns the repo-relative paths written (for logging / `git add`).

    Creates the module by default. When `target['append']` is set — the issue named
    an existing module in its `location:` line — the candidate's declarations are
    SPLICED in instead (`splice_into_module`), which is the only safe way to honour
    that placement."""
    written: list[str] = []

    mod_rel = target["main_module"]
    mod_abs = os.path.join(main_repo, mod_rel)
    if os.path.exists(mod_abs):
        if not target.get("append"):
            # The candidate is a whole standalone module (license + `module` + namespace),
            # so an open-in-"w" would DELETE the existing module's theorems rather than add
            # to it. #162 hit exactly this: the drafter set main_module to an existing
            # module it also imported; the clobber then surfaced as a confusing downstream
            # AxiomAuditGen "unknown constant". The emit-time placement guard heals that
            # case; this backstop guarantees no UNDECLARED route can silently corrupt an
            # existing module — it aborts the PR loud-and-early instead.
            raise FileExistsError(
                f"main_module {mod_rel} already exists — a new-object contribution must "
                "create a fresh module, not overwrite an existing one. Set "
                "target['append'] (from the issue's `location:`) to splice instead.")
        merged = splice_into_module(pack, open(mod_abs, encoding="utf-8").read(),
                                    candidate_code)
        if merged is None:
            raise ValueError(
                f"cannot splice into {mod_rel} — the existing file or the candidate is "
                "not a recognisable `namespace … end` module. Refusing "
                "rather than overwriting.")
        candidate_code = merged
    os.makedirs(os.path.dirname(mod_abs), exist_ok=True)
    with open(mod_abs, "w", encoding="utf-8") as f:
        f.write(candidate_code if candidate_code.endswith("\n") else candidate_code + "\n")
    written.append(mod_rel)

    bench_rel = target["benchmark"]
    bench_abs = os.path.join(main_repo, bench_rel)
    text = open(bench_abs, encoding="utf-8").read()
    entry, scrubbed = sanitize_provenance(benchmark_entry)
    if scrubbed:
        print(f"[assemble] provenance: dropped retired drafter name from {scrubbed} "
              "(the queue entry predates the current pipeline)")
    with open(bench_abs, "w", encoding="utf-8") as f:
        f.write(append_entry(text, entry))
    written.append(bench_rel)
    return written
