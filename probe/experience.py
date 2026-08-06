"""Cross-tick rolling experience memory for a target (item K).

The cron retries a target across ticks, but every retry re-drafts and re-proves
nearly blind: `runs/*-summary.jsonl` records that attempt 3 died `fail_gate`, and
nothing carries *what was already tried* into attempt 4. The loop therefore
re-walks the same dead ends on our budget.

This is the missing half. After a failed attempt the tick folds that attempt into
a per-target **rolling summary** and stores it; the next tick renders it into the
prover task. Three properties are the whole design, and each is load-bearing:

1. **Rolling, not appended.** `record` feeds the summariser BOTH the prior summary
   and the new attempt, and takes its output as the new summary. Nothing is kept
   verbatim, so information from attempt 1 survives to attempt 9 only if it is
   still worth carrying — which is what a summariser is for, and what a growing
   log is not.
2. **Bounded.** The rendered block is capped (`MAX_EXPERIENCE_CHARS`). A memory
   that grows without limit eventually crowds out the context pack, which is the
   one thing miniCTX says we must not do.
3. **Fail-open.** No summariser, no API key, a raising model, a corrupt store —
   every one of these degrades to a deterministic mechanical digest, and a total
   failure degrades to no memory at all. This is measurement plumbing attached to
   the failure path; it must never be the reason a tick goes red.

Shape adapted from Axiomatic AI's `ExperienceProcessor` (`Axiomatic-AI/ax-prover-base`,
harvested 2026-08-06 — see `docs/research/2026-08-06-axiomatic-harvest.md`), which
runs the same rolling summary *within* one 50-iteration episode. Ours runs across
ticks instead, because that is where our re-drafting actually repeats. Their
stochastic-diversity instruction (Nexus's set, already logged as the second half of
item K) rides along in `render`.

Opt-in via `[autoformalize].experience`; off ⇒ prompts are byte-identical to
before this module existed. Pure stdlib; the store is `runs/experience.json`.
"""
from __future__ import annotations

import json
import os

# Lessons name Mathlib lemmas and tactic behaviour, so they go stale on a pin bump
# exactly like the gate/state caches. Bump to drop the store wholesale.
EXPERIENCE_GENERATION = "v1"

# Cap on the rendered block. Chosen to sit well under the context pack's share of
# the prompt: a memory that outgrows the premises it is meant to help apply has
# made the prompt worse, not better.
MAX_EXPERIENCE_CHARS = 4000

# Rotating diversity instruction (Nexus's stochastic-injection set). Indexed by
# attempt count, not sampled — a deterministic rotation is reproducible in tests
# and still breaks the repeat-the-same-approach loop, which is the only thing the
# randomness was buying.
DIVERSITY_INSTRUCTIONS = (
    "Decompose the goal into named intermediate `have` steps and close them one at a time.",
    "Combine the most promising ideas from the previous attempts rather than restarting.",
    "Try a completely different approach from the ones recorded above.",
)

_SUMMARISE_SYSTEM = (
    "You maintain a running lab notebook for one Lean 4 proof target across repeated "
    "attempts. You are given the notebook so far and the newest attempt. Return the "
    "REPLACEMENT notebook: what was tried, what the compiler/gate actually objected to, "
    "and which approaches are now ruled out. Preserve anything from the previous notebook "
    "that is still true — it is the only copy. Drop narration and restate nothing twice. "
    "Be specific about lemma names and error classes. Plain prose, no preamble, "
    "at most 200 words."
)


def _attempt_digest(attempt: dict) -> str:
    """The deterministic one-line-per-fact rendering of a single failed attempt.

    Used verbatim as the mechanical fallback summary, and as the summariser's view of
    the new attempt, so the LLM and the fallback read the same facts.
    """
    bits = []
    outcome = attempt.get("outcome")
    if outcome:
        bits.append(f"outcome: {outcome}")
    reason = attempt.get("reason")
    if reason:
        bits.append(f"reason: {reason}")
    errors = [e for e in (attempt.get("errors") or []) if e]
    if errors:
        # Two errors is the informative prefix: the first real failure plus whatever it
        # cascaded into. Beyond that a Lean error list is mostly repetition of the first.
        bits.append("errors: " + " | ".join(str(e).strip().replace("\n", " ")[:200]
                                            for e in errors[:2]))
    notes = attempt.get("notes")
    if notes:
        bits.append(f"notes: {notes}")
    return "; ".join(bits) or "outcome: unknown"


def _mechanical_summary(prior: str, attempt: dict, *, index: int) -> str:
    """Fallback roll-up: prior notebook + a numbered digest of the new attempt, tail-capped.

    Keeps the TAIL on overflow (the newest lessons describe the state the next attempt
    actually starts from) and marks the elision so a reader knows history was dropped.
    """
    line = f"attempt {index}: {_attempt_digest(attempt)}"
    rolled = f"{prior.strip()}\n{line}" if prior.strip() else line
    if len(rolled) <= MAX_EXPERIENCE_CHARS:
        return rolled
    return "[...earlier attempts elided...]\n" + rolled[-MAX_EXPERIENCE_CHARS:]


