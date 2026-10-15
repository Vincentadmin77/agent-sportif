#!/bin/bash
# Installs the agent on a fresh Ubuntu 24.04 server (IONOS VPS M). Run once, as root:
#   curl/scp this folder to the server, then: bash install.sh
# Nothing is published: the firewall only lets SSH in. The website (step 4) is added later.
set -euo pipefail
BRANCH=${BRANCH:-claude/project-thread-mnjit9}   # the branch the Claude routines run today
REPO=${REPO:-git@github.com:Vincentadmin77/agent-sportif.git}
APP=/srv/agent-sportif

echo "== Mises à jour et outils"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q && apt-get upgrade -yq
apt-get install -yq git python3-venv python3-pip ufw fail2ban unattended-upgrades sqlite3
dpkg-reconfigure -f noninteractive unattended-upgrades   # security updates every night

echo "== Pare-feu : seul SSH entre"
ufw default deny incoming && ufw default allow outgoing && ufw allow OpenSSH && ufw --force enable
systemctl enable --now fail2ban    # bans addresses that try passwords on SSH

echo "== Utilisateur dédié, sans droits d'administration"
id agent >/dev/null 2>&1 || useradd --system --create-home --home-dir $APP --shell /bin/bash agent
mkdir -p $APP/page $APP/files /mnt
# The code reads its data under /mnt/project-files/agent-sportif (the Claude project folder): same path here.
[ -e /mnt/project-files ] || ln -s $APP/files /mnt/project-files
mkdir -p $APP/files/agent-sportif/state $APP/files/agent-sportif/data
chown -R agent:agent $APP

echo "== Clé de lecture du dépôt GitHub (privé)"
sudo -u agent bash -c "[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -q -t ed25519 -N '' -f ~/.ssh/id_ed25519 -C agent-sportif-serveur"
sudo -u agent bash -c "ssh-keyscan -q github.com >> ~/.ssh/known_hosts 2>/dev/null"
if ! sudo -u agent git ls-remote "$REPO" >/dev/null 2>&1; then
  echo
  echo "Ajoute cette clé dans GitHub : dépôt agent-sportif > Settings > Deploy keys > Add deploy key"
  echo "(lecture seule, ne coche pas « Allow write access »), puis relance ce script :"
  cat $APP/.ssh/id_ed25519.pub
  exit 1
fi

echo "== Code et dépendances"
[ -d $APP/code/.git ] || sudo -u agent git clone -q -b "$BRANCH" "$REPO" $APP/code
sudo -u agent bash -c "cd $APP/code && git fetch -q origin $BRANCH && git checkout -q $BRANCH && git reset -q --hard origin/$BRANCH"
sudo -u agent python3 -m venv $APP/venv
sudo -u agent $APP/venv/bin/pip install -q -e "$APP/code/backend"

echo "== Clés des services (à remplir à la main, jamais dans une conversation)"
if [ ! -f /etc/agent-sportif.env ]; then
  cat > /etc/agent-sportif.env <<ENV
API_FOOTBALL_KEY=
THE_ODDS_API_KEY=
DATA_MODE=real
PAGE_DIR=$APP/page
BRANCH=$BRANCH
ENV
fi
chown root:agent /etc/agent-sportif.env && chmod 640 /etc/agent-sportif.env

echo "== Services : agent, mise à jour automatique, sauvegarde"
cp "$(dirname "$0")"/systemd/* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now agent-update.timer agent-backup.timer
if grep -q '^API_FOOTBALL_KEY=.\+' /etc/agent-sportif.env; then
  systemctl enable --now agent-runner.service
  echo "Agent démarré. Suivi : journalctl -u agent-runner -f"
else
  echo "Remplis les clés : nano /etc/agent-sportif.env puis systemctl enable --now agent-runner"
fi

echo "== SSH : connexion par clé seulement, si une clé est déjà en place"
if [ -s /root/.ssh/authorized_keys ]; then
  printf 'PasswordAuthentication no\nPermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/90-agent.conf
  systemctl reload ssh
else
  echo "(aucune clé SSH pour root : la connexion par mot de passe reste ouverte, à fermer à l'étape suivante)"
fi
echo "Terminé."
