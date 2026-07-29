#!/bin/bash
# Chorus bot - DigitalOcean droplet setup (Ubuntu 22.04/24.04).
#
# Run as root on a fresh droplet:
#   bash digitalocean-setup.sh
#
# Run it twice: the first pass prints a deploy key for you to add to GitHub,
# the second pass clones and starts the bot.
set -euo pipefail

APP=/opt/chorus
REPO=git@github.com:SanaNiroomand/Chorus.git

echo "=== packages ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git

id -u chorus &>/dev/null || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin chorus

# --- deploy key: generated here so the private half never leaves this box ---
if [ ! -f /root/.ssh/id_ed25519 ]; then
  install -d -m 0700 /root/.ssh
  ssh-keygen -t ed25519 -N "" -C "chorus-digitalocean" -f /root/.ssh/id_ed25519 -q
  ssh-keyscan -t ed25519 github.com >> /root/.ssh/known_hosts 2>/dev/null
  echo
  echo "=============================================================="
  echo " Add this as a DEPLOY KEY on the repo, then re-run this script"
  echo " GitHub > repo > Settings > Deploy keys > Add deploy key"
  echo " (leave 'Allow write access' UNCHECKED)"
  echo "=============================================================="
  cat /root/.ssh/id_ed25519.pub
  echo "=============================================================="
  exit 0
fi

echo "=== code ==="
if [ -d "$APP/.git" ]; then
  git -C "$APP" -c safe.directory="$APP" pull --ff-only
else
  rm -rf "$APP"
  GIT_SSH_COMMAND="ssh -i /root/.ssh/id_ed25519 -o IdentitiesOnly=yes" git clone --depth 1 "$REPO" "$APP"
fi

python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --quiet --upgrade pip
"$APP/.venv/bin/pip" install --quiet -r "$APP/requirements-bot.txt"
chown -R chorus:chorus "$APP"

# --- secrets: a root-only env file. Created by you, never in git. ---
install -d -m 0700 /etc/chorus
if [ ! -f /etc/chorus/env ]; then
  cat > /etc/chorus/env <<'EOS'
OPENAI_API_KEY=PUT_YOUR_KEY_HERE
OPENAI_MODEL=gpt-5.5
TELEGRAM_TOKEN=PUT_YOUR_TOKEN_HERE
EOS
  chmod 0600 /etc/chorus/env
  echo
  echo "!! Edit /etc/chorus/env and put your real keys in, then re-run this script."
  echo "   nano /etc/chorus/env"
  exit 0
fi
if grep -q PUT_YOUR /etc/chorus/env; then
  echo "!! /etc/chorus/env still has placeholders. Edit it, then re-run."
  exit 1
fi

echo "=== service ==="
install -m 0644 "$APP/deploy/chorus-bot.service" /etc/systemd/system/chorus-bot.service
systemctl daemon-reload
systemctl enable chorus-bot
systemctl restart chorus-bot
sleep 5
systemctl is-active chorus-bot
echo
echo "Done. Follow the log with:  journalctl -u chorus-bot -f"
