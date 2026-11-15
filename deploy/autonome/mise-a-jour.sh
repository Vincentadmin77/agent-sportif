#!/bin/bash
# Nouvelle version du code : seulement la branche validée, et seulement si elle n'ajoute aucun test en échec
# (au 15/11/2026, un test ancien échoue déjà sur la branche de production : test_final_pass_fills_half_time_score).
# Sinon on revient à l'ancienne version et rien ne change.
set -uo pipefail
A=/srv/analytrack; R=$A/agent-sportif; B=claude/project-thread-ky3kjz
echecs() { sudo -u analytrack $A/venv/bin/python -m pytest -q autonome/tests backend/tests -p no:cacheprovider 2>/dev/null \
  | grep -E '^FAILED|^ERROR' | sort; }
cd $R && sudo -u analytrack git fetch -q origin $B || exit 0
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/$B)" ] && exit 0
ANCIEN=$(git rev-parse HEAD)
AVANT=$(echecs)
sudo -u analytrack git reset -q --hard origin/$B
APRES=$(echecs)
NOUVEAUX=$(comm -13 <(echo "$AVANT") <(echo "$APRES") | grep -v '^$')
if [ -n "$NOUVEAUX" ]; then
  sudo -u analytrack git reset -q --hard $ANCIEN
  echo "nouveaux tests en échec, ancienne version gardée : $NOUVEAUX"
  exit 0
fi
sudo -u analytrack $A/venv/bin/pip install -q -e "$R/backend[dev]"
systemctl restart analytrack
echo "mis à jour : $ANCIEN -> $(git rev-parse --short HEAD)"
