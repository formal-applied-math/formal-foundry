"""The shell scripts are part of the program. Test them like it.

On 2026-08-16 a refactor gave `house_context.build_system_prompt` a `pack` parameter and
updated every Python caller and every test. Two callers lived inside shell strings
(`python3 -c "...build_system_prompt('$MAIN')"` in both prover launchers) and a third in
open-pr.sh's heredoc; nothing executed or even parsed them. From the next tick on, the
launcher raised a TypeError before the prover started, the harness read back the untouched
stub, and five weeks of ticks were scored as failed proofs.

Two defences, both cheap:
  1. every Python call embedded in `scripts/*.sh` (heredocs and `python3 -c`) is resolved
     through the snippet's OWN imports and bound against the current signature;
  2. both launchers are executed end to end with fake `docker` / `claude` / `vibe`
     binaries on PATH, so the doctrine they build and the argv they hand the model are
     real outputs of the real scripts.
"""
from __future__ import annotations

import ast
import glob
import importlib
import inspect
import json
import os
import re
import stat
import subprocess
import sys

import pytest

PROBE = os.path.dirname(os.path.abspath(__file__))
FOUNDRY = os.path.dirname(PROBE)
SCRIPTS = sorted(glob.glob(os.path.join(FOUNDRY, "scripts", "*.sh")))
PROBE_MODULES = {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(PROBE, "*.py"))
                 if not os.path.basename(p).startswith("test_")}

_HEREDOC = re.compile(r"<<\s*'?(\w+)'?[^\n]*\n(.*?)\n\1\s*\n", re.S)
_DASH_C = re.compile(r"python3\s+-c\s+(\"|')(.*?)(?<!\\)\1", re.S)


def embedded_snippets(path: str):
    """(label, python source) for every heredoc fed to python and every `python3 -c`."""
    src = open(path, encoding="utf-8").read()
    rel = os.path.relpath(path, FOUNDRY)
    for m in _HEREDOC.finditer(src):
        head = src[max(0, src.rfind("\n", 0, m.start())):m.start()]
        if "python" not in head:
            continue          # a heredoc feeding cat/a config file, not Python
        yield f"{rel}:{src.count(chr(10), 0, m.start()) + 1}", m.group(2)
    for m in _DASH_C.finditer(src):
        code = m.group(2).replace('\\"', '"')
        yield f"{rel}:{src.count(chr(10), 0, m.start()) + 1}", code


def _resolve(node, names: dict):
    """The callable a call's `func` names, via the snippet's imports; None if unknown."""
    chain = []
    while isinstance(node, ast.Attribute):
        chain.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name) or node.id not in names:
        return None
    obj = names[node.id]
    for attr in reversed(chain):
        obj = getattr(obj, attr, None)
        if obj is None:
            return None
    return obj if callable(obj) else None


