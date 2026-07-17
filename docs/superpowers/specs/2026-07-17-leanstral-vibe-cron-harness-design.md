# Move the cron prove step to the trained-for vibe ⇄ lean-lsp-mcp harness — design

Approved 2026-07-17 (R, in chat). Derived from the ML4TP Zulip harvest
(`docs/research/2026-07-17-ml4tp-zulip-harvest.md`) and `docs/upgrade-backlog.md`.

## Goal

Run the automated pipeline's prove step in the harness Leanstral 1.5 was
RL-trained for — an agentic loop where the model drives `lean-lsp-mcp` tools
(`lean_goal`, `lean_multi_attempt`, on-demand search) against a live Lean LSP —
instead of the current text-loop that pastes compiler error strings into
`/chat/completions`. Plus three low-regret upgrades (deepen the loop, triage
failures, harden the PR gate). Keep what makes sense for the agentic setup; drop
what it makes redundant.

## Approved decisions

- **CI strategy:** bake `vibe` + the Leanstral agent into the Docker image via
  `publish-image.yml` (never a local `docker compose build` — memory doctrine).
- **Sequencing:** get W1–W4 green LOCALLY first; do W5 (CI wiring) after, gated on
  the W0 spike confirming headless vibe works.
- **Keep/drop:** as listed below (R approved).

## Architecture — the new tick flow

The `mathfin-lean-lsp` service bind-mounts `MathFin/` read-write, so whatever vibe
edits inside `/app` appears on the host and is capturable. The one-Lean-process
doctrine forces daemon XOR lean-lsp, so the tick flips the Lean slot twice:

1. **daemon up** → refill + `build_manifest` (unchanged).
2. **down daemon, up lean-lsp** (`leanstral-vibe.sh` already enforces this).
3. materialize the queue stub → a scratch `.lean` under `/app` the LSP can open →
   **one deep headless vibe session** (`vibe --trust --auto-approve --max-turns N
   -p "<doctrine + task>"`) → read the completed file back from the host mount.
4. **down lean-lsp, up daemon**.
5. **gate** the captured file via the existing `daemon_check` (slop screen +
   axiom whitelist + kernel-clean) → on pass, `open-pr.sh`.
6. record.

## Keep / drop

**Drop** (redundant once the model drives the tools):
- **autop static tactic menu (Track B1)** — subsumed by `lean_multi_attempt`
  (arbitrary model-proposed tactics, ~5× faster). The `.scout` draft-PR path goes
  with it.
- **Track A mistral-embed injection + reactive lookup** — subsumed by the model's
  own `lean_local_search` (ripgrep over MathFin) + `loogle`/`leansearch`/
  `lean_state_search`, invoked on-demand.
- **`fanout` as parallel text candidates** — the model self-fans via
  `lean_multi_attempt`; depth (turns) replaces breadth.

Remove these from the prove LOOP and prune their now-dead tests. Keep the modules
on disk only if something else imports them (check before deleting).

**Keep** (still load-bearing):
- **House doctrine** system prompt (values gate, idioms, live `patterns.md`, pins)
  as the task preamble — `leanstral-vibe.sh` already injects it.
