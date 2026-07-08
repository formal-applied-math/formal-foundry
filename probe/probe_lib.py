"""Pure helper functions for the Leanstral calibration probe. Stdlib only."""

from __future__ import annotations

import hashlib
import json
import re

ALLOWED_AXIOMS = ["propext", "Classical.choice", "Quot.sound"]

FORBIDDEN = [
    "sorry", "admit", "native_decide", "polyrith",
    "exact?", "apply?", "hint",
]

_FENCE_RE = re.compile(r"```(lean)?\s*\n(.*?)```", re.DOTALL)


def normalize_content(content) -> str:
    """Reduce a chat message's content to answer text.

    Plain mode returns a string. Reasoning mode (reasoning_effort set) returns a
    list of blocks — e.g. [{"type": "thinking", ...}, {"type": "text", "text": …}].
    Keep the `text` blocks (the answer, incl. the ```lean fence); drop the
    internal `thinking` blocks.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return str(content)


def extract_lean_code(text: str) -> str | None:
    blocks = _FENCE_RE.findall(text)
    if not blocks:
        return None
    lean = [body for lang, body in blocks if lang == "lean"]
    body = lean[-1] if lean else blocks[-1][1]
    return body.strip()


class TokenLedger:
    def __init__(self, budget: int):
        self.budget = budget
        self.spent = 0

    def add(self, n: int) -> None:
        self.spent += n

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.budget


def build_initial_prompt(file_content: str) -> str:
    # The house doctrine, idioms, values, and pins live in the system message
    # (house_context.build_system_prompt); the user turn just presents the file.
    return (
        "Replace the `sorry` with a complete proof. Output the COMPLETE file "
        "(imports and statement unchanged) in a single ```lean code block. "
        "The house rules, idioms, and pins are in the system message.\n\n"
        f"```lean\n{file_content}\n```"
    )


def build_repair_prompt(errors: list[str]) -> str:
    err = "\n".join(errors[:20])
    return (
        "The Lean compiler reports:\n"
        f"```\n{err}\n```\n"
        "Fix the proof. Output the corrected COMPLETE file in a single "
        "```lean code block. Same rules as before."
    )


def window_messages(messages: list[dict], keep_last: int = 3) -> list[dict]:
    """Leading system message(s) + first user message + last keep_last pairs.

    Preserves the whole head (any `system` messages carrying the house doctrine,
    plus the first user turn with the file) so the agent never loses its context
    as the transcript is windowed for long repair loops.
    """
    head = 0
    while head < len(messages) and messages[head].get("role") == "system":
        head += 1
    head = min(head + 1, len(messages))  # + the first user message
    if len(messages) <= head + 2 * keep_last:
        return messages
    return messages[:head] + messages[-2 * keep_last:]


def axiom_guard_block(file_content: str, decl_name: str) -> str:
    """Append a subset check: the proof's axioms must lie within ALLOWED_AXIOMS.

    The daemon drops `#print axioms` info messages, and `#guard_msgs` only does
    exact-match (so it rejects a clean proof that depends on *fewer* than the
    three standard axioms). Instead we collect the axioms programmatically and
    `throwError` on any that is not allowed — a surfaced error the daemon
    reports. A proof using a subset of the standard axioms passes; `sorryAx`
    (or any exotic axiom, e.g. native_decide's `Lean.ofReduceBool`) fails.
    """
    allowed = ", ".join(f"`{a}" for a in ALLOWED_AXIOMS)
    return (
        f"{file_content}\n\n"
        "open Lean Elab Command in\n"
        "run_cmd do\n"
        f"  let axs ← Lean.collectAxioms `{decl_name}\n"
        f"  let allowed : List Lean.Name := [{allowed}]\n"
        "  for ax in axs do\n"
        "    unless allowed.contains ax do\n"
        '      throwError s!"DISALLOWED_AXIOM {ax}"\n'
    )


def slop_report(code: str) -> dict:
    found = [w for w in FORBIDDEN if w in code]
    brackets = re.findall(r"\[([^\[\]]*)\]", code)
    max_args = max((len([a for a in b.split(",") if a.strip()]) for b in brackets),
                   default=0)
    return {
        "forbidden": found,
        "line_count": code.count("\n") + 1,
        "max_bracket_args": max_args,
    }


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def append_jsonl(path: str, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
