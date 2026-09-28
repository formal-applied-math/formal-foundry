"""Pure helper functions for the Leanstral calibration probe. Stdlib only."""

from __future__ import annotations

import hashlib
import json
import re

ALLOWED_AXIOMS = ["propext", "Classical.choice", "Quot.sound"]

# The forbidden-text screen, kept identical to the target library's own values gate
# (`tests/test_values.py::FORBIDDEN_PATTERNS` in the flagship), which is what finally
# accepts or rejects a PR. It used to be a raw substring list, and two things went wrong:
# `"hint"` matched the binders `hintS2`/`hintEx` in cal-bk-57 and the hypothesis
# `have hint` in cal-bk-80, so no proof of either could ever pass a gate the library
# itself does not impose; and the tokens the library DOES forbid (`rw?`, `simp?`,
# `hammer`, `#loogle`, `leansearch`) sailed through here and would only have been caught
# at PR time. Matched on word boundaries in comment-stripped text, like the library.
FORBIDDEN_PATTERNS = (
    (re.compile(r"\bsorry\b"), "sorry"),
    (re.compile(r"\badmit\b"), "admit"),
    (re.compile(r"\bnative_decide\b"), "native_decide"),
    (re.compile(r"\bpolyrith\b"), "polyrith"),
    (re.compile(r"\bexact\?"), "exact?"),
    (re.compile(r"\bapply\?"), "apply?"),
    (re.compile(r"\brw\?"), "rw?"),
    (re.compile(r"\bsimp\?"), "simp?"),
    (re.compile(r"\bhammer\b"), "hammer"),
    (re.compile(r"#loogle"), "#loogle"),
    (re.compile(r"\bleansearch\b", re.IGNORECASE), "leansearch"),
)
FORBIDDEN = [label for _pattern, label in FORBIDDEN_PATTERNS]

_SORRY_RE = re.compile(r"\bsorry\b")


def strip_lean_comments(src: str) -> str:
    """Remove Lean comments — nested `/- -/` blocks (docstrings included) and `--` line
    comments — keeping newlines so line numbers stay stable. String literals are
    respected, so a `--` inside a string survives. A port of the target library's own
    `strip_comments` (its `tools/verify` declaration index), so both sides read the
    same text."""
    out = []
    i, n = 0, len(src)
    depth = 0
    in_string = False
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if depth == 0 and not in_string and c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if in_string:
            if c == "\\":
                out.append(c)
                out.append(nxt)
                i += 2
                continue
            if c == '"':
                in_string = False
            out.append(c)
            i += 1
            continue
        if c == "/" and nxt == "-":
            depth += 1
            i += 2
            continue
        if depth > 0:
            if c == "-" and nxt == "/":
                depth -= 1
                i += 2
                continue
            if c == "\n":
                out.append(c)
            i += 1
            continue
        if c == "-" and nxt == "-":
            while i < n and src[i] != "\n":
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def count_sorries(code: str) -> int:
    """`sorry` occurrences in CODE, not in prose — a docstring saying "no sorry" is not
    one. Every "is this still a stub?" question in the pipeline goes through here."""
    return len(_SORRY_RE.findall(strip_lean_comments(code)))


def has_sorry(code: str) -> bool:
    return count_sorries(code) > 0

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


def best_failure(results: list[dict]) -> int:
    """Index of the failing candidate with the fewest compiler errors — the most
    promising to repair (Goedel-style: build the repair round on the closest
    miss). Ties resolve to the earliest."""
    return min(range(len(results)), key=lambda i: len(results[i].get("errors", [])))


def slop_report(code: str) -> dict:
    stripped = strip_lean_comments(code)
    found = [label for pattern, label in FORBIDDEN_PATTERNS if pattern.search(stripped)]
    brackets = re.findall(r"\[([^\[\]]*)\]", code)
    max_args = max((len([a for a in b.split(",") if a.strip()]) for b in brackets),
                   default=0)
    return {
        "forbidden": found,
        "line_count": code.count("\n") + 1,
        "max_bracket_args": max_args,
    }


# consumable exports (`def`/`abbrev`/`structure`) — used both to MEASURE pointer
# modules (routing) and to LINT drafted stubs. Deliberately excludes `theorem`.
DEF_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?(?:noncomputable\s+)?(?:def|abbrev|structure)\s+([A-Za-z0-9_'.]+)",
    re.MULTILINE,
)


def _camel(name: str) -> str:
    return re.sub(r"_(\w)", lambda m: m.group(1).upper(), name)


def lint_violations(code: str) -> list[str]:
    """The main repo's `lake lint` classes checkable textually — the two that
    rejected autoform PR #123: `defsWithUnderscore` (def names must be
    lowerCamelCase; theorem names stay snake_case) and `docBlame` (every
    def/abbrev/structure needs a `/-- … -/` immediately above — a `/-!` module
    doc does not count). Structure FIELDS are not checked (no reliable textual
    anchor); the main repo's CI stays the backstop for that class."""
    out = []
    for m in DEF_RE.finditer(code):
        name = m.group(1)
        if "_" in name:
            out.append(f"def `{name}`: underscore in a definition name (defsWithUnderscore) "
                       f"— rename to lowerCamelCase (e.g. `{_camel(name)}`)")
        prefix = code[:m.start()].rstrip()
        has_doc = (prefix.endswith("-/") and prefix.rfind("/--") != -1
                   and prefix.rfind("/--") == prefix.rfind("/-"))
        if not has_doc:
            out.append(f"def `{name}`: missing docstring (docBlame) — put `/-- … -/` "
                       f"immediately above the definition")
    return out


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def append_jsonl(path: str, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# `:= [by] [exact] [Iff.]rfl` as a whole proof term; the negative lookahead keeps
# it from firing inside a longer identifier (e.g. `foo_rfl`, `rfl_lemma`).
_RFL_PROOF_RE = re.compile(r":=(by)?(exact)?(Iff\.)?rfl(?![A-Za-z0-9_.])")


def rfl_proof_present(text: str) -> bool:
    """True if a module's proof is a definitional/`rfl` discharge — a
    reduced_core-in-disguise the values gate rejects for a `full` entry. Catches
    `:= rfl`, `:= by rfl`, `:= Iff.rfl`, `:= by exact rfl` (with the term ending the
    line or continuing on the next), ANYWHERE, not just at EOF — the shell glob it
    replaces missed `:= by rfl` before a namespace `end` and the `:=byrfl` variant.
    Comment/import/structure lines are ignored; matching is per-line so a trailing
    `end <Namespace>` never fuses onto the `rfl`."""
    prev_opens_proof = False
    for ln in text.splitlines():
        if re.match(r"\s*(--|/-|import|module|open|namespace|variable|@\[)", ln):
            continue
        compact = re.sub(r"[ \t]+", "", ln)
        if not compact:
            continue
        if _RFL_PROOF_RE.search(compact):
            return True
        if prev_opens_proof and re.fullmatch(r"(exact)?(Iff\.)?rfl", compact):
            return True   # bare rfl continuing a `:=`/`:= by` from the previous line
        prev_opens_proof = compact.endswith(":=") or compact.endswith(":=by")
    return False
