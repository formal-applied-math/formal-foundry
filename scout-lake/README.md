# scout-lake — the lean_scout index project

A throwaway Lake project whose only job is to run
[`lean_scout`](https://github.com/mathlib-initiative/lean_scout) over the
`MathFin` library and emit the JSONL index the foundry's context packs consume
(`house_context` → `scout_index.ScoutIndex`).

It is deliberately **separate** from the main repo's `lakefile.lean`: that
manifest is the verification anchor + the ledger input-hash, so we never add a
dev-only extractor dependency to it. Here MathFin is a local-path require, so
its pinned Mathlib + BrownianMotion win, and lean_scout is pinned to an exact
rev (`5b7cdb6…`), never `@main`.

## Build the index

The daemon must be **down** (one Lean process on this box) — `build-index.sh`
guards this. From the foundry root:

```bash
scripts/build-index.sh          # → index/{types,tactics,const_dep}.jsonl + index/PIN
```

## Toolchain-compat risk (the one thing that can go wrong)

lean_scout `@5b7cdb6` may have been built against a different Mathlib than
MathFin's pin (`fabf563a`). Because MathFin is required last, its pins win — if
lean_scout's own code depends on Mathlib API that moved, `lake build` here will
error. Fallbacks, in order of preference:

1. Bump the lean_scout `rev` in `lakefile.toml` to a commit that targets Lean
   `v4.31.0` / Mathlib `fabf563a` (check its `lean-toolchain`).
2. If no compatible rev exists, run lean_scout in *its own* project (its own
   Mathlib) and point `--imports MathFin` at MathFin oleans built against the
   same Mathlib — only viable if the two Mathlibs match.
3. Worst case, keep the regex `extract_signatures` fallback (the foundry still
   works without the index; it just has coarser context packs).

The index is rebuilt once per pin; `index/PIN` records the toolchain + lean_scout
rev it was built under so staleness is detectable.
