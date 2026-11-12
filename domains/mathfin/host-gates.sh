#!/usr/bin/env bash
# formal-mathfin's host-side gates (stdlib Python + gh, no Lean), run by
# `scripts/open-pr.sh` in the library checkout AFTER the contribution is committed —
# the attribution gate reads who added a file from git, so it only sees a committed one.
#
# Mirrors the "Python gates" and "Blueprint ↔ issues" steps of formal-mathfin's
# `.github/workflows/build.yml`. A non-zero exit blocks the PR.
#
# Env: GH_TOKEN (read access to the library's issues, for the blueprint check).
set -euo pipefail

python3 -m pip show pytest >/dev/null 2>&1 || python3 -m pip install -q pytest
python3 -m pytest tests/ -q
python3 -m tools.verify.ledger status
python3 -m tools.formalization_yaml --check
# `--branch`: label drift is reported, not enforced — labels follow main's tree
python3 -m tools.verify.blueprint_issues check --branch
