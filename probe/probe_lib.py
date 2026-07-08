"""Pure helper functions for the Leanstral calibration probe. Stdlib only."""

from __future__ import annotations

import hashlib
import json
import re

STANDARD_AXIOMS = "[propext, Classical.choice, Quot.sound]"

FORBIDDEN = [
    "sorry", "admit", "native_decide", "polyrith",
    "exact?", "apply?", "hint",
]

_FENCE_RE = re.compile(r"```(lean)?\s*\n(.*?)```", re.DOTALL)


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
    return (
        "You are given a Lean 4 file from a Mathlib-based project "
        "(Lean toolchain pinned by the project). Replace the `sorry` with a "
        "complete proof.\n"
        "Rules:\n"
        "- Output the COMPLETE file in a single ```lean code block.\n"
        "- Do not change the theorem statement, imports, or anything else.\n"
        "- Forbidden: sorry, admit, native_decide, polyrith, exact?, apply?, "
        "hint, new axioms.\n\n"
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
    """First message + the last keep_last (assistant, user) exchange pairs."""
    if len(messages) <= 1 + 2 * keep_last:
        return messages
    return [messages[0]] + messages[-2 * keep_last:]


def axiom_guard_block(file_content: str, decl_name: str) -> str:
    return (
        f"{file_content}\n\n"
        f"/-- info: '{decl_name}' depends on axioms: {STANDARD_AXIOMS} -/\n"
        f"#guard_msgs in\n"
        f"#print axioms {decl_name}\n"
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
