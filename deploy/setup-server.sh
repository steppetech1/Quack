#!/usr/bin/env bash
# setup-server.sh — first-time setup of a fresh Ubuntu 24.04 server, any provider.
# ТЗ Фазы 4 WP9.7, Д-§10, Д-§4.1, Д-§4.2.
#
# Run once as root on the new server:
#   ADMIN_SSH_PUBKEY='ssh-ed25519 AAAA… admin@host' \
#   DEPLOY_SSH_PUBKEY='ssh-ed25519 AAAA… github-deploy' \
#   bash setup-server.sh
#
# Safe to run again: every step checks its own result first.
#
# Not done here (needs `ops alerts`, WP9.5): the systemd timer of `alerts`. Not done and never stored here: the provider panel firewall (22, 80,
# 443 — set it by hand), DNS (deploy/README.md), secrets (the deploy job renders /srv/quack/.env).
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
: "${ADMIN_SSH_PUBKEY:?set ADMIN_SSH_PUBKEY (public key of the person who administers the server)}"
: "${DEPLOY_SSH_PUBKEY:?set DEPLOY_SSH_PUBKEY (public key whose private half is DEPLOY_SSH_KEY in GitHub)}"

# Starting values (Д-§4.1, Д-§10).
SWAP_SIZE_GB="${SWAP_SIZE_GB:-4}"
JOURNAL_MAX_USE="${JOURNAL_MAX_USE:-1G}"
DOCKER_MIN_VERSION="28.3.3"
SRV=/srv/quack

export DEBIAN_FRONTEND=noninteractive
step() { printf '\n== %s\n' "$*"; }

step "packages"
apt-get update -y
apt-get install -y ca-certificates curl gnupg ufw fail2ban unattended-upgrades

step "users: admin (sudo) and deploy (no sudo)"
if ! id admin >/dev/null 2>&1; then
  # Ubuntu ships a legacy group `admin`; useradd refuses to create a same-named user group.
  if getent group admin >/dev/null; then
    useradd --create-home --shell /bin/bash --gid admin --groups sudo admin
  else
    useradd --create-home --shell /bin/bash --groups sudo admin
  fi
fi
id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
install_key() { # user key
  local home; home="$(getent passwd "$1" | cut -d: -f6)"
  install -d -m 700 -o "$1" -g "$1" "$home/.ssh"
  grep -qxF "$2" "$home/.ssh/authorized_keys" 2>/dev/null || echo "$2" >> "$home/.ssh/authorized_keys"
  chown "$1:$1" "$home/.ssh/authorized_keys"; chmod 600 "$home/.ssh/authorized_keys"
}
install_key admin "$ADMIN_SSH_PUBKEY"
install_key deploy "$DEPLOY_SSH_PUBKEY"
# `admin` runs sudo with a key only, no password is set.
echo 'admin ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-quack-admin
chmod 440 /etc/sudoers.d/90-quack-admin

step "firewall: ufw 22/80/443 (rule for 22 first, then enable)"
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw default deny incoming
ufw default allow outgoing
ufw --force enable

step "ssh: keys only, no root, no passwords"
cat > /etc/ssh/sshd_config.d/10-quack.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
EOF
sshd -t
systemctl reload ssh
systemctl enable --now fail2ban

step "docker engine >= $DOCKER_MIN_VERSION"
if command -v snap >/dev/null 2>&1 && snap list docker >/dev/null 2>&1; then
  # A snap refreshes itself and restarts the daemon with every container (Д-§10), and its
  # confinement hides /srv/quack. Remove it by hand, the script then installs docker-ce.
  echo "Docker is installed as a snap. Run 'snap remove docker --purge', then start this script again." >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi
installed="$(docker version --format '{{.Server.Version}}')"
if [[ "$(printf '%s\n%s\n' "$DOCKER_MIN_VERSION" "$installed" | sort -V | head -n 1)" != "$DOCKER_MIN_VERSION" ]]; then
  echo "docker $installed is older than $DOCKER_MIN_VERSION (published ports on 127.0.0.1 and CVE-2025-54388)" >&2
  exit 1
fi
# `deploy` in the docker group is equivalent to root: a known trade-off of Д-§10.
getent group docker >/dev/null || groupadd docker
usermod -aG docker deploy

step "swap ${SWAP_SIZE_GB} GB, vm.swappiness=10"
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  [[ -f /swapfile ]] || { fallocate -l "${SWAP_SIZE_GB}G" /swapfile && chmod 600 /swapfile && mkswap /swapfile; }
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness = 10' > /etc/sysctl.d/90-quack.conf
sysctl --system >/dev/null

step "unattended-upgrades without Docker packages (Д-§10)"
cat > /etc/apt/apt.conf.d/52quack-unattended <<'EOF'
Unattended-Upgrade::Package-Blacklist {
    "docker-ce";
    "docker-ce-cli";
    "containerd.io";
    "docker-compose-plugin";
};
// Reboots are done by hand, outside the demo window, after a snapshot.
Unattended-Upgrade::Automatic-Reboot "false";
EOF
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

