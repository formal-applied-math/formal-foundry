# Frontier drafter (item I) — design

**Goal:** route the DRAFT stage (intent + formalize + emit) of the autoform pipe to
Claude via `claude -p`, while Leanstral keeps PROVE and the gate battery is
untouched. Backlog: `docs/upgrade-backlog.md` §I (DECIDED R 2026-07-23, build-first).

**Decision (R, 2026-07-24):** build all four options — both run-locations (1a local,
1b CI-cron) and both drafter modes (2a completion adapter, 2b agentic session).
Sequenced by risk/testability so each lands validated.

## Architecture — the engine switch is a chat_fn swap

The draft stages already take *injected* chat functions, so the switch changes only
*which fn the DRAFT stage calls*; intent/formalize/emit/gates/manifest/open-pr stay
byte-identical (the SP1–SP3 payoff: gates are drafter-agnostic).

```
[drafter]
engine = "mistral" | "claude"     # default mistral (today's behaviour + fallback)
mode   = "completion" | "agentic" # only meaningful for engine=claude
```

- `engine="mistral"` (default): intent = magistral, formalize = leanstral — unchanged.
- `engine="claude"`: intent_fn and formalize_fn both become the claude adapter.
- **Judge / intent-fidelity stay magistral; prove + kernel gates stay leanstral.**
  Only the draft chat_fn swaps.

**Required refactor (small):** today `reason_fn` serves BOTH the intent draft and the
judge/fidelity gates (`refill` passes `reason_fn` to `draft_intent` AND to
`semantic_verdict`). Split them: add an `intent_fn` param to `refill` (defaults to
`reason_fn` for back-compat) used only by `draft_intent`; the judge keeps `reason_fn`.
That is what lets the draft go to Claude while the judge stays magistral.

## The claude adapter

`claude_draft_fn(messages) -> (text, tokens)` — a drop-in for `reason_fn`/`formalize_fn`:
- flattens the system+user messages into `claude -p <user> --append-system-prompt
  <system> --output-format json`, tools DISABLED (pure completion, no file/bash/agentic
  side effects), returns the result text + usage tokens (json `.usage`).
- Auth: resolves from the ambient `~/.claude` (local) or a materialised credential (CI).
  No API key.
- Injectable ⇒ unit-tested with a FAKE claude_fn (no live model); the real `claude -p`
  is validated by a live probe.

**Attribution (unchanged, non-negotiable):** `Co-Authored-By: Leanstral` only on
autoform PRs; Claude is NEVER attributed anywhere. Whether the PR body discloses a
frontier-assisted draft = R's call at first merge (default: no disclosure).

**Caps handling:** a subscription cap/usage error from `claude -p` is caught by the
adapter and raised as a distinct signal; `refill` treats that target as deferred
(requeue, NO obstruction recorded) OR falls back to the mistral drafter for that tick
(config `on_cap = "defer" | "fallback"`). Cron retries absorb it, same as model flakes.

## Phasing (each phase independently valuable + validated)

**Phase 1 — engine switch + completion adapter + local auth (1a + 2a).** The
foundation. Config `[drafter]`, `intent_fn` split, `claude_draft_fn` (completion),
CLI wiring to select it. Unit tests with a fake claude_fn (message→prompt shape, json
parse, cap-error → defer/fallback, engine/mode selection). Live probe: run
`refill --only 53` locally with `engine=claude` and confirm the draft reaches prove
(the obstruction census is the did-it-help signal). LOCAL only — this box's `~/.claude`.

**Phase 2 — agentic drafter (2b).** `mode="agentic"`: one `claude -p` session WITH the
lean tools (lean-lsp / daemon) that self-validates the stub against Lean before
returning an already-elaborating stub — bypassing the mistral formalize-repair loop
(Claude does its own repair). Needs daemon-slot coordination (one Lean process; flip as
the vibe harness does). Bigger; built after Phase 1 proves the plumbing.

**Phase 2b — autonomous-tick slot management (DONE).** The wrinkle: in `mode="agentic"`
the FORMALIZE stage needs lean-lsp up while the gate battery (`depth`/`triviality`/
`vacuity`/`disproof`, all `check_fn=daemon_check`; the prover gates need the fast
persistent daemon) needs the daemon — both inside one `refill` call. Resolution: `refill`
takes an injected `slot_switch_fn` and, in the agentic branch, calls
`slot_switch_fn("lean-lsp")` before the agentic formalize and `slot_switch_fn("daemon")`
after — so the gates run on the daemon. `scripts/slot-switch.sh <daemon|lean-lsp>` is the
flip (stop the other slot, up the target, wait ready; `stop` not `down`, per the memory
doctrine); `main` wires `slot_switch_fn` to it when engine=claude & mode=agentic. The
flips are cheap (lean-lsp ~10s, daemon ~55s on the warm olean cache). The cron ends each
refill on the daemon, so build_manifest/ledger/open-pr are unaffected. Default
mode=completion → no flips, cron byte-identical.

**Phase 3 — CI-cron auth (1b).** Make `engine=claude` work in the GH cron: materialise
the OAuth credential from a CI secret into `~/.claude` before the tick, refresh-aware.
This IS a new secret surface (the backlog's "no new secret surface" was the hope) and
OAuth tokens rotate, so it is last and gated on Phase 1/2 proving the value.

## Validation set (backlog §I)

The live stuck families the mistral drafter died on: #53, #61, #72, #88, #108
(+ #109/#60 unknown-id). Success = seeds passing the gates and reaching prove, measured
by the obstruction census — not a synthetic bench.

## Non-goals / watch

- The gate battery is untouched; a stronger drafter RAISES the stakes on the
  fidelity/faithfulness/instance-probe gates (item J tightens the prove-path pin).
- The mistral path stays intact as the default + fallback; the cron is byte-identical
  until `engine` is flipped.
