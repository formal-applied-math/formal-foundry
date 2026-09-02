"""House context / values / idioms / pins for the formalization (prover) agents.

This is the reusable "setup" layer that equips a Leanstral (or any) prover agent
with the same standards a library author works to: the toolchain/dependency pins
it must target, the values contract its output is held to, and the distilled
house Lean idioms that make a proof idiomatic instead of a kernel-green blob.

`build_system_prompt(main_repo, pack)` returns the system message injected on
every attempt. `extract_signatures(main_repo, modules)` builds the per-target
context pack — the existing declarations the agent should CONSUME rather than
reprove (coherence-first, anti-wrapper). Both read the live repo so they never go
stale.

The house doctrine itself is DATA: it lives in the domain pack
(`domains/<name>/house.md`), because it is the one part of this module that is
wholly about one library. What stays here is the assembly — the pin block, the
live-patterns injection, and the context pack — all of which are field-neutral.

Design of record: docs/PROVER_SETUP.md, and
the flagship's docs/plans/2026-08-09-program-execution/02-foundry-domain-packs.md.
Stdlib only.
"""

from __future__ import annotations

import json
import os
import re

from domain_pack import DomainPack
from scout_index import ScoutIndex, default_index_dir

# --- Pins ---------------------------------------------------------------------

def read_pins(main_repo: str, pack: DomainPack) -> dict:
    """Live toolchain + per-dependency pins from the target repo, keyed by the
    pack's `manifest` names plus `toolchain`. A dependency the manifest does not
    carry reads '?' rather than raising: a stale pin degrades prompt quality, it
    is not a reason to fail a tick."""
    toolchain = open(os.path.join(main_repo, "lean-toolchain")).read().strip()
    wanted = {d.manifest for d in pack.deps}
    revs: dict[str, str] = {}
    try:
        man = json.load(open(os.path.join(main_repo, "lake-manifest.json")))
        for p in man.get("packages", []):
            n = (p.get("name") or "").lower()
            if n in wanted:
                revs[n] = (p.get("rev") or "")[:12]
    except Exception:
        pass
    pins = {"toolchain": toolchain}
    pins.update({m: revs.get(m, "?") for m in sorted(wanted)})
    return pins


def read_patterns(main_repo: str) -> str:
    """The LIVE, first-class house patterns doc from the main repo
    (`docs/patterns.md`), read fresh on every prompt so the foundry always applies
    its CURRENT form (a first-class requirement, not a snapshot). '' if absent — the
    doctrine summary still carries the essentials."""
    try:
        with open(os.path.join(main_repo, "docs", "patterns.md"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


# --- The house doctrine (values + idioms + strategy) ---------------------------
# The doctrine prose itself is the DOMAIN's, not the foundry's: it names the
# library, its dependencies, and the idioms its authors hold proofs to. It lives
# in `domains/<name>/house.md` and reaches the prompt through `pack.house_doctrine`.


def build_system_prompt(main_repo: str, pack: DomainPack) -> str:
    pins = read_pins(main_repo, pack)
    pin_block = (
        "── PINS (target THIS API surface exactly) ──\n"
        + pack.pin_block(pins["toolchain"], pins)
        + "Lemma names / signatures must match these revisions — do not assume a "
        "newer or older Mathlib API. If unsure a lemma exists at this pin, prefer "
        "a first-principles step over a guessed name.\n"
    )
    # The LIVE docs/patterns.md is a FIRST-CLASS requirement: inject its current
    # form in full so the foundry always proves to the library's latest patterns.
    patterns = read_patterns(main_repo)
    patterns_block = (
        "\n── docs/patterns.md — FIRST-CLASS, LIVE, AUTHORITATIVE ──\n"
        "The following is the CURRENT contents of the library's patterns doc. It is a "
        "first-class requirement: study and apply it, and prefer it over the quick "
        "summary above wherever they differ.\n\n"
        f"{patterns}\n"
    ) if patterns.strip() else ""
    return pack.house_doctrine + "\n" + patterns_block + pin_block


# --- Drafter (statement) authority --------------------------------------------
# The DRAFTER (intent + formalize stages) writes STATEMENTS, not proofs. Until
# now it got none of the prover's context — no pins, no house statement-design
# rules — which is why it hallucinated constants (the pack's worked-example
# constant onto an unrelated problem) and stated context-free theorems the
# depth-gate then killed. It gets the pins + the statement-design section of
# patterns.md, and pointedly NOT the prover's tactic ladder (which would only
# tempt it to write proofs).
#
# The fallback prose — used when the target's patterns.md carries no 'Statement
# design' section — is the domain's, and lives in
# `domains/<name>/statement-design.md` (`pack.statement_design_fallback`).


def _slice_patterns_section(patterns: str, header_substr: str) -> str:
    """The `## …<header_substr>…` section of patterns.md — the header line through
    the line before the next `## ` (or EOF). '' if no such header is present."""
    lines = patterns.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if ln.startswith("## ") and header_substr in ln), None)
    if start is None:
        return ""
    end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")),
               len(lines))
    return "\n".join(lines[start:end]).strip()