def bad_calls(source: str) -> list[str]:
    tree = ast.parse(source)
    names: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in PROBE_MODULES:
            mod = importlib.import_module(node.module)
            for alias in node.names:
                if hasattr(mod, alias.name):
                    names[alias.asname or alias.name] = getattr(mod, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in PROBE_MODULES:
                    names[alias.asname or alias.name] = importlib.import_module(alias.name)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = _resolve(node.func, names)
        if fn is None or inspect.isclass(fn):
            continue
        if any(isinstance(a, ast.Starred) for a in node.args) or \
                any(k.arg is None for k in node.keywords):
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        try:
            sig.bind(*[object()] * len(node.args), **{k.arg: object() for k in node.keywords})
        except TypeError as e:
            out.append(f"line {node.lineno}: {ast.unparse(node.func)}{sig}: {e}")
    return out


def test_every_python_call_embedded_in_a_script_matches_the_current_signature():
    failures = []
    for path in SCRIPTS:
        for label, code in embedded_snippets(path):
            try:
                failures += [f"{label}: {f}" for f in bad_calls(code)]
            except SyntaxError as e:
                failures.append(f"{label}: embedded Python does not parse: {e}")
    assert not failures, "\n".join(failures)


def test_the_scan_catches_the_call_that_broke_the_prover():
    """The exact snippet that shipped in both launchers from 2026-08-16."""
    broken = ("import sys; sys.path.insert(0, 'X'); from house_context import "
              "build_system_prompt; print(build_system_prompt('$MAIN'))")
    assert bad_calls(broken) and "pack" in bad_calls(broken)[0]
    assert bad_calls("import pipeline_lib as p; print(p.ProverConfig.load('x').engine)") == []


def test_the_scan_sees_heredocs_and_dash_c():
    labels = [label for path in SCRIPTS for label, _ in embedded_snippets(path)]
    assert any(label.startswith("scripts/open-pr.sh") for label in labels)
    assert any(label.startswith("scripts/pipeline-tick.sh") for label in labels)


# --- the launchers, executed ---------------------------------------------------------

def _exe(path, body):
    with open(path, "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env bash\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


@pytest.fixture
def sandbox(tmp_path):
    """A fake target repo and a PATH whose docker/claude/vibe record what they were given."""
    main = tmp_path / "main"
    (main / "docs").mkdir(parents=True)
    (main / "docker").mkdir()
    (main / "MathFin").mkdir()
    (main / "lean-toolchain").write_text("leanprover/lean4:v4.33.0-rc1\n")
    (main / "lake-manifest.json").write_text(json.dumps({"packages": [
        {"name": "mathlib", "rev": "81a5d257deadbeef"}]}))
    (main / "docs" / "patterns.md").write_text("# Patterns\n\n## Tactic ladder\n\nTry grind.\n")
    log = tmp_path / "log"
    log.mkdir()
    fake = tmp_path / "bin"
    fake.mkdir()
    _exe(fake / "docker", 'case " $* " in *" logs "*) echo LEAN_LSP_MCP_READY ;; esac\nexit 0\n')
    _exe(fake / "claude", f'''
if [ "$1" = "--help" ]; then echo "  --append-system-prompt-file <file>"; exit 0; fi
printf '%s\\n' "$@" > "{log}/claude.argv"
prev=""
for a in "$@"; do
  [ "$prev" = "--append-system-prompt-file" ] && cp "$a" "{log}/doctrine.txt"
  prev="$a"
done
echo '{{"type":"result","subtype":"success","is_error":false,"num_turns":1}}'
''')
    _exe(fake / "vibe", f'printf "%s\\n" "$@" > "{log}/vibe.argv"\n')
    env = {"PATH": f"{fake}:{os.path.dirname(sys.executable)}:/usr/bin:/bin",
           "MAIN_REPO": str(main), "HOME": str(tmp_path), "LANG": "C.UTF-8"}
    return {"main": main, "log": log, "env": env}


def _launch(script, sandbox, *extra_env):
    env = dict(sandbox["env"], **dict(extra_env))
    return subprocess.run([os.path.join(FOUNDRY, "scripts", script), "--agent", "lean",
                           "--auto-approve", "--max-turns", "7", "-p", "TASK: prove it"],
                          env=env, capture_output=True, text=True, timeout=120)


def test_claude_prove_hands_claude_the_doctrine_the_tools_and_a_transcript(sandbox):
    r = _launch("claude-prove.sh", sandbox)
    assert r.returncode == 0, r.stderr
    argv = (sandbox["log"] / "claude.argv").read_text().splitlines()
    assert argv[argv.index("-p") + 1] == "TASK: prove it"
    assert "mcp__lean-lsp" in argv[argv.index("--allowedTools") + 1]    # else: no Lean at all
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
    assert argv[argv.index("--max-turns") + 1] == "7"
    doctrine = (sandbox["log"] / "doctrine.txt").read_text()
    assert "── PINS" in doctrine and "Try grind." in doctrine   # built from the live repo
    assert '"type":"result"' in r.stdout                        # the transcript reaches stdout


def test_leanstral_vibe_prepends_the_doctrine_to_the_task(sandbox):
    r = _launch("leanstral-vibe.sh", sandbox, ("MISTRAL_API_KEY", "x"))
    assert r.returncode == 0, r.stderr
    argv = (sandbox["log"] / "vibe.argv").read_text()
    assert "── PINS" in argv and "TASK: prove it" in argv


def test_every_prover_launcher_is_executable():
    """`vibe_prove.py` runs the launcher as argv[0]. `claude-prove.sh` was committed
    100644, so the Claude arm could not have started even had anything called it."""
    from pipeline_lib import PROVER_LAUNCHERS
    for launcher in PROVER_LAUNCHERS.values():
        path = os.path.join(FOUNDRY, "scripts", launcher)
        assert os.access(path, os.X_OK), f"{launcher} is not executable (chmod +x)"
