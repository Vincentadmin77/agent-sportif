#!/bin/bash
# Run by agent-backup.timer every night: the page, the analysis database and the models, 14 days kept.
set -euo pipefail
APP=/srv/agent-sportif; DEST=/var/backups/agent-sportif
mkdir -p $DEST && chmod 700 $DEST
tar -czf "$DEST/agent-$(date +%F).tar.gz" -C $APP page runner.json files/agent-sportif/state 2>/dev/null || true
find $DEST -name 'agent-*.tar.gz' -mtime +14 -delete
