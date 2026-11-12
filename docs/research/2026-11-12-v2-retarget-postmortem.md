# No pull request from 2026-07-27 to 2026-11-12: the token, then the library

The [September postmortem](2026-09-27-prover-outage-postmortem.md) ends with the prover
running again. It did run: on 2026-09-28 Claude proved `cal-bk-129` (the Merton
jump-diffusion delta) in one session. No pull request followed, and none has since. This
is the record of why, read from the GitHub Actions job logs and from formal-mathfin's
tree, not from `runs/`.

## 1. The token could push and could not open a pull request

`MAIN_PR_TOKEN` was replaced on 2026-09-28 at 02:22 UTC, after a push was refused with a
403. The new token can push to formal-mathfin and cannot create a pull request there.
From the next run on, every tick ended the same way:

```
 * [new branch]      autoform/cal-bk-129-pipeline-20261021-124059 -> …
pull request create failed: GraphQL: Resource not accessible by personal access token (createPullRequest)
[open-pr] TRANSIENT: gh pr create failed (candidate is green — retry next tick)
```

Thirteen ticks, 09-28 to 10-21. Each re-gated the kept proof, rebuilt the library, ran
lint, the regenerations, the ledger and the library's tests, pushed a branch, and failed
at `gh pr create`. The Phase 0 work did what it was built to do — every one of those
ticks was red, with the reason in the annotation — and the cron was switched off on
10-22. The red was read as "the pipeline is broken"; the annotation said which line.

Two things that made it worse than it needed to be:

- every tick pushed a NEW branch (`autoform/<id>-<run tag>`), so thirteen copies of the
  same commit were left on the library;
- formal-mathfin closed #129 on 10-08, and has its own
  `hasDerivAt_mertonCallPrice_spot`. The foundry went on assembling a contribution to
  a closed issue for two more weeks: nothing in the tick asks whether the source issue
  of a kept proof is still open.

The token is the maintainer's to fix. It needs *Contents*, *Pull requests* and *Issues*,
each read and write, on formal-mathfin.

## 2. The library reorganised, and the foundry kept copies of where things were

formal-mathfin's v2 layout landed on 2026-11-08: layers (`Probability/`, `Contracts/`,
`Pricing/`, `Models/`, `Risk/`, `Choice/`, `Insurance/`, `Markets/`), one corpus file
per area, a taxonomy the issue labels are derived from, a documentation standard, a
bibliography, a declaration index and a ratchet over it. Measured against that tree on
11-11, with the token question set aside:

| what the foundry assumed | what was true |
|---|---|
| issue pointers name files | 0 of the pointers in the 54 ready issues named a file that existed |
| `area:fixed-income` → `MathFin/FixedIncome/` | labels are taxonomy cells (`area:valuation/term`); the directory is `MathFin/Models/Rates/` |
| entries go in `benchmarks/mathematical_finance.json` | deleted; 28 files, one per area |
| the 16 staged stubs are the queue | every one imports modules that are gone |
| a module may `import Mathlib` | ratchet `whole_mathlib_imports`, baseline 0 |
| a one-line module docstring | hard gate: title, sections, References keyed to the bibliography, Tags |
| an entry needs a `reference` | and `citations` resolving against `docs/references.bib` |
| any true theorem is a contribution | ratchet `corpus_off_blueprint`: the entry must rest on a blueprint node |
| a merged proof closes its issue | 46 of the 54 ready issues name a FRONTIER node, which a merge may not orphan |
| the commit author is `mathfin-autoform` | the attribution gate reads git: an uncredited contributor |

None of these failed loudly. A pointer that did not exist was dropped as "a module to
create"; an unmapped area fell back to a CamelCase directory; the assembly step ran the
checks it knew and called the result green. A contribution in the old shape, placed
in a scratch copy of the library, failed five of its host-side gates (citations, the
docstring title, and the ratchet counts for whole-Mathlib imports, Main results and
Tags) before the attribution gate and the index ratchet were reached.

