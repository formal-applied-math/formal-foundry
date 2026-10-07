"""Can the token deliver? Ask GitHub before a tick spends anything on a proof.

Six ticks, 2026-09-28 to 2026-10-07, ended the same way: cal-bk-129 passed the gate,
built green in the verify image, its branch was pushed to the library — and
`gh pr create` answered `GraphQL: Resource not accessible by personal access token
(createPullRequest)`. `MAIN_PR_TOKEN` is a fine-grained PAT. It had been granted
Contents: write (after the push itself was refused with a 403 on 2026-09-28) but not
Pull requests: write. `open-pr.sh` filed the refusal under `transient`, so the target
was never recorded, the planner chose it again, and the thirteen unattempted targets
behind it never ran. Every tick re-gated the same proof, rebuilt the library, asked a
model for review notes, pushed one more orphan branch and went red with `open-pr rc=4`.

A missing permission is not transient. No retry fixes it; a person editing the token
does. So the tick asks first, for the price of two HTTP calls, and when the answer is
no it stands down with the permission named.

How to ask without doing. No endpoint lists a fine-grained token's permissions, so each
write the delivery makes is attempted in a form GitHub must refuse: a ref pointing at
the zero object, a pull request from a branch that does not exist. GitHub authorizes a
request before it reads the body, so

    422  the token holds the permission (the body was read, and rejected)
    403  `Resource not accessible by …`: it does not
    401  the token is invalid, expired or revoked
    404  the token cannot see the repository

and neither probe can create anything, whatever the token holds. Anything else — a rate
limit, a 5xx, no network — is UNKNOWN and fails open: the tick proceeds and the real
attempt decides, exactly as it did before this check existed. The check can make a tick
stop early; it cannot make a working token look broken.

    GH_TOKEN=… python3 pr_access.py --repo OWNER/NAME
    exit 0: can deliver · 3: cannot (a person must grant it) · 4: could not tell

The one-line verdict goes to stdout (the tick puts it in `runs/ticks.jsonl`, where the
health check reads it); the remedy goes to stderr. Stdlib only.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid

__all__ = ["check", "classify", "remedy", "EXIT_OK", "EXIT_DENIED", "EXIT_UNKNOWN"]

EXIT_OK, EXIT_DENIED, EXIT_UNKNOWN = 0, 3, 4

DEFAULT_API = "https://api.github.com"

#: what a person does about each kind of refusal — the fix differs, so the advice does
_PERMISSIONS = ("Contents: Read and write, Pull requests: Read and write (Issues: Read and "
                "write keeps the issues' status labels in step)")
_WHERE = ("GitHub → Settings → Developer settings → Personal access tokens → "
          "Fine-grained tokens → the token")
_SECRET = ("If you issue a new token rather than change this one, store its value in the "
           "secret; if the organization approves fine-grained tokens, approve the request.")
REMEDY = {
    403: f"grant it on the token ({_WHERE}) → Repository permissions: {_PERMISSIONS}. {_SECRET}",
    404: (f"add the repository to the token's Repository access ({_WHERE}), with "
          f"{_PERMISSIONS}. {_SECRET}"),
    401: ("the token no longer authenticates: regenerate it, or issue a fine-grained token "
          f"on the library with {_PERMISSIONS}, and store the new value in the secret."),
}


def remedy(result: dict) -> str:
    """The advice for a `check` result: one line per distinct kind of refusal."""
    kinds = dict.fromkeys(p["status"] for p in result.get("probes", [])
                          if p["verdict"] == "denied" and p["status"] in REMEDY)
    return " ".join(REMEDY[k] for k in sorted(kinds)) or REMEDY[401]


def _probes(base: str):
    """(permission, API path, body) for each write the delivery makes, in a form GitHub
    must refuse whatever the token holds. A fresh name per call and per probe: no branch
    by that name can exist, and no probe can create what another one then uses."""
    stem = f"foundry-access-probe-{uuid.uuid4().hex[:12]}"
    return (
        # the push: a ref at the zero object, which no repository contains
        ("contents=write", "git/refs", {"ref": f"refs/heads/{stem}-ref", "sha": "0" * 40}),
        # the pull request: from a branch that does not exist
        ("pull_requests=write", "pulls",
         {"title": "foundry access probe", "head": f"{stem}-head", "base": base}),
    )


def _message(text: str) -> str:
    try:
        msg = json.loads(text).get("message")
    except (ValueError, AttributeError):
        msg = None
    return str(msg or text or "").strip().replace("\n", " ")[:160]


def classify(status: int | None, headers: dict, text: str) -> str:
    """'ok' | 'denied' | 'unknown' for one probe response.

    A 403 is a refusal only when GitHub's message says it is about the token's grant. A
    rate limit is a 403 too, and reading one as a missing permission would stop a tick
    for nothing. `X-Accepted-GitHub-Permissions` is no evidence either way: it names the
    permission an endpoint wants, on answers that are not refusals as well, so it is used
    to name what is missing, never to decide that something is."""
    if status is None:
        return "unknown"
    if 200 <= status < 300 or status == 422:
        return "ok"
    if status in (401, 404):
        return "denied"
    if status == 403:
        msg = _message(text).lower()
        if "rate limit" not in msg and ("not accessible by" in msg or "saml" in msg):
            return "denied"
    return "unknown"


def _why(status: int | None, permission: str, text: str, slug: str) -> str:
    msg = _message(text)
    if status == 401:
        return f"the token is invalid, expired or revoked (HTTP 401: {msg})"
    if status == 404:
        return f"the token cannot see {slug} (HTTP 404)"
    if status == 403:
        return f"{permission} not granted ({msg})"
    return f"{permission}: HTTP {status}: {msg}" if status else f"{permission}: {msg}"


def _http(method: str, url: str, headers: dict, body: bytes, *, timeout: float = 30):
    """(status, lower-cased headers, text). Never raises: status is None when nothing
    came back, because a check that cannot answer must not fail the tick."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (resp.status, {k.lower(): v for k, v in resp.headers.items()},
                    resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            text = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, text
    except Exception as e:  # noqa: BLE001 — URLError, a timeout, TLS: all "could not tell"
        return None, {}, f"{type(e).__name__}: {e}"


