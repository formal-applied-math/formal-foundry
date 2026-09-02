"""af_prompts — prompt ASSEMBLY. The prose itself lives in the domain pack.

Every system prompt this module used to carry as a string constant now comes from
`domains/<name>/prompts/*.md` through `domain_pack.load(...)`. What stays here is
the assembly: how an issue, a context pack, prior lessons and a gate rejection get
threaded into the message list a model actually receives. That is the split
runbook 02 is after — the wording is the domain's, the choreography is the
foundry's — and it is why callers now reach a prompt as `pack.prompt("judge-system")`
rather than importing a constant.

Every entry point takes the `pack` as its first argument, and there is
deliberately no module-level default: two domains must be able to live in one
process, and a global would let the second silently inherit the first's
namespace. That constraint also retired `_DRAFTER_PROMPT`, the one module-level
global this file had — the drafter preamble is now a `drafter_preamble` argument
the caller computes once via `house_context.build_drafter_prompt`.

Extracted from autoformalize.py; see it for the pipeline overview.
"""
from __future__ import annotations

from domain_pack import DomainPack

__all__ = ['_issue_prose', 'judge_messages', '_assistant', 'intent_messages',
           'render_gate_feedback', 'GATE_FEEDBACK_DEFAULT']




# --- chat-mediated runners (claude: judge) ------------------------------------


def _issue_prose(issue: dict) -> str:
    return f"{issue.get('title', '')}\n{issue.get('body', '')}"




def judge_messages(pack: DomainPack, issue: dict, stub: str,
                   deferred: list[str] | None = None) -> list[dict]:
    declared = ""
    if deferred:
        bullets = "\n".join(f"- {d}" for d in deferred)
        declared = ("\n\nDECLARED DEFERRED (the drafter intentionally left these for "
                    "follow-up issues; do NOT fail the subset for omitting them):\n"
                    f"{bullets}")
    return [{"role": "system", "content": pack.prompt("judge-system")},
            {"role": "user",
             "content": f"ISSUE:\n{_issue_prose(issue)}\n\nCANDIDATE:\n```lean\n{stub}\n```{declared}"}]




def _assistant(content: str) -> dict:
    """An assistant turn safe to re-send. Mistral 400s on an empty-content assistant message
    ("Assistant message must have either content or tool_calls, but not none") — which a
    free-tier empty reply produces once threaded into a repair round — so substitute a
    placeholder (caught #61 in the 2026-07-15 forced tick)."""
    return {"role": "assistant", "content": content or "(no output)"}




# --- two-stage draft: intent + agentic formalize (both claude) ----------------
#
# The split still stands, but both stages are Claude now: it SPECIFIES the intended
# statement in precise prose first (no Lean), then FORMALIZES it agentically
# (`claude -p` + the lean-lsp MCP, self-validating to elaboration). Leanstral no
# longer drafts either stage — it only PROVES. See
# docs/superpowers/specs/2026-07-14-leanstral-drafter-two-stage-design.md.
#
# The system prompts these stages use are pack files:
#   intent-system.md · formalize-system.md · intent-defs-addendum.md
# The defs addendum (F1) is appended when the issue's primitives do not exist yet,
# so the intent must also SPECIFY the 1-3 definitions the module introduces.


def intent_messages(pack: DomainPack, issue: dict, context_pack: str,
                    feedback: str | None = None,
                    route: str = "theorem",
                    prior_unknowns: list[str] | None = None,
                    prior_lessons: str | None = None,
                    drafter_preamble: str = "") -> list[dict]:
    """The intent stage's messages. `drafter_preamble` is the wired drafter
    authority (pins + the statement-design section of patterns.md, from
    `house_context.build_drafter_prompt`); '' leaves the pack's base system prompt
    unchanged, which is what a caller that never wires it should get."""
    user = f"ISSUE #{issue.get('number')}: {_issue_prose(issue)}\n"
    if context_pack:
        user += "\nAvailable declarations you may reference:\n" + context_pack
    if route == "defs":
        user += pack.prompt("intent-defs-addendum")
        if prior_unknowns:
            user += ("\nPrior attempts guessed these MISSING declarations — define "
                     "equivalents where sensible: " + ", ".join(prior_unknowns))
    if prior_lessons:   # item K: what earlier TICKS tried + a rotating diversity nudge
        user += "\n\n" + prior_lessons
    if feedback:
        user += ("\n\n" + feedback
                 + "\nProduce a REVISED intent that fixes this; respond with the same JSON shape.")
    base = pack.prompt("intent-system")
    system = (drafter_preamble + "\n" + base) if drafter_preamble else base
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]




# --- semantic-gate feedback (the repair cascade's re-draft signal) ------------
#
# The only repaired failure class used to be compilation (formalize_with_repair);
# every semantic gate was a terminal skip, so the drafter was never told WHY a
# clean-elaborating draft was rejected (design: 2026-07-17-semantic-repair-cascade).
# Each gate gets a repair DIRECTION in the pack's `gate-instructions.json`; the
# block is sent to BOTH stages of the next attempt (Claude may need to re-frame the
# intent, or the agentic formalize step must stop inlining what it should consume).
#
# The directions are the DOMAIN's — they name its objects and its recurring failure
# shapes — but the default below is field-neutral, so an unmapped gate still
# produces a usable instruction instead of an empty one.

GATE_FEEDBACK_DEFAULT = "Fix the reported failure without weakening any fact."


def render_gate_feedback(pack: DomainPack, gate: str, detail: str,
                         stub: str | None) -> str:
    """The re-draft feedback block for a semantic-gate rejection: the rejected stub
    (when one exists), the gate's own verdict, and the gate-specific revision
    instruction."""
    txt = f"PREVIOUS ATTEMPT — rejected by the `{gate}` gate"
    if detail:
        txt += f": {detail}"
    txt += "\n"
    if stub:
        txt += f"```lean\n{stub.strip()}\n```\n"
    txt += "REVISE: " + pack.gate_instruction(gate, GATE_FEEDBACK_DEFAULT)
    return txt