The common cause is the one the library's own CLAUDE.md names for itself: a copy. The
foundry held its own table of the library's layout, and a table is a second place for a
fact to be wrong.

## What changed

- **`probe/library_map.py`** reads the layout from the checkout at tick time. The pack
  says where the library keeps its map (`[layout]` in `target.toml`); nothing is copied.
  A moved pointer is followed by basename.
- **The emitter writes what the library gates**: the documentation standard, the
  citation (a key the drafter picks from the bibliography, checked before any session is
  paid for), the cell's subject codes.
- **The map gate.** A Lean meta check at draft time, beside the depth gate: the
  statement's type must reach a declaration the blueprint tags, through the library's
  own definitions. It is the sufficient half of the library's ratchet.
- **Assembly runs the pack's recipe.** `domains/mathfin/regen.sh` is formal-mathfin's
  `build.yml`, step for step, including the shake that narrows the stub's whole-Mathlib
  import and the declaration index; `host-gates.sh` runs on the committed tree.
- **A frontier node is not closed by a machine.** The entry goes under the node's `core`
  and the PR `refs` the issue.
- **One branch per target**, and the commit is a `[bot]`'s.
- **A stub whose issue closed is retired** before planning (`issue_state.retire_closed`),
  on a confirmed lookup only.
- **The assembly canary** (`scripts/assembly-smoke.sh`, `assembly-smoke.yml`): the whole
  assembly path on a fixture theorem, in the verify image, no model call, no push. On a
  PR that touches the path, and weekly, because the library moves without telling us.

## What the canary found before a tick paid for it

Three canary runs on the retarget's own pull request, each about ten minutes on a runner,
no model call:

1. *Run 43145772467.* The build, the shake (`public import Mathlib` narrowed to the one
   module the proof needs), lint, the regenerations, the ledger row and the index ratchets
   passed. One library gate refused: `formalization.yaml`'s disclosure is derived from
   each entry's `provenance.model`, and the fixture was signed by a prover that does not
   exist. The canary now signs with the model `pipeline.toml` configures, so the run also
   answers whether the library accepts an entry that prover signs.
2. *Run 43147663249.* Every gate on the contribution passed. The library's blueprint
   check refused, for three open issues naming labels that main retired twenty minutes
   later: main itself was red. A red host gate is now re-asked of the base commit, and
   red there is a retry, not a `fail_assembly` verdict against the proof.
3. *Run 43150003089.* Green: `cal-smoke-1 assembled green … not pushed`, on main at
   `88f4284c` (871 ledger rows fresh, 137 library tests, blueprint check 0 problems).

And two read off the code while the runs were in flight, either of which would have cost
the first real tick:

- **The prover's doctrine no longer fits on a command line.** Built from the v2 library
  it is 134 KB; Linux allows one argument 128 KiB. `claude-prove.sh` chose between a file
  and an argument by looking for `--append-system-prompt-file` in `claude --help`, which
  does not list it (2.1.322 accepts it). It is always a file now.
- **The refill exited without `MISTRAL_API_KEY`**, though Claude drafts, judges and
  proves. The key now buys embedding retrieval only, and retrieval fails open.

## What this does not fix

- A candidate the assembly recipe refuses is recorded `fail_assembly` and its stub stays
  staged: the issue is not redrafted with the reason. The reason is now in the
  `autoform-blocked` issue (filed with the foundry's own token; the library's had no
  rights here and had filed nothing since 09-28) and in `runs/<tag>-<id>.assembly.log`.
- The map gate reads the statement only. A proof can reach the map through a lemma the
  statement does not name; such a target is refused at draft time though its build would
  have passed.
- Until `MAIN_PR_TOKEN` can create a pull request, a green proof ends as a pushed branch
  and one `autoform-ready` issue here carrying the link that opens it. A pull request
  opened from that link is found by its branch on the next tick.
- An issue served once (an entry with `provenance.issue = N`) is skipped until a person
  splits or closes it. The pipeline does not yet take the `deferred` facts of its own
  subset proof as its next target.
