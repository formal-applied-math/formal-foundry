"""The token check, and the tick standing down on its answer.

From 2026-09-28 to 2026-10-07 every tick proved cal-bk-129 and pushed its branch, then
`gh pr create` was refused for want of Pull requests: write. open-pr called that
transient, so the planner re-chose the same target forever. These tests pin the three
things the fix rests on: a refusal is recognised and named, a non-answer never stops a
tick, and no probe can create anything on the library whatever the token holds.
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time

import pytest

import pr_access
from pr_access import check, classify, remedy

PROBE = os.path.dirname(os.path.abspath(__file__))
FOUNDRY = os.path.dirname(PROBE)

#: the response GitHub gave on every tick from 2026-09-28 to 2026-10-07
LIVE_REFUSAL = (403, {"X-Accepted-GitHub-Permissions": "pull_requests=write"},
                json.dumps({"message": "Resource not accessible by personal access token",
                            "documentation_url": "https://docs.github.com/rest"}))
VALIDATION = (422, {}, json.dumps({"message": "Validation Failed"}))


class FakeGitHub:
    """An http_fn: answers each probe by the path it writes to, and keeps the requests."""

    def __init__(self, refs=VALIDATION, pulls=VALIDATION):
        self.routes = {"git/refs": refs, "pulls": pulls}
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append({"method": method, "url": url, "headers": headers,
                              "body": json.loads(body)})
        for suffix, answer in self.routes.items():
            if url.endswith("/" + suffix):
                return answer
        return 404, {}, "{}"


# --- the classification ----------------------------------------------------------------

def test_a_422_means_the_token_holds_the_permission():
    assert classify(422, {}, VALIDATION[2]) == "ok"
    assert classify(201, {}, "{}") == "ok"


def test_the_live_refusal_is_a_refusal():
    status, headers, text = LIVE_REFUSAL
    assert classify(status, {k.lower(): v for k, v in headers.items()}, text) == "denied"
    # GitHub Apps get the same refusal worded for an integration, sometimes headerless
    assert classify(403, {}, '{"message": "Resource not accessible by integration"}') == "denied"


def test_a_rate_limit_is_not_a_refusal():
    """A rate limit is a 403 too. Reading it as a missing grant would stop a tick for
    nothing — the one failure this check must never introduce. Not even when it carries
    the accepted-permissions header, which names a permission without refusing one."""
    named = {"x-accepted-github-permissions": "pull_requests=write"}
    for msg in ("API rate limit exceeded for user ID 1.",
                "You have exceeded a secondary rate limit."):
        assert classify(403, {}, json.dumps({"message": msg})) == "unknown"
        assert classify(403, named, json.dumps({"message": msg})) == "unknown"
    assert classify(403, named, "{}") == "unknown"     # the header alone decides nothing


def test_no_answer_is_unknown_never_denied():
    assert classify(None, {}, "URLError: timed out") == "unknown"
    assert classify(500, {}, "{}") == "unknown"
    assert classify(502, {}, "<html>bad gateway</html>") == "unknown"


def test_bad_credentials_and_an_invisible_repo_are_refusals():
    assert classify(401, {}, '{"message": "Bad credentials"}') == "denied"
    assert classify(404, {}, '{"message": "Not Found"}') == "denied"


# --- the check -------------------------------------------------------------------------

def test_the_outage_token_is_denied_and_the_missing_permission_named():
    gh = FakeGitHub(refs=VALIDATION, pulls=LIVE_REFUSAL)
    r = check("org/library", "github_pat_x", http_fn=gh)
    assert r["verdict"] == "denied"
    assert r["missing"] == ["pull_requests=write"]
    assert "pull_requests=write not granted" in r["reason"]
    assert "Resource not accessible by personal access token" in r["reason"]
    advice = remedy(r)
    assert "Pull requests: Read and write" in advice and "Repository permissions" in advice


def test_a_token_missing_both_grants_names_both():
    refused = (403, {}, '{"message": "Resource not accessible by personal access token"}')
    r = check("org/library", "t", http_fn=FakeGitHub(refs=refused, pulls=refused))
    assert r["verdict"] == "denied"
    assert r["missing"] == ["contents=write", "pull_requests=write"]


def test_a_token_holding_both_grants_is_ok():
    r = check("org/library", "t", http_fn=FakeGitHub())
    assert r["verdict"] == "ok" and r["missing"] == []


def test_a_revoked_token_is_told_to_regenerate_not_to_edit():
    bad = (401, {}, '{"message": "Bad credentials"}')
    r = check("org/library", "t", http_fn=FakeGitHub(refs=bad, pulls=bad))
    assert r["verdict"] == "denied"
    assert r["reason"].count("invalid, expired or revoked") == 1      # said once, not per probe
    assert "regenerate" in remedy(r) and "Repository permissions" not in remedy(r)


def test_one_unanswered_probe_makes_the_whole_check_unknown():
    r = check("org/library", "t", http_fn=FakeGitHub(pulls=(None, {}, "URLError: down")))
    assert r["verdict"] == "unknown"
    assert "the real attempt decides" in r["reason"]


def test_a_refusal_outranks_a_non_answer():
    r = check("org/library", "t", http_fn=FakeGitHub(refs=(None, {}, "down"),
                                                     pulls=LIVE_REFUSAL))
    assert r["verdict"] == "denied"


def test_no_token_is_denied_without_asking():
    gh = FakeGitHub()
    r = check("org/library", "", http_fn=gh)
    assert r["verdict"] == "denied" and gh.requests == []


def test_no_probe_can_create_anything(monkeypatch):
    """Whatever the token holds, GitHub must refuse each probe: a ref at the zero object,
    a pull request from a branch that does not exist — fresh names on every call."""
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    gh = FakeGitHub()
    check("org/library", "github_pat_x", http_fn=gh)
    check("org/library", "github_pat_x", http_fn=gh)
    refs = [r["body"] for r in gh.requests if r["url"].endswith("/git/refs")]
    pulls = [r["body"] for r in gh.requests if r["url"].endswith("/pulls")]
    assert len(refs) == len(pulls) == 2
    assert all(b["sha"] == "0" * 40 for b in refs)
    heads = [b["head"] for b in pulls]
    assert len(set(heads)) == 2                                # never reused
    assert all(h.startswith("foundry-access-probe-") for h in heads)
    made = {b["ref"].removeprefix("refs/heads/") for b in refs}
    assert not made & set(heads)          # no probe opens a PR from a ref another made
    for req in gh.requests:
        assert req["method"] == "POST"
        assert req["url"].startswith("https://api.github.com/repos/org/library/")
        assert req["headers"]["Authorization"] == "Bearer github_pat_x"


def test_the_api_base_follows_the_runner(monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example/api/v3/")
    gh = FakeGitHub()
    check("org/library", "t", http_fn=gh)
    assert all(r["url"].startswith("https://ghe.example/api/v3/repos/org/library/")
               for r in gh.requests)


@pytest.mark.parametrize("pulls,code", [(VALIDATION, 0), (LIVE_REFUSAL, 3),
                                        ((None, {}, "down"), 4)])
def test_the_exit_code_carries_the_verdict(monkeypatch, capsys, pulls, code):
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(pr_access, "_http",
                        lambda m, u, h, b: FakeGitHub(pulls=pulls)(m, u, h, b))
    assert pr_access.main(["--repo", "org/library"]) == code
    out = capsys.readouterr()
    assert len(out.out.strip().splitlines()) == 1        # one line, for the tick row
    assert ("Read and write" in out.err) == (code == 3)  # the remedy only on a refusal


# --- open-pr's own reading of a refusal (the second line of defence) --------------------

def _refused_pattern() -> str:
    src = open(os.path.join(FOUNDRY, "scripts", "open-pr.sh"), encoding="utf-8").read()
    line = next(ln for ln in src.splitlines() if ln.startswith("REFUSED='"))
    return line[len("REFUSED='"):-1]


@pytest.mark.parametrize("stderr,refused", [
    # verbatim from the tick logs, 2026-09-28 .. 2026-10-07: the PR, then the push
    ("pull request create failed: GraphQL: Resource not accessible by personal access "
     "token (createPullRequest)", True),
    ("remote: Permission to org/library.git denied to someone.\nfatal: unable to access "
     "'https://github.com/org/library/': The requested URL returned error: 403", True),
    ("HTTP 401: Bad credentials (https://api.github.com/graphql)", True),
    # transient: retrying is the right answer to these — a 403 rate limit included
    ("HTTP 403: You have exceeded a secondary rate limit. Please wait a few minutes "
     "before you try again. (https://api.github.com/graphql)", False),
    ("fatal: unable to access 'https://github.com/org/library/': Could not resolve host: "
     "github.com", False),
    ("HTTP 502: Bad Gateway (https://api.github.com/graphql)", False),
    ("error connecting to api.github.com", False),
])
def test_open_pr_tells_a_refusal_from_a_blip(tmp_path, stderr, refused):
    """`grep -qiE "$REFUSED"`, exactly as open-pr.sh runs it: exit 5 on a refusal, so the
    tick names the missing grant instead of `rc=4` six times over."""
    err = tmp_path / "err"
    err.write_text(stderr + "\n")
    hit = subprocess.run(["grep", "-qiE", _refused_pattern(), str(err)]).returncode == 0
    assert hit is refused


# --- the tick, executed ----------------------------------------------------------------

class _Handler(http.server.BaseHTTPRequestHandler):
    answers: dict = {}
    seen: list = []

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).seen.append(self.path)
        code, headers, text = next((a for s, a in self.answers.items()
                                    if self.path.endswith("/" + s)), (404, {}, "{}"))
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))

    def log_message(self, *a):
        pass


@pytest.fixture
def github():
    """A local GitHub API the tick's own `pr_access.py` talks to (via GITHUB_API_URL)."""
    handler = type("H", (_Handler,), {"answers": {}, "seen": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield handler, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _exe(path, body):
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def foundry(tmp_path):
    """A copy of the foundry (its runs/ must not be the real one), a target library whose
    python gates pass, a `gh` that records being called and fails, and a state whose last
    tick was just now — so a tick that gets past the token check stops at `not_due`
    instead of reaching for a daemon."""
    root = tmp_path / "foundry"
    skip = shutil.ignore_patterns("__pycache__", ".pytest_cache")
    for d in ("probe", "scripts", "domains", "targets"):
        shutil.copytree(os.path.join(FOUNDRY, d), root / d, ignore=skip)
    shutil.copy(os.path.join(FOUNDRY, "pipeline.toml"), root / "pipeline.toml")
    (root / "runs").mkdir()
    state = json.load(open(os.path.join(FOUNDRY, "pipeline_state.json")))
    state["last_tick_epoch"] = int(time.time())
    (root / "pipeline_state.json").write_text(json.dumps(state))
    main = tmp_path / "library"
    (main / "tests").mkdir(parents=True)
    (main / "tests" / "test_gates.py").write_text("def test_green():\n    pass\n")
    fake = tmp_path / "bin"
    fake.mkdir()
    _exe(fake / "gh", f'echo "$*" >> "{tmp_path}/gh.calls"\nexit 1\n')
    _exe(fake / "docker", "exit 0\n")
    env = {"PATH": f"{fake}:{os.path.dirname(sys.executable)}:/usr/bin:/bin",
           "HOME": str(tmp_path), "LANG": "C.UTF-8", "MAIN_REPO": str(main),
           "MAIN_PR_TOKEN": "github_pat_" + "x" * 40, "GH_GROUND_TRUTH": "0"}
    return {"root": root, "env": env, "gh_calls": tmp_path / "gh.calls"}


def _tick(foundry, api):
    env = dict(foundry["env"], GITHUB_API_URL=api)
    r = subprocess.run(["bash", str(foundry["root"] / "scripts" / "pipeline-tick.sh")],
                       env=env, capture_output=True, text=True, timeout=180)
    rows = (foundry["root"] / "runs" / "ticks.jsonl").read_text().splitlines()
    return r, json.loads(rows[-1])


def test_the_tick_stands_down_red_on_the_outage_token(foundry, github):
    handler, api = github
    handler.answers.update({"git/refs": VALIDATION, "pulls": LIVE_REFUSAL})
    before = (foundry["root"] / "pipeline_state.json").read_text()
    r, row = _tick(foundry, api)
    assert r.returncode == 1, r.stderr
    assert row["action"] == "skip" and row["reason"] == "pr_token_denied"
    assert row["infra_failure"] is True
    assert "pull_requests=write not granted" in row["infra_reason"]   # the health line says why
    assert "::error::" in r.stderr and "Pull requests: Read and write" in r.stderr
    # it stopped before anything else: no reconcile, no plan, nothing recorded
    assert not foundry["gh_calls"].exists()
    assert "[tick] skip: not_due" not in r.stderr
    assert (foundry["root"] / "pipeline_state.json").read_text() == before


@pytest.mark.parametrize("answer", [VALIDATION, (500, {}, "{}")], ids=["ok", "unknown"])
def test_the_tick_proceeds_when_the_token_is_fine_or_the_check_cannot_tell(
        foundry, github, answer):
    handler, api = github
    handler.answers.update({"git/refs": answer, "pulls": answer})
    r, row = _tick(foundry, api)
    assert r.returncode == 0, r.stderr
    assert row["action"] == "skip" and row["reason"] == "not_due"   # it reached the plan
    assert row["infra_failure"] is False
    assert foundry["gh_calls"].exists()                              # and the reconcile
    assert len(handler.seen) == 2                                    # one call per probe
