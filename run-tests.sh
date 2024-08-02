#!/usr/bin/env bash

VENV=$HOME/.virtualenvs/repo-summary-post
PIP=${VENV}/bin/pip
[ ! -f ${PIP} ] && python -m venv ${VENV} && ${PIP} install -U pip
#${PIP} install -q -e .

ensure() { command -v $1 >/dev/null || ${PIP} install -q $1; }

ensure pytest

errors=0
#${VENV}/bin/darkgray_collect_contributors \
#  --repo akaihola/darkgray-dev-tools \
#  || errors=$?
${VENV}/bin/pytest --quiet || errors=$?

exit $errors
