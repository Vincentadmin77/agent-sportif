#!/bin/bash
# Installation d'Analytrack autonome sur un serveur Debian 12 / Ubuntu 24.04 neuf (VPS IONOS).
# À lancer une fois, en root :  bash installer.sh
# Ne publie rien : le site n'est visible qu'une fois le nom de domaine pointé vers le serveur (accord de Vincent).
set -euo pipefail
A=/srv/analytrack
apt-get update -q && apt-get install -yq python3 python3-venv python3-pip git nginx certbot python3-certbot-nginx rsync sqlite3 ufw
id analytrack >/dev/null 2>&1 || useradd --system --create-home --home-dir $A --shell /bin/bash analytrack
mkdir -p $A/{base,site,travail,fichiers,sauvegardes}
# Code : dépôt privé GitHub (clé de déploiement en lecture seule dans ~analytrack/.ssh)
[ -d $A/agent-sportif ] || sudo -u analytrack git clone -b claude/project-thread-ky3kjz git@github.com:Vincentadmin77/agent-sportif.git $A/agent-sportif
python3 -m venv $A/venv && $A/venv/bin/pip install -q -e "$A/agent-sportif/backend[dev]"
# Les scripts gardent leurs chemins d'origine : /mnt/project-files/agent-sportif -> dossier des fichiers
mkdir -p /mnt/project-files && ln -sfn $A/fichiers /mnt/project-files/agent-sportif
# Clés des API : jamais dans le dépôt. Fichier rempli à la main (modèle : clés.exemple), lisible par analytrack seul
[ -f $A/cles.env ] || { cp $A/agent-sportif/deploy/autonome/cles.exemple $A/cles.env; echo "REMPLIR $A/cles.env"; }
chmod 600 $A/cles.env
chown -R analytrack:analytrack $A
cp $A/agent-sportif/deploy/autonome/systemd/*.service $A/agent-sportif/deploy/autonome/systemd/*.timer /etc/systemd/system/
cp $A/agent-sportif/deploy/autonome/nginx-analytrack.conf /etc/nginx/sites-available/analytrack
ln -sfn /etc/nginx/sites-available/analytrack /etc/nginx/sites-enabled/analytrack && rm -f /etc/nginx/sites-enabled/default
ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw --force enable
systemctl daemon-reload && nginx -t && systemctl reload nginx
systemctl enable analytrack.service analytrack-sauvegarde.timer analytrack-mise-a-jour.timer
echo "Installé. Étapes suivantes (docs/30-autonomie.md) : cles.env, import de la base, site, puis systemctl start analytrack"
