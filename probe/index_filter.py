"""Slice a raw lean_scout extraction down to the neighborhoods the library lives in.

`scripts/build-index.sh` extracts the FULL transitive closure — ~771k records /
~850 MB, overwhelmingly Mathlib and core internals. Until 2026-08-14 step 2 of
that script kept only own-namespace decls (~2.8k records, a 275x shrink) so the adapter
would load in ~0.03s. That was a load-time decision, but it had a consequence
nobody costed: the embedding retrieval in `embed.py` runs over `types.jsonl`, so
**the drafter's semantic retrieval had never seen a single Mathlib lemma**. Its
only Mathlib channel was `af_drafting.loogle_candidates` — syntactic, and
pointed at a PUBLIC instance tracking a newer Mathlib than the pin, which its
own docstring marks UNVERIFIED. A drafter held to a coherence-first,
anti-wrapper contract could not actually look up the lemma it was supposed to
consume.

Keeping everything is not the fix either: `embed.top_k` is a pure-Python O(n·d)
cosine scan, so the corpus size is a latency budget, not just a disk number.

So we keep a principled middle: the pack's OWN namespaces in full,
plus every ALLOWED external module that an own declaration actually reaches
through `const_dep`. That is "the neighborhoods we already live in" — if a
proof consumes `MeasureTheory.Integrable.add`, all of that lemma's
module becomes visible, so the drafter can find the sibling it should consume
instead of reproving it. It is also the miniCTX finding applied to retrieval:
whole-file context beats isolated signatures.

`tactics.jsonl` stays OWN-only on purpose. It feeds `tactic_exemplars`, whose
job is to show how *this library* discharges a goal; Mathlib's own tactic usage
is not house style, so admitting it would be bloat that actively misleads.

Streaming + stdlib only: the raw files do not fit comfortably in memory, so
every pass reads line-by-line and retains only small name/module sets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Which namespaces count as OURS is the DOMAIN's call — `pack.own_namespaces` — so
# `own` is a REQUIRED argument here with no default. An empty default would slice the
# corpus down to nothing and look like a working run; a TypeError at the call site is
# the better failure. Everything reachable from the own namespaces is "external".
# The only external namespace worth retrieving from. An ALLOW list, not a deny
# list, and deliberately so: a proof reaches `Eq.mpr`, `Nat.succ` and
# friends, and admitting the modules that host them (`Init.Prelude`, …) would
# drag in thousands of records no drafter can usefully cite.
DEFAULT_ALLOW = ("Mathlib",)


def module_matches(module: str | None, prefixes: tuple[str, ...]) -> bool:
    """True iff `module` is one of `prefixes` or a submodule of one.

    Prefix-with-dot, never bare `startswith`: `Foobar.Baz` is not a `Foo`
    module, and `Mathlibrary.X` is not a Mathlib one."""
    if not module:
        return False
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def _stream(path: str):
    """Yield parsed records from a JSONL file; silently empty if absent. A single
    unparseable line is skipped rather than killing an hour-long extraction."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def reached_constants(const_dep_path: str, own: tuple[str, ...]) -> set[str]:
    """Every constant named as a dependency BY an own-module declaration.

    This is the frontier where our library touches the rest of the world."""
    reached: set[str] = set()
    for rec in _stream(const_dep_path):
        if module_matches(rec.get("module"), own):
            reached.update(rec.get("deps") or [])
    return reached


def neighborhood_modules(types_path: str, reached: set[str], *,
                         own: tuple[str, ...],
                         allow: tuple[str, ...] = DEFAULT_ALLOW) -> set[str]:
    """The allowed external modules that HOST a reached constant.

    Own modules are excluded — they are kept by prefix at write time, so putting
    them here too would just duplicate the test."""
    mods: set[str] = set()
    for rec in _stream(types_path):
        if rec.get("name") in reached:
            mod = rec.get("module")
            if module_matches(mod, allow) and not module_matches(mod, own):
                mods.add(mod)
    return mods


def _keep(rec: dict, keep_modules: set[str], own: tuple[str, ...]) -> bool:
    mod = rec.get("module")
    return module_matches(mod, own) or mod in keep_modules


def filter_file(path: str, keep_modules: set[str], *,
                own: tuple[str, ...]) -> tuple[int, int]:
    """Rewrite `path` in place keeping own-module + `keep_modules` records.

    Returns `(kept, total)`. Writes to a sibling temp file and renames, so an
    interrupted run leaves the original intact rather than a truncated index.
    A missing file is `(0, 0)` — `tactics.jsonl` is opt-in (SCOUT_TACTICS=1)."""
    if not os.path.isfile(path):
        return (0, 0)
    tmp = path + ".slice"
    kept = total = 0
    with open(tmp, "w", encoding="utf-8") as out:
        for rec in _stream(path):
            total += 1
            if _keep(rec, keep_modules, own):
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                kept += 1
    os.replace(tmp, path)
    return (kept, total)


def slice_index(index_dir: str, *, own: tuple[str, ...],
                allow: tuple[str, ...] = DEFAULT_ALLOW) -> dict:
    """Slice types/const_dep to own + reached-neighborhood, tactics to own only.

    Idempotent: a second run recomputes the same frontier from the already-sliced
    files and keeps everything, so a re-run after a partial failure is safe."""
    types_path = os.path.join(index_dir, "types.jsonl")
    const_dep_path = os.path.join(index_dir, "const_dep.jsonl")
    tactics_path = os.path.join(index_dir, "tactics.jsonl")

    reached = reached_constants(const_dep_path, own)
    mods = neighborhood_modules(types_path, reached, own=own, allow=allow)

    t_kept, t_total = filter_file(types_path, mods, own=own)
    c_kept, c_total = filter_file(const_dep_path, mods, own=own)
    # Own-only: house-style exemplars, see the module docstring.
    x_kept, x_total = filter_file(tactics_path, set(), own=own)

    return {
        "reached_constants": len(reached),
        "neighborhood_modules": len(mods),
        "types": {"kept": t_kept, "total": t_total},
        "const_dep": {"kept": c_kept, "total": c_total},
        "tactics": {"kept": x_kept, "total": x_total},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="index_filter",
        description="Slice a raw lean_scout index to the library + its reached "
                    "Mathlib neighborhoods.")
    ap.add_argument("index_dir")
    ap.add_argument("--domain", default=None,
                    help="domain pack whose `own_namespaces` to keep in full "
                         "(default: the pack named in pipeline.toml)")
    ap.add_argument("--own", action="append", default=None,
                    help="own module prefix, repeatable; overrides the pack")
    ap.add_argument("--allow", action="append", default=None,
                    help=f"external prefix to retrieve from, repeatable (default: {', '.join(DEFAULT_ALLOW)})")
    args = ap.parse_args(argv)

    import domain_pack
    own = (tuple(args.own) if args.own
           else domain_pack.load(args.domain or domain_pack.DEFAULT_NAME).own_namespaces)
    stats = slice_index(args.index_dir,
                        own=own,
                        allow=tuple(args.allow) if args.allow else DEFAULT_ALLOW)

    def line(label, d):
        pct = (100.0 * d["kept"] / d["total"]) if d["total"] else 0.0
        print(f"  {label:<10} {d['kept']:>8} / {d['total']:<8} ({pct:5.2f}%)", file=sys.stderr)

    print(f"[index_filter] frontier: {stats['reached_constants']} constants reached "
          f"in {stats['neighborhood_modules']} external modules", file=sys.stderr)
    for k in ("types", "const_dep", "tactics"):
        line(k, stats[k])
    return 0


if __name__ == "__main__":
    sys.exit(main())
