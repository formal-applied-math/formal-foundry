"""First-pass refinery punch list shipped in an autoformalized PR (Task 2.7).

The unpriced bottleneck is the HUMAN refinery (the 8-lens rewrite before merge). This is a
chat-mediated review (Claude by default) over the proven candidate that produces the
MECHANICAL half of that pass —
unused/gratuitous constructs, wrapper smell, docstring/register, obvious golf — as a
checklist the human refiner starts from. It is **soft: it never gates** (a chat failure
still opens the PR) and it deliberately does NOT touch the taste half (inspired math,
architecture, statement faithfulness), which stays human/Claude. open-pr.sh embeds it in a
collapsed "first-pass refinery" section of the PR body.
"""

from __future__ import annotations

import argparse
import os
import sys

REFINERY_SYSTEM = (
    "You are a Lean 4 code reviewer for {namespace} (on {deps}). You are given "
    "a PROVEN candidate module — it already compiles, is sorry-free and axiom-clean. Produce "
    "the MECHANICAL half of an 8-lens refinery pass: a punch list a human refiner starts "
    "from. Check ONLY these mechanical lenses, never taste:\n"
    "- ZERO SLOP: unused or gratuitous constructs — dead `have`s, unused hypotheses/binders, "
    "redundant `simp` lemmas, a leftover `classical`, imports the proof never uses.\n"
    "- ANTI-WRAPPER: a lemma that merely renames a single Mathlib/{namespace} result — name it so "
    "it can be inlined/deleted.\n"
    "- REGISTER: a missing or thin docstring; non-idiomatic naming; `=>` where `↦` is house "
    "style; `by exact`/`by exact_mod_cast` where a bare term is defeq; hand-written coercions.\n"
    "- OBVIOUS GOLF: a two-step `calc` that folds to `.trans`, a have-ladder a `suffices` "
    "would flatten, a hand-rolled 𝓝 ε–δ a squeeze would replace.\n"
    "Do NOT judge the mathematics, the architecture, or whether the statement is FAITHFUL — "
    "those are the human/taste half. If a lens is already clean, say so in one line.\n"
    "Respond with a markdown checklist ONLY: one `- [ ]` line per concrete finding (quote the "
    "construct), grouped under the four lens headings. No preamble, no summary."
)


def refinery_messages(pack, lean_text: str) -> list[dict]:
    """The review prompt, with the pack's library identity rendered in. Kept a
    format-string here rather than a pack file: every clause but the two names is
    field-neutral, and a second domain should inherit the lens list unchanged."""
    system = REFINERY_SYSTEM.format(namespace=pack.namespace,
                                    deps=pack.dependencies_inline)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "CANDIDATE MODULE:\n```lean\n" + lean_text.strip() + "\n```"},
    ]


def refinery_notes(pack, lean_text: str, *, chat_fn) -> str:
    """The mechanical-half refinery punch list. SOFT — it never raises and never gates: any
    failure (no key, API error, empty reply) returns a fallback line so the PR still opens.
    `chat_fn(messages)` returns `(content, tokens)` (a bare string is tolerated too)."""
    try:
        res = chat_fn(refinery_messages(pack, lean_text))
    except Exception as e:   # soft: the punch list must never block a PR
        return f"_(first-pass refinery notes unavailable: {str(e)[:80]})_"
    content = res[0] if isinstance(res, tuple) else res
    return (content or "").strip() or "_(first-pass refinery notes: model returned nothing)_"


def main() -> int:
    ap = argparse.ArgumentParser(description="first-pass refinery punch list (markdown) for a PR")
    ap.add_argument("--lean-file", required=True)
    ap.add_argument("--out", default=None, help="write here instead of stdout")
    ap.add_argument("--model", default=os.environ.get("REFINERY_MODEL", "claude-sonnet-5"))
    ap.add_argument("--domain", default=None, help="domain pack name")
    args = ap.parse_args()
    import domain_pack
    pack = domain_pack.load(args.domain or domain_pack.DEFAULT_NAME)
    try:
        text = open(args.lean_file, encoding="utf-8").read()
    except OSError as e:
        text = ""
        _ = e
    if not text:
        md = "_(first-pass refinery notes skipped: empty candidate)_"
    elif args.model.startswith("claude"):   # default: claude (mistral remains selectable via --model)
        from autoformalize import claude_draft_fn   # lazy: only when a real call is made
        md = refinery_notes(pack, text,
                            chat_fn=lambda msgs: claude_draft_fn(msgs, model=args.model))
    elif not os.environ.get("MISTRAL_API_KEY"):
        md = "_(first-pass refinery notes skipped: no MISTRAL_API_KEY for a mistral --model)_"
    else:
        from probe import mistral_chat   # lazy: only when a real call is made
        md = refinery_notes(pack, text, chat_fn=lambda msgs: mistral_chat(
            msgs, api_key=os.environ["MISTRAL_API_KEY"], model=args.model))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md + "\n")
    else:
        sys.stdout.write(md + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
