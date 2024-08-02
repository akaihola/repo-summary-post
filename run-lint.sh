#!/usr/bin/env bash

VENV=$HOME/.virtualenvs/repo-summary-post
PIP=${VENV}/bin/pip
[ ! -f ${PIP} ] && python -m venv ${VENV} && ${PIP} install -U pip
#${PIP} install -q -e .
${PIP} uninstall -q -y ruff  # must run from NixOS installed ruff

ensure() { command -v $1 >/dev/null || ${PIP} install -q $1; }

ensure black
ensure codespell
ensure darker
ensure graylint
ensure mypy
ensure yamllint

errors=0

source ${VENV}/bin/activate
graylint --quiet --revision origin/main --lint "mypy" || errors=$?
for file in "$@"; do
    case "$file" in
        *.py)
            darker --quiet --isort --revision=origin/main "$file" || errors=$?
            graylint --quiet --revision=origin/main \
              --lint "ruff check" \
              --lint "codespell" \
              "$file" || errors=$?
            ;;
        *.yml|*.yaml)
            yamllint "$file" || errors=$?
        *.sh|*.md|*.rst|*.txt)
            codespell "$file" || errors=$?
            ;;
    esac
done
deactivate

exit $errors
