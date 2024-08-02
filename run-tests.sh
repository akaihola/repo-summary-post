#!/usr/bin/env bash

VENV=$HOME/.virtualenvs/repo-summary-post
PIP=${VENV}/bin/pip
[ ! -f ${PIP} ] && python -m venv ${VENV} && ${PIP} install -U pip
#${PIP} install -q -e .

ensure() { command -v $1 >/dev/null || ${PIP} install -q $1; }

ensure pytest

errors=0
export INPUT_GITHUB_TOKEN=$(secret-tool lookup service gh:github.com)
export INPUT_REPO_NAME=akaihola/darker
${VENV}/bin/python summarize_prs.py || errors=$?
${VENV}/bin/pytest --quiet || errors=$?

exit $errors