def summarize(prior: str, attempt: dict, *, index: int, summarize_fn=None) -> str:
    """Roll `prior` + `attempt` into the new notebook.

    `summarize_fn(messages) -> (text, tokens)` matches `probe.mistral_chat`'s contract so
    either chat backend drops in. Anything falsy, raising, or returning an empty body
    falls through to `_mechanical_summary` — the caller cannot tell, by design.
    """
    if summarize_fn is not None:
        try:
            reply, _tokens = summarize_fn([
                {"role": "system", "content": _SUMMARISE_SYSTEM},
                {"role": "user", "content":
                    f"── NOTEBOOK SO FAR ──\n{prior or '(empty — this is the first attempt)'}\n\n"
                    f"── NEWEST ATTEMPT ({index}) ──\n{_attempt_digest(attempt)}"},
            ])
            text = (reply or "").strip()
            if text:
                return text[:MAX_EXPERIENCE_CHARS]
        except Exception:      # noqa: BLE001 — memory is never a reason to fail a tick
            pass
    return _mechanical_summary(prior, attempt, index=index)


class ExperienceStore:
    """A `{target -> rolling notebook}` store, persisted to `path` as JSON.

    Reads predating `EXPERIENCE_GENERATION` (a toolchain bump) or a corrupt file start
    empty, matching `GateCache`/`StateCache`.
    """

    def __init__(self, path: str, generation: str = EXPERIENCE_GENERATION):
        self.path = path
        self.generation = generation
        self._entries: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and data.get("generation") == self.generation:
            entries = data.get("entries")
            if isinstance(entries, dict):
                self._entries = {k: v for k, v in entries.items() if isinstance(v, dict)}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"generation": self.generation, "entries": self._entries}, f)
        os.replace(tmp, self.path)   # atomic — a crash mid-write never corrupts the store

    def attempts(self, target: str) -> int:
        return int(self._entries.get(target, {}).get("attempts", 0))

    def get(self, target: str) -> str:
        """The raw notebook for `target`, or `""` if the target has no history."""
        return str(self._entries.get(target, {}).get("experience", ""))

    def record(self, target: str, attempt: dict, *, summarize_fn=None) -> str:
        """Fold one FAILED attempt into `target`'s notebook and persist. Returns the
        new notebook.

        Only failures are recorded: a pass ends the target, so its lessons have no next
        attempt to inform, and storing them would just dilute the notebook of a target
        that later regresses.
        """
        index = self.attempts(target) + 1
        rolled = summarize(self.get(target), attempt, index=index, summarize_fn=summarize_fn)
        self._entries[target] = {"experience": rolled, "attempts": index}
        self._save()
        return rolled

    def forget(self, target: str) -> None:
        """Drop `target`'s notebook, if it has one.

        A target that succeeded has no next attempt to inform, and if it ever regresses
        it should start clean rather than inherit the notes of attempts that eventually
        worked — those read as ruled-out approaches when one of them was the answer.
        """
        if self._entries.pop(target, None) is not None:
            self._save()

    def render(self, target: str, *, nudge: bool = True) -> str:
        """The prompt block for `target`, or `""` when there is nothing to say.

        Empty on a cold store, so a target on its first attempt gets a prompt identical
        to the pre-memory one — the same silent-until-useful discipline as
        `StateCache.suggestions()`.

        `nudge=False` omits the rotating diversity instruction, for callers that already
        supply one. The DRAFT path is such a caller: `af_routing.render_prior_lessons`
        has owned the rotation there since before this store existed, and two rotations
        in one prompt would sooner or later contradict each other.
        """
        notebook = self.get(target).strip()
        if not notebook:
            return ""
        if len(notebook) > MAX_EXPERIENCE_CHARS:
            notebook = "[...earlier attempts elided...]\n" + notebook[-MAX_EXPERIENCE_CHARS:]
        block = ("\n── LESSONS FROM PREVIOUS ATTEMPTS AT THIS TARGET ──\n"
                 "These are notes from earlier ticks that failed. Do not repeat a ruled-out "
                 "approach; the notes are a record, not a specification — the theorem "
                 "statement above still governs.\n"
                 f"{notebook}")
        if not nudge:
            return block
        # attempts-1 so the first retry reads instruction 0 rather than skipping it.
        rotation = DIVERSITY_INSTRUCTIONS[
            (self.attempts(target) - 1) % len(DIVERSITY_INSTRUCTIONS)]
        return f"{block}\nTHIS ATTEMPT: {rotation}"

    def report(self) -> dict:
        """Whether the memory is earning its tokens: targets carried, and how deep the
        retry chains actually run. A store where `max_attempts` never exceeds 1 means
        targets are not being retried, so the feature is ceremony and comes back out."""
        attempts = [int(e.get("attempts", 0)) for e in self._entries.values()]
        return {"targets": len(self._entries),
                "total_attempts": sum(attempts),
                "max_attempts": max(attempts, default=0),
                "retried_targets": sum(1 for a in attempts if a > 1),
                "chars": sum(len(str(e.get("experience", ""))) for e in self._entries.values())}
