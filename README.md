# mathfin-foundry

Private operational repo for the MathFin autoformalization operation.
Design of record: formal-mathfin `docs/superpowers/specs/2026-07-08-leanstral-foundry-design.md`.

Hard rules:
- This repo reads formal-mathfin; it NEVER writes to it. PRs into main are authored
  by R from the local clone after refinery, never by automation.
- API traffic (Mistral or any prover) carries only public-corpus statements and fresh
  textbook statements. Never held-out eval content (`formal-mathfin-evals`), never
  DMW/Dalang-named material.
- `MISTRAL_API_KEY` lives in `.env` (gitignored) or the shell env. Never committed.
- Machine proofs are scouts, not authors: nothing merges to main without the refinery
  (conceptually-right refactor + house idiom + 8-lens bar).

Layout: `probe/` calibration tooling · `targets/` statement files · `runs/` telemetry
JSONL · `reports/` calibration reports.