step "journald SystemMaxUse=$JOURNAL_MAX_USE"
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=%s\n' "$JOURNAL_MAX_USE" > /etc/systemd/journald.conf.d/90-quack.conf
systemctl restart systemd-journald

step "directories (Д-§4.2)"
install -d -m 755 -o deploy -g deploy "$SRV" "$SRV/data" "$SRV/tmp"
# Owned by the user of the `backup` container (uid 10001), mode 700 (Д-§8.1).
install -d -m 700 -o 10001 -g 10001 "$SRV/backups"

step "steal time once a minute (Д-§9.1)"
cat > /usr/local/sbin/quack-steal <<'EOF'
#!/bin/sh
# `st` of vmstat (average over one second) into the host journal: the one metric that tells
# "we are slow" from "the neighbours took the cores" (Д-§9.1, Д-§2.3).
st="$(vmstat 1 2 | tail -n 1 | awk '{print $(NF-1)}')"
logger -t quack-steal "steal_pct=$st"
EOF
chmod 755 /usr/local/sbin/quack-steal
cat > /etc/systemd/system/quack-steal.service <<'EOF'
[Unit]
Description=Record CPU steal time

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/quack-steal
EOF
cat > /etc/systemd/system/quack-steal.timer <<'EOF'
[Unit]
Description=Record CPU steal time every minute

[Timer]
OnCalendar=minutely

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now quack-steal.timer

step "backup timer (Д-§8.1)"
# Every 6 hours = backup_interval_hours (starting value); change both together. The unit is
# skipped until the first rollout has put .env and compose.yaml into $SRV.
cat > /etc/systemd/system/quack-backup.service <<EOF
[Unit]
Description=Quack Postgres dump to R2 (docker compose run --rm backup)
ConditionPathExists=$SRV/.env

[Service]
Type=oneshot
User=deploy
WorkingDirectory=$SRV
Environment=COMPOSE_FILE=compose.yaml
ExecStart=/usr/bin/docker compose --env-file .env --env-file release.env run --rm backup
EOF
cat > /etc/systemd/system/quack-backup.timer <<'EOF'
[Unit]
Description=Quack Postgres dump every 6 hours

[Timer]
OnCalendar=*-*-* 00/6:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now quack-backup.timer

step "alerts: host state file and timer (Д-§9.3)"
# The `alerts` container has no Docker socket: this timer writes containers and disk into a file
# it can read (/run/quack/state.json is on tmpfs, so a reboot starts with no file).
cat > /usr/local/sbin/quack-host-state <<'EOF'
#!/bin/sh
set -eu
install -d -m 755 /run/quack
python3 - > /run/quack/state.json.tmp <<'PY'
import json, shutil, subprocess, time
ids = subprocess.run(
    ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=quack"],
    capture_output=True, text=True, check=True,
).stdout.split()
containers = []
if ids:
    out = subprocess.run(
        ["docker", "inspect", "--format", "{{.Name}}|{{.State.Status}}|{{.RestartCount}}", *ids],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    for line in out:
        name, state, restarts = line.split("|")
        containers.append({"name": name.lstrip("/"), "state": state, "restart_count": int(restarts)})
usage = shutil.disk_usage("/")
print(json.dumps({
    "generated_at_epoch": time.time(),
    "disk_used_pct": round(usage.used * 100 / usage.total),
    "containers": containers,
}))
PY
chmod 644 /run/quack/state.json.tmp
mv /run/quack/state.json.tmp /run/quack/state.json
EOF
chmod 755 /usr/local/sbin/quack-host-state
cat > /etc/systemd/system/quack-host-state.service <<'EOF'
[Unit]
Description=Write containers and disk state for the alerts container

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/quack-host-state
EOF
cat > /etc/systemd/system/quack-host-state.timer <<'EOF'
[Unit]
Description=Write containers and disk state every minute

[Timer]
OnCalendar=minutely

[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/quack-alerts.service <<EOF
[Unit]
Description=Quack alerts check (docker compose run --rm alerts)
ConditionPathExists=$SRV/.env

[Service]
Type=oneshot
User=deploy
WorkingDirectory=$SRV
Environment=COMPOSE_FILE=compose.yaml
ExecStart=/usr/bin/docker compose --env-file .env --env-file release.env run --rm alerts
EOF
cat > /etc/systemd/system/quack-alerts.timer <<'EOF'
[Unit]
Description=Quack alerts check every 5 minutes

[Timer]
OnCalendar=*:0/5

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now quack-host-state.timer quack-alerts.timer

step "done"
echo "Check from ANOTHER terminal that 'ssh admin@<this host>' works before closing this session."
echo "Next: deploy/README.md (provider firewall, DNS, GitHub Environment, first rollout)."
