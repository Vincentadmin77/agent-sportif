#!/bin/bash
# Run by agent-update.timer: takes the branch's new commits and restarts the agent when the code changed.
set -euo pipefail
APP=/srv/agent-sportif; BRANCH=${BRANCH:-claude/project-thread-mnjit9}
cd $APP/code
G="sudo -u agent git"
before=$($G rev-parse HEAD)
$G fetch -q origin "$BRANCH"
after=$($G rev-parse "origin/$BRANCH")
[ "$before" = "$after" ] && exit 0
$G reset -q --hard "$after"
if ! $G diff --quiet "$before" "$after" -- backend/pyproject.toml; then
  sudo -u agent $APP/venv/bin/pip install -q -e "$APP/code/backend"
fi
cp $APP/code/deploy/systemd/* /etc/systemd/system/ && systemctl daemon-reload
if ! systemctl restart agent-runner.service; then   # a broken version: back to the one that worked
  $G reset -q --hard "$before"; systemctl restart agent-runner.service
  echo "nouvelle version $after refusée, retour à $before"; exit 1
fi
echo "mis à jour : $before -> $after"
