#!/usr/bin/env bash

VENV=$HOME/.virtualenvs/repo-summary-post
PIP=${VENV}/bin/pip
[ ! -f ${PIP} ] && python -m venv ${VENV} && ${PIP} install -U pip
if [ requirements.in -nt requirements.txt ]; then
    ${VENV}/bin/pip-compile requirements.in
    # note: also need to run the below pip install
    # when console_scripts are added to pyproject.toml:
    ${PIP} install -q -e '.[test]'
fi

ensure() { command -v $1 >/dev/null || ${PIP} install -q $1; }

ensure pytest

errors=0
export INPUT_GITHUB_TOKEN=$(secret-tool lookup service gh:github.com)
export INPUT_REPO_NAME=akaihola/darker
${VENV}/bin/summarize-repo-activity \
  --cache \
  --model=openrouter/google/gemini-flash-1.5 \
  --output=tmp/summary.md \
  --output-content=tmp/content.md \
  --output-prompt=tmp/prompt.md \
  --category=Announcements \
  --dry-run \
  -vv \
|| errors=$?
${VENV}/bin/pytest --quiet src/tests || errors=$?

exit $errors
