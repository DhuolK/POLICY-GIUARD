#!/usr/bin/env bash
# PolicyGuard — one-shot server setup for an Oracle Cloud Always Free VM
# (Ubuntu 22.04/24.04, ARM64 or x86_64).
#
# Prereq: the app code must already be at /srv/policyguard (git clone or scp —
# see docs/deployment-oracle.md Part D). Then run:
#   chmod +x /srv/policyguard/deploy/oracle/setup.sh
#   sudo /srv/policyguard/deploy/oracle/setup.sh
#
# Idempotent: safe to re-run; each step checks before acting.
set -euo pipefail

APP_USER=policyguard
APP_DIR=/srv/policyguard

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo." >&2
    exit 1
fi

if [ ! -f "$APP_DIR/requirements.txt" ] || [ ! -d "$APP_DIR/app" ]; then
    echo "ERROR: app code not found at $APP_DIR." >&2
    echo "Upload it first (docs/deployment-oracle.md Part D), then re-run." >&2
    exit 1
fi

echo "==> [1/7] System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx cron git \
    zip unzip iptables-persistent curl

echo "==> [2/7] Dedicated system user: $APP_USER"
if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd --system --shell /usr/sbin/nologin --home-dir "$APP_DIR" "$APP_USER"
fi
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

echo "==> [3/7] Python virtualenv + pinned dependencies"
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
fi
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> [4/7] systemd service (gunicorn)"
cp "$APP_DIR/deploy/oracle/policyguard.service" /etc/systemd/system/policyguard.service
systemctl daemon-reload
systemctl enable policyguard
if [ -f "$APP_DIR/.env" ]; then
    systemctl restart policyguard
    echo "    .env found — service (re)started."
else
    echo "    NOTE: $APP_DIR/.env missing — service enabled but NOT started."
    echo "    Create .env (docs Part E), then: sudo systemctl start policyguard"
fi

echo "==> [5/7] nginx reverse proxy"
cp "$APP_DIR/deploy/oracle/nginx-policyguard.conf" /etc/nginx/sites-available/policyguard
ln -sf /etc/nginx/sites-available/policyguard /etc/nginx/sites-enabled/policyguard
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx

echo "==> [6/7] VM firewall (Oracle Ubuntu images ship an iptables REJECT rule)"
for PORT in 80 443; do
    if ! iptables -C INPUT -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null; then
        iptables -I INPUT 1 -p tcp --dport "$PORT" -j ACCEPT
        echo "    opened tcp/$PORT"
    fi
done
netfilter-persistent save

echo "==> [7/7] SMS scheduler cron (every minute, same as the old cPanel job)"
touch /var/log/policyguard-sms.log
chown "$APP_USER":"$APP_USER" /var/log/policyguard-sms.log
cat > /etc/cron.d/policyguard-sms <<'EOF'
# PolicyGuard SMS engine tick — mirrors the old cPanel per-minute cron job.
* * * * * policyguard cd /srv/policyguard && /srv/policyguard/venv/bin/python scripts/sms_scheduler.py --once >> /var/log/policyguard-sms.log 2>&1
EOF
chmod 644 /etc/cron.d/policyguard-sms

echo
echo "Setup complete. Remaining manual steps (docs/deployment-oracle.md):"
echo "  E. Create $APP_DIR/.env with production values, then: sudo systemctl start policyguard"
echo "  F. Smoke test: curl http://localhost/healthz  and  http://<VM-IP>/healthz"
echo "  G. Point DNS A record at this VM's public IP (leave MX records alone!)"
echo '  H. TLS: sudo snap install --classic certbot && sudo certbot --nginx \'
echo '        -d westlakeinsuranceltd.com -d www.westlakeinsuranceltd.com'
echo "  I. Migrate claim uploads from Truehost (zip + scp)"
echo "  J. Tighten Atlas allowlist to this VM's IP; K. re-register M-Pesa/AT webhooks"
