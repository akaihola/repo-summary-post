#!/usr/bin/env bash

VENV=$HOME/.virtualenvs/repo-summary-post
PIP=${VENV}/bin/pip
[ ! -f ${PIP} ] && python -m venv ${VENV} && ${PIP} install -U pip
${PIP} install -q -e '.[test]'

ensure() { command -v $1 >/dev/null || ${PIP} install -q $1; }

ensure pytest

errors=0
export INPUT_GITHUB_TOKEN=$(secret-tool lookup service gh:github.com)
export INPUT_REPO_NAME=akaihola/darker
( ${VENV}/bin/summarize-repo-activity || errors=$? ) | head -30
${VENV}/bin/pytest --quiet src/tests || errors=$?

exit $errors
