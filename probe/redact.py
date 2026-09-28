"""Credentials never reach an agent that does not need them, or a file that gets published.

The foundry is a public repository whose CI runs LLM agents on a runner that holds its
secrets, and whose persist step commits `runs/` — including every agent transcript, i.e.
every tool result the agent saw — to that public repository. Three defences, each cheap:

- `agent_env(engine)`: the environment an agent subprocess gets. Every credential-named
  variable is removed except the one that agent's own CLI authenticates with. The prover
  used to inherit the whole CI environment, `MAIN_PR_TOKEN` included.
- `secret_findings(text)`: the KINDS of credential-shaped strings in a text (never the
  value), so a candidate or an entry carrying one is rejected before it is pushed.
- `redact(...)` / `redact_paths(...)`: replace, in place, every known secret VALUE (read
  from the environment, so formats without a prefix are caught too) and every
  credential-shaped string with a placeholder. Run over each session's transcript as it
  is captured, and over everything the persist step commits.

Stdlib only.
"""
from __future__ import annotations

import os
import re
import sys

#: an environment variable whose NAME says it holds a credential
SECRET_NAME = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|_KEY$|^KEY$|_PAT$)",
                         re.IGNORECASE)

#: the one credential each agent CLI authenticates with — the only one it keeps
AGENT_AUTH = {
    "claude": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
    "leanstral": ("MISTRAL_API_KEY",),
}

#: credential SHAPES, labelled by kind; the label is safe to log, the match is not
SECRET_PATTERNS = (
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("git-basic-auth", re.compile(r"AUTHORIZATION: basic [A-Za-z0-9+/=]{12,}", re.IGNORECASE)),
    ("token-in-url", re.compile(r"x-access-token:[^@\s\"']{8,}@")),
)

#: values shorter than this are not treated as secrets (too likely to occur by accident)
MIN_SECRET_LEN = 12


def agent_env(engine: str, base: dict | None = None) -> dict:
    """A copy of the environment for an agent subprocess of `engine`, with every
    credential removed except the ones that engine's own CLI authenticates with."""
    env = dict(os.environ if base is None else base)
    keep = set(AGENT_AUTH.get(engine, ()))
    return {k: v for k, v in env.items() if k in keep or not SECRET_NAME.search(k)}


def secret_values(env: dict | None = None) -> list[str]:
    """The values of every credential-named variable in `env` (default: this process),
    longest first so a value that contains another is replaced whole."""
    env = os.environ if env is None else env
    vals = {v for k, v in env.items() if SECRET_NAME.search(k) and v and len(v) >= MIN_SECRET_LEN}
    return sorted(vals, key=len, reverse=True)


def secret_findings(text: str, values: list[str] | None = None) -> list[str]:
    """Sorted kinds of credential-shaped strings (and known secret values) in `text`."""
    kinds = {kind for kind, pat in SECRET_PATTERNS if pat.search(text or "")}
    if values and any(v in (text or "") for v in values):
        kinds.add("known-secret-value")
    return sorted(kinds)


def redact(text: str, values: list[str] | None = None) -> tuple[str, int]:
    """`text` with every known secret value and every credential-shaped string replaced
    by a `[REDACTED:<kind>]` placeholder; returns `(text, replacements)`."""
    n = 0
    for v in values or []:
        if v in text:
            n += text.count(v)
            text = text.replace(v, "[REDACTED:known-secret-value]")
    for kind, pat in SECRET_PATTERNS:
        text, k = pat.subn(f"[REDACTED:{kind}]", text)
        n += k
    return text, n


def redact_file(path: str, values: list[str] | None = None) -> int:
    """Redact one file in place; returns the number of replacements (0 leaves it untouched)."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return 0
    new, n = redact(text, values)
    if n:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
    return n


def redact_paths(paths: list[str], values: list[str] | None = None) -> dict[str, int]:
    """Redact every file under `paths`; `{path: replacements}` for the files changed."""
    changed: dict[str, int] = {}
    for root in paths:
        files = [root] if os.path.isfile(root) else [
            os.path.join(d, f) for d, _dirs, fs in os.walk(root) for f in fs]
        for p in files:
            n = redact_file(p, values)
            if n:
                changed[p] = n
    return changed


def main(argv=None) -> int:
    """`python3 redact.py PATH...` — redact in place before anything is committed. Prints
    which files changed and how many replacements, never a value. Exit 0 either way:
    this runs in the persist step, and a redaction is the success case, not a failure."""
    paths = list(sys.argv[1:] if argv is None else argv)
    changed = redact_paths(paths, secret_values())
    for p, n in sorted(changed.items()):
        print(f"::warning::[redact] {p}: {n} credential(s) redacted before commit")
    print(f"[redact] scanned {', '.join(paths) or '(nothing)'}: {len(changed)} file(s) redacted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
