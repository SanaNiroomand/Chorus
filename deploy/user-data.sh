#!/bin/bash
# Chorus bot - EC2 bootstrap (Amazon Linux 2023, arm64).
# Runs once at first boot as root. Log: /var/log/chorus-bootstrap.log
set -euxo pipefail
exec > >(tee /var/log/chorus-bootstrap.log) 2>&1

REGION="eu-central-1"
APP_DIR="/opt/chorus"
REPO="git@github.com:SanaNiroomand/Chorus.git"   # private repo - needs a deploy key

dnf -y update
dnf -y install git python3.11 python3.11-pip

# Unprivileged service account - the bot never needs root.
id -u chorus &>/dev/null || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin chorus

# --- code ---------------------------------------------------------------
# The repo is PRIVATE, so this clone needs credentials. See DEPLOY.md step 4:
# a read-only GitHub deploy key is written to /root/.ssh/id_ed25519 before this runs.
install -d -m 0700 /root/.ssh
ssh-keyscan -t ed25519 github.com >> /root/.ssh/known_hosts 2>/dev/null
GIT_SSH_COMMAND="ssh -i /root/.ssh/id_ed25519 -o IdentitiesOnly=yes" \
  git clone --depth 1 "$REPO" "$APP_DIR"

python3.11 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements-bot.txt"
chown -R chorus:chorus "$APP_DIR"

# --- secrets ------------------------------------------------------------
# INTENTIONALLY LEFT UNIMPLEMENTED.
#
# The project's CLAUDE.md forbids calling `secretsmanager get-secret-value`
# directly and requires the `aws-secrets-manager` skill's pattern instead
# ({{resolve:secretsmanager:...}} via asm-exec). That skill was not loadable
# in the session that wrote this file, so the mechanism is deliberately not
# guessed at here.
#
# In the new session: load the aws-secrets-manager skill, then generate
# /etc/chorus/env (root-owned, 0600) containing:
#     OPENAI_API_KEY=...
#     OPENAI_MODEL=gpt-5.5
#     TELEGRAM_TOKEN=...
install -d -m 0700 /etc/chorus
# <-- secrets step goes here -->

# --- service ------------------------------------------------------------
install -m 0644 "$APP_DIR/deploy/chorus-bot.service" /etc/systemd/system/chorus-bot.service
systemctl daemon-reload
systemctl enable --now chorus-bot.service
systemctl --no-pager status chorus-bot.service || true