- **Pointer-based context pack** (`house_context.extract_signatures` from the
  issue's Pointers) — curated consume-don't-reprove grounding, not search.
- **Acceptance gate** (forbidden tactics + axiom whitelist + kernel-clean via
  `daemon_check`) — our strength; unchanged.
- **Manifest / refill / queue / ledger / open-pr / kernel-replay CI / AxiomAudit** —
  unchanged.

## The three suggestions, folded in

- **R1 (depth over breadth):** the first-class knob becomes vibe `--max-turns` (one
  deep session), replacing `fanout × tokens_per_attempt`. Retire fanout in
  `pipeline.toml`.
- **R5 (failure triage):** a small aggregator over `runs/…-attempts.jsonl` + the
  vibe session outcome that buckets misses into obstruction families
  (unknown-identifier / gives-up-mid-proof / doesn't-typecheck / times-out),
  surfaced in the tick summary.
- **R4 + R7 (integrity + notes):** add the Lean FRO `comparator` to `open-pr.sh` —
  kernel-replay check that the assembled MathFin statement still matches the queue
  stub's statement (catches drift during assembly) — and emit a
  statement-fidelity-notes `.md` in the PR (issue claim ↔ Lean statement ↔
  divergence).

## Workstreams

- **W0 — spike (I do this, not a subagent):** one target end-to-end through
  headless vibe locally: materialize stub → `/app` scratch file → one headless
  `leanstral-vibe.sh -p` session → capture → gate. Validates the linchpin. If vibe
  will not run headless/exit cleanly, STOP and pivot to Plan B.
- **W1 — tick restructure (local):** `pipeline-tick.sh` sequences daemon→lsp→daemon;
  new prove step calls headless vibe; drop autop + Track A injection; keep doctrine
  + pointer pack + gate.
- **W2 — depth knob:** `pipeline.toml` + `pipeline_lib.py` — `--max-turns` deep
  single session; retire fanout.
- **W3 — failure triage:** new `probe/triage.py` over `runs/` + vibe outcome →
  obstruction families in the tick summary.
- **W4 — PR-gate hardening:** `open-pr.sh` — add `comparator` statement-integrity
  check + emit statement-fidelity-notes.md.
- **W5 — CI wiring (after W0/local-green):** `publish-image.yml` bakes `vibe` + agent
  + `lean-lsp-mcp`; `pipeline.yml` runs the lean-lsp service + the daemon↔lsp flip +
  the restructured tick.

## Global constraints (bind every task)

- Docker-only for the Lean toolchain; never `python -m tools.verify` on host.
- **One Lean-loaded process locally, ever** (daemon XOR lean-lsp). Never run two.
- Never `docker compose build` locally; image changes go through
  `publish-image.yml` on main.
- `index/` stays gitignored (build artifact).
- No secrets in argv/logs; source `.env`, use vars only in headers/`-u`.
- No Claude attribution anywhere; Leanstral credited on autoform PROOF provenance.
- Commit/push only when R asks; specific `git add` paths only, never `-A`/`.`.
- Foundry code changes do NOT require an image republish EXCEPT W5 (which
  deliberately bakes new tooling into the image).
- The acceptance gate stays exactly as strict (this is where we're ahead).

## Risks + Plan B

- **Linchpin risk:** `vibe` may not run fully headless in CI (interactive setup,
  no clean exit, agent-install friction). W0 retires this locally first.
- **Plan B (if W0 fails):** drive our own thin tool-calling loop against Leanstral's
  API + `lean-lsp-mcp` (stdio) directly, replicating the agentic loop without the
  `vibe` CLI. Larger, but keeps the trained-for tool-driving behavior.
- **Cost:** an agentic session makes many tool calls/turns — higher cost per target
  than the text-loop. That is the intended deeper loop, but bound it with
  `--max-turns` and keep the per-tick cadence.

## Acceptance

- W0: one real target proved by headless vibe, captured, passes the existing gate,
  axiom-clean.
- W1–W4: the local tick produces a gate-passing, comparator-verified proof + a
  fidelity-notes artifact from a queued target, daemon↔lsp flips cleanly, autop +
  Track A removed with green tests.
- W5: `pipeline.yml` runs the restructured tick end-to-end on a CI runner and opens
  a PR (or files `autoform-blocked` on a non-assembling candidate).

## W0 spike result (2026-07-17) — PASS

Ran one headless vibe session on a trivial throwaway target
(`MathFin/_VibeSpike.lean : (1:ℝ)+1=2 := by sorry`). Outcome:

- **Headless vibe works end-to-end.** `vibe --trust --auto-approve --max-turns 20
  -p "<task>"` ran unattended, read the goal via the MCP, **edited the host file**
  (`by sorry` → `by norm_num`), reported "compiles with zero diagnostics", and
  **exited clean (0)** in ~2.5 min. No interactive hang. The linchpin is retired;
  Plan B (own tool-loop) is NOT needed.
- **Finding 1 — CWD alignment is required.** vibe edits files on the HOST from its
  CWD; the MCP reads goals from `/app` in the container. Running vibe with **CWD =
  the main repo** makes `MathFin/…` resolve to the same bind-mounted file on both
  sides. W1 must invoke vibe with CWD = `$MAIN` (the spike confirmed this works;
  `leanstral-vibe.sh` does not `cd`, so the caller sets CWD).
- **Finding 2 — the daemon↔lsp flip must use `stop`, never compose `down`.**
  `docker compose down lean-lsp` removed the SHARED `docker_default` network, which
  broke the daemon restart ("network not found"); recovery needed `rm -f` + `up
  --force-recreate`. W1's flip MUST use `docker compose stop lean-lsp` / `docker
  stop mathfin-lean-lsp` (preserve the network) and only ever have one Lean
  container running at a time. Bringing the other service up is `up -d <svc>` on the
  SAME project (`-p docker`), never a `down` in between.