def check(slug: str, token: str, *, base: str = "main", api: str | None = None,
          http_fn=None) -> dict:
    """Whether `token` can push a branch to `slug` and open a pull request from it.

    Returns {verdict: ok|denied|unknown, missing: [...], reason: str, probes: [...]}.
    `missing` names the permissions GitHub reported absent; `reason` is one line."""
    if not token:
        return {"verdict": "denied", "missing": [], "probes": [],
                "reason": f"cannot deliver to {slug}: no token to check"}
    api = (api or os.environ.get("GITHUB_API_URL") or DEFAULT_API).rstrip("/")
    http = http_fn or _http
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "Content-Type": "application/json", "User-Agent": "formal-foundry-pr-access"}
    probes = []
    for permission, path, body in _probes(base):
        status, hdrs, text = http("POST", f"{api}/repos/{slug}/{path}", headers,
                                  json.dumps(body).encode("utf-8"))
        hdrs = {k.lower(): v for k, v in (hdrs or {}).items()}
        named = hdrs.get("x-accepted-github-permissions") or permission
        probes.append({"permission": named, "status": status,
                       "verdict": classify(status, hdrs, text),
                       "why": _why(status, named, text, slug)})

    def unique(rows):
        return "; ".join(dict.fromkeys(r["why"] for r in rows))

    denied = [p for p in probes if p["verdict"] == "denied"]
    unknown = [p for p in probes if p["verdict"] == "unknown"]
    if denied:
        verdict, reason = "denied", f"cannot deliver to {slug}: {unique(denied)}"
    elif unknown:
        verdict = "unknown"
        reason = (f"could not confirm its access to {slug} ({unique(unknown)}); "
                  "proceeding, the real attempt decides")
    else:
        verdict, reason = "ok", f"can push to and open pull requests on {slug}"
    return {"verdict": verdict, "reason": reason, "probes": probes,
            "missing": [p["permission"] for p in denied if p["status"] == 403]}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="pr_access", description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="owner/name of the target library")
    ap.add_argument("--base", default="main", help="the branch a pull request would target")
    args = ap.parse_args(argv)

    result = check(args.repo, os.environ.get("GH_TOKEN", ""), base=args.base)
    print(result["reason"])
    if result["verdict"] == "denied":
        print(f"[pr-access] {remedy(result)}", file=sys.stderr)
        return EXIT_DENIED
    return EXIT_OK if result["verdict"] == "ok" else EXIT_UNKNOWN


if __name__ == "__main__":
    raise SystemExit(main())
