"""Statement-fidelity notes shipped alongside an autoformalized PR.

ML4TP harvest R7 / the 5-color-strong-majority practice: a human-readable map from
the informal claim to the Lean statement, plus where to look for divergence — so the
fidelity judgment is auditable by someone who is NOT the pipeline. The kernel checks
the *proof*; it cannot check that the *statement* means the claim. That is this
document's job. Pure/stdlib; open-pr.sh feeds it the issue text + assembled statement.
"""

from __future__ import annotations

import argparse
import json
import sys


def _fence(code: str, lang: str = "lean") -> str:
    return f"```{lang}\n{code.strip()}\n```"


def render_notes(*, target_id: str, lean_statement: str, issue_number=None,
                 issue_title: str = "", issue_task: str = "", pointers=None,
                 provenance=None, harness: str = "vibe ⇄ lean-lsp-mcp") -> str:
    pointers = pointers or []
    provenance = provenance or {}
    out: list[str] = [
        f"# Statement-fidelity notes — `{target_id}`",
        "",
        "A reviewer's map from the informal claim to the Lean statement. The kernel "
        "checks the *proof*; it cannot check that the *statement* means the claim — "
        "that is this document's job.",
        "",
    ]
    if issue_number is not None:
        out += [f"**Source issue:** #{issue_number}"
                + (f" — {issue_title}" if issue_title else ""), ""]
    if issue_task.strip():
        out += ["## Informal claim (from the issue)", "", issue_task.strip(), ""]
    out += ["## Lean statement (as merged)", "", _fence(lean_statement), ""]
    if pointers:
        out += ["## Existing results it builds on (pointers)", ""]
        out += [f"- `{p}`" for p in pointers]
        out += [""]
    out += [
        "## What to check",
        "",
        "- Do the Lean hypotheses match every assumption in the informal claim "
        "(none dropped, none added)?",
        "- Is the conclusion the same proposition, not a weaker/vacuous variant "
        "(a trivially-true `≤`, an unused binder, a hypothesis that is secretly `False`)?",
        "- Are the domain objects the intended ones (the pointer defs above), not raw "
        "Mathlib stand-ins that only look right?",
        "",
    ]
    src = provenance.get("source")
    if src:
        model = provenance.get("model", "")
        out += [
            "## Provenance",
            "",
            f"- statement + proof: {src}" + (f" ({model})" if model else ""),
            f"- harness: {harness}",
            "- scout, not author: opened by the pipeline, reviewed + merged by a human.",
            "",
        ]
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="render statement-fidelity notes (markdown) for a PR")
    ap.add_argument("--target-id", required=True)
    ap.add_argument("--lean-statement", required=True, help="the assembled Lean statement text")
    ap.add_argument("--issue-number", type=int, default=None)
    ap.add_argument("--issue-title", default="")
    ap.add_argument("--issue-task", default="")
    ap.add_argument("--pointers", default="[]", help="JSON list of pointer names")
    ap.add_argument("--provenance", default="{}", help="JSON {source, model}")
    ap.add_argument("--out", default=None, help="write here instead of stdout")
    args = ap.parse_args()
    try:
        pointers = json.loads(args.pointers)
    except ValueError:
        pointers = []
    try:
        provenance = json.loads(args.provenance)
    except ValueError:
        provenance = {}
    md = render_notes(target_id=args.target_id, lean_statement=args.lean_statement,
                      issue_number=args.issue_number, issue_title=args.issue_title,
                      issue_task=args.issue_task, pointers=pointers, provenance=provenance)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
