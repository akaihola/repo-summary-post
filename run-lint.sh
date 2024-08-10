#!/usr/bin/env bash

VENV=$HOME/.virtualenvs/repo-summary-post
PIP=${VENV}/bin/pip
[ ! -f ${PIP} ] && python -m venv ${VENV} && ${PIP} install -U pip
${PIP} uninstall -q -y ruff 2>/dev/null  # must run from NixOS installed ruff

ensure() { command -v $1 >/dev/null || ${PIP} install -q $1; }

ensure black
ensure codespell
ensure darker
ensure graylint
ensure isort
ensure mypy
ensure yamllint

errors=0

source ${VENV}/bin/activate
darker --quiet --isort --revision=origin/main .
graylint --quiet --revision origin/main \
  --lint "mypy" \
  --lint "ruff check" \
  --lint "codespell" \
  src || errors=$?
graylint --quiet --revision origin/main \
  --lint "codespell" \
  .github *.yml *.toml *.sh || errors=$?
for file in "$@"; do
    case "$file" in
        *.yml|*.yaml)
            yamllint "$file" || errors=$?
            ;;
        *.sh|*.md|*.rst|*.txt)
            codespell "$file" || errors=$?
            ;;
    esac
done
deactivate

exit $errors
