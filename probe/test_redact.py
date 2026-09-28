"""Credentials never reach an agent that does not need them, or a file that gets published."""
from __future__ import annotations

import os

import redact

GH = "ghp_" + "A" * 36
FINE = "github_pat_" + "B" * 60
ANT = "sk-ant-oat01-" + "c" * 40


def test_an_agent_keeps_only_its_own_credential():
    env = {"PATH": "/bin", "HOME": "/h", "DOMAIN_NAME": "mathfin",
           "MAIN_PR_TOKEN": GH, "GH_TOKEN": GH, "GITHUB_TOKEN": "x" * 20,
           "CLAUDE_CODE_OAUTH_TOKEN": ANT, "ANTHROPIC_API_KEY": "k" * 20,
           "MISTRAL_API_KEY": "m" * 32, "GHCR_TOKEN": "g" * 20, "SOME_SECRET": "s" * 20}
    claude = redact.agent_env("claude", env)
    assert claude["CLAUDE_CODE_OAUTH_TOKEN"] == ANT and "ANTHROPIC_API_KEY" in claude
    for gone in ("MAIN_PR_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "MISTRAL_API_KEY",
                 "GHCR_TOKEN", "SOME_SECRET"):
        assert gone not in claude, gone
    assert claude["PATH"] == "/bin" and claude["DOMAIN_NAME"] == "mathfin"
    vibe = redact.agent_env("leanstral", env)
    assert "MISTRAL_API_KEY" in vibe and "CLAUDE_CODE_OAUTH_TOKEN" not in vibe


def test_findings_name_the_kind_never_the_value():
    text = f"-- {GH}\n-- {FINE}\n-- {ANT}\n-- x-access-token:abcdefghij@github.com"
    kinds = redact.secret_findings(text)
    assert kinds == ["anthropic-key", "github-pat", "github-token", "token-in-url"]
    assert all(GH not in k for k in kinds)
    assert redact.secret_findings("theorem t (hintS2 : P) : P := hintS2") == []
    assert redact.secret_findings("a Mistral key abcd1234abcd1234", ["abcd1234abcd1234"]) == [
        "known-secret-value"]                      # no prefix: caught by value


def test_redact_replaces_values_and_shapes(tmp_path):
    mistral = "M" * 32
    text, n = redact.redact(f"env: MISTRAL_API_KEY={mistral} and {GH}", [mistral])
    assert mistral not in text and GH not in text and n == 2
    assert "[REDACTED:known-secret-value]" in text and "[REDACTED:github-token]" in text
    p = tmp_path / "runs" / "t.transcript.jsonl"
    p.parent.mkdir()
    p.write_text('{"type":"user","content":"' + ANT + '"}\n')
    (tmp_path / "runs" / "clean.jsonl").write_text('{"ok": true}\n')
    changed = redact.redact_paths([str(tmp_path / "runs")], [])
    assert list(changed) == [str(p)] and ANT not in p.read_text()


def test_the_cli_reports_files_and_counts_but_never_values(tmp_path, capsys, monkeypatch):
    (tmp_path / "f.txt").write_text("leaked " + "Z" * 30)
    monkeypatch.setenv("MAIN_PR_TOKEN", "Z" * 30)
    assert redact.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 file(s) redacted" in out and "Z" * 30 not in out
    assert "Z" * 30 not in (tmp_path / "f.txt").read_text()