def build_drafter_prompt(main_repo: str, pack: DomainPack) -> str:
    """System-prompt PREAMBLE for the DRAFTER (intent + formalize stages): the pins
    it must target + the live 'Statement design' section of patterns.md (fail-open to
    the pack's curated fallback). Deliberately EXCLUDES the prover's tactic ladder —
    the drafter states, it does not prove. Read live so patterns.md edits reach it."""
    pins = read_pins(main_repo, pack)
    pin_block = (
        "── PINS (target THIS API surface exactly) ──\n"
        + pack.pin_block(pins["toolchain"], pins)
        + "Object and lemma names must exist at these revisions; if unsure a name exists, "
        "do not guess it — take the definitions route or omit it.\n"
    )
    section = _slice_patterns_section(read_patterns(main_repo), "Statement design")
    design_block = section if section else pack.statement_design_fallback
    return pin_block + "\n" + design_block + "\n"


# --- Per-target context pack (consume-don't-reprove) --------------------------

_DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:noncomputable\s+)?(?:public\s+)?"
    r"(theorem|lemma|def|abbrev|structure|instance)\s+([A-Za-z0-9_'.]+)",
    re.MULTILINE,
)


def _first_line(s: str | None) -> str:
    if not s:
        return ""
    return s.strip().splitlines()[0].strip()


def _sig_line(name: str, typ: str, doc: str | None) -> str:
    short = name.rsplit(".", 1)[-1]
    return f"{short} : {typ}" + (f"    -- {_first_line(doc)}" if doc else "")


def _index_pack(idx: ScoutIndex, modules: list[str], max_per_module: int,
                exemplar_limit: int, closure_depth: int = 2,
                closure_limit: int = 24) -> str:
    """Context pack from the lean_scout index: REAL elaborated signatures +
    docstrings, the cross-file dependency-closure premises of those decls, plus
    house-style (goal → tactic) exemplars. '' if the index covers none of the
    requested modules (caller then falls back to regex)."""
    by_mod = idx.signatures(modules, max_per_module=max_per_module)
    if not by_mod:
        return ""
    blocks: list[str] = []
    seed_names: list[str] = []
    for mod in modules:
        recs = by_mod.get(_module_key(idx, mod))
        if not recs:
            continue
        lines = []
        for name, typ, doc in recs:
            seed_names.append(name)
            lines.append(_sig_line(name, typ, doc))
        blocks.append(f"• {mod}:\n    " + "\n    ".join(lines))
    if not blocks:
        return ""
    pack = ("── EXISTING DECLARATIONS TO BUILD ON (real signatures; consume, do not reprove) ──\n"
            + "\n".join(blocks) + "\n")
    # cross-file premises: the dependency closure of the pointer modules' decls,
    # so the agent sees the lemmas those decls transitively rest on, not just the
    # pointer modules themselves (miniCTX: cross-file premises are a first-order
    # lever). Skip closure constants defined IN the pointer modules (already shown)
    # and own-library/Mathlib-core noise without a recorded type.
    seed_set = set(seed_names)
    clines: list[str] = []
    for cname in idx.dependency_closure(seed_names, depth=closure_depth):
        if cname in seed_set:
            continue
        sig = idx.signature_of(cname)
        if sig and sig[1]:
            clines.append(_sig_line(cname, sig[1], sig[2]))
        if len(clines) >= closure_limit:
            break
    if clines:
        pack += ("── DEPENDENCY-CLOSURE SIGNATURES (cross-file premises these decls rest on) ──\n    "
                 + "\n    ".join(clines) + "\n")
    exemplars = idx.tactic_exemplars(modules, limit=exemplar_limit)
    if exemplars:
        ex_lines = [f"    {goal}\n        ⟶  {tac}" for goal, tac in exemplars]
        pack += ("── HOUSE-STYLE TACTIC EXEMPLARS (how this library discharges goals) ──\n"
                 + "\n".join(ex_lines) + "\n")
    return pack


def _module_key(idx: ScoutIndex, mod: str) -> str:
    from scout_index import path_to_module
    return path_to_module(mod)


def _regex_pack(main_repo: str, modules: list[str], max_per_module: int) -> str:
    """Fallback context pack: declaration names scraped from source by regex."""
    out: list[str] = []
    for mod in modules:
        path = os.path.join(main_repo, mod)
        if not os.path.exists(path):
            continue
        try:
            src = open(path, encoding="utf-8").read()
        except Exception:
            continue
        names = [f"{kind} {name}" for kind, name in _DECL_RE.findall(src)][:max_per_module]
        if names:
            out.append(f"• {mod}:\n    " + "\n    ".join(names))
    if not out:
        return ""
    return ("── EXISTING DECLARATIONS TO BUILD ON (consume these; do not reprove) ──\n"
            + "\n".join(out) + "\n")


def extract_signatures(main_repo: str, modules: list[str], max_per_module: int = 40,
                       index_dir: str | None = None, exemplar_limit: int = 6,
                       closure_depth: int = 2) -> str:
    """Per-target context pack so the agent builds on existing results instead of
    reproving them. `modules` are repo-relative paths (e.g.
    '<LakeRoot>/<Section>/<Module>.lean').

    Prefers the lean_scout index (real elaborated signatures + docstrings +
    house-style tactic exemplars) when it covers the requested modules; otherwise
    falls back to the regex name scrape, so the foundry works with or without an
    index built."""
    idx = ScoutIndex(index_dir if index_dir is not None else default_index_dir())
    if idx.available:
        pack = _index_pack(idx, modules, max_per_module, exemplar_limit,
                           closure_depth=closure_depth)
        if pack:
            return pack
    return _regex_pack(main_repo, modules, max_per_module)
