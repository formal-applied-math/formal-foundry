"""Select the next autoformalization target from the formal-mathfin issue backlog.

The open issues ARE the curated backlog: each `type:proof` `status:ready` issue
carries the statement (its "Task" section) and the context modules (its
"Pointers" section), and a merged PR closes it. `scripts/*.sh` fetch them with
`gh issue list ... --json number,title,labels,body`; this module (pure, stdlib)
does the label parsing + tractability ordering. The prose body → Lean stub
authoring is a separate step (hand-seeded in Phase 1; the autoformalize sub-probe
automates it in the follow-on plan).
"""

from __future__ import annotations

# difficulty rank: lower = attempt first. good-first/small/medium are in scope;
# hard is out until the harness proves out on easier targets.
_DIFF_RANK = {"good-first": 0, "small": 1, "medium": 2}
_GOOD_FIRST_TITLE = "good first issue"  # GitHub's built-in label


def parse_labels(issue: dict) -> dict:
    """Map an issue's label list to its {status, type, difficulty, area} dims.

    Recognises the `key:value` label convention plus GitHub's built-in
    "good first issue" label (which maps to difficulty good-first)."""
    dims = {"status": None, "type": None, "difficulty": None, "area": None}
    for lab in issue.get("labels", []):
        name = lab["name"] if isinstance(lab, dict) else str(lab)
        if name == _GOOD_FIRST_TITLE:
            dims["difficulty"] = dims["difficulty"] or "good-first"
            continue
        for key in ("status", "type", "difficulty", "area"):
            prefix = key + ":"
            if name.startswith(prefix):
                dims[key] = name[len(prefix):]
    return dims


def _rank(issue: dict) -> int | None:
    """Tractability rank, or None if the issue is out of scope (not a ready
    proof, or blocked/research/too-hard)."""
    d = parse_labels(issue)
    if d["status"] != "ready" or d["type"] != "proof":
        return None
    return _DIFF_RANK.get(d["difficulty"])


def select_issues(raw: list[dict], *, max_difficulty: str = "medium") -> list[dict]:
    """`status:ready` + `type:proof` issues up to `max_difficulty`, ordered
    good-first → small → medium then by issue number. Each returned dict is
    {number, title, area, difficulty}."""
    cap = _DIFF_RANK.get(max_difficulty, 2)
    scored = []
    for issue in raw:
        r = _rank(issue)
        if r is None or r > cap:
            continue
        d = parse_labels(issue)
        scored.append((r, issue["number"], {
            "number": issue["number"], "title": issue.get("title", ""),
            "area": d["area"], "difficulty": d["difficulty"],
        }))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [s[2] for s in scored]
