#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   sudo bash scripts/pi5-setup-myapp.sh --app myapp --domain myapp.quamity.com
#
# Ne yapar:
# - /var/www/<app> dizinlerini açar (releases/current)
# - deploy kullanıcısını ve sudoers iznini ayarlar (nginx reload için)
# - Nginx site dosyasını yazar ve reload eder
# - Cloudflared (tunnel) kurar; login/create/route adımlarını ekrana hatırlatır

APP_NAME=""
DOMAIN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)    APP_NAME="$2"; shift 2;;
    --domain) DOMAIN="$2"; shift 2;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  endesac
done

if [[ -z "${APP_NAME}" || -z "${DOMAIN}" ]]; then
  echo "Gerekli parametreler: --app <name> --domain <host.example.com>" >&2
  exit 2
fi

echo "===> Paketler"
apt update -y
apt install -y nginx curl git rsync

WEB_ROOT="/var/www/${APP_NAME}"
echo "===> Web root: ${WEB_ROOT}"
mkdir -p "${WEB_ROOT}/releases" "${WEB_ROOT}/current"

echo "===> deploy kullanıcısı ve izinler"
if ! id deploy >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" deploy
fi
mkdir -p /home/deploy/.ssh
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chown -R deploy:deploy "${WEB_ROOT}"

echo "===> deploy için parolasız sudo (nginx reload)"
SUDOERS_FILE="/etc/sudoers.d/deploy-nginx"
if [[ ! -f "${SUDOERS_FILE}" ]]; then
  echo 'deploy ALL=(ALL) NOPASSWD: /usr/sbin/nginx, /bin/systemctl' > "${SUDOERS_FILE}"
  chmod 440 "${SUDOERS_FILE}"
fi

echo "===> Nginx site yazılıyor"
SITE_FILE="/etc/nginx/sites-available/${APP_NAME}"
cat > "${SITE_FILE}" <<EOF
server {
  listen 80;
  server_name ${DOMAIN};

  root ${WEB_ROOT}/current;
  index index.html;

  # SPA yönlendirme
  location / { try_files \$uri /index.html; }

  # Basit cache
  location ~* \.(js|css|png|jpg|jpeg|gif|svg|ico|woff2?)$ {
    try_files \$uri =404;
    add_header Cache-Control "public, max-age=31536000, immutable";
    access_log off;
  }
}
EOF

ln -sf "${SITE_FILE}" "/etc/nginx/sites-enabled/${APP_NAME}"
nginx -t
systemctl reload nginx

echo "===> Cloudflared kurulumu kontrol"
if ! command -v cloudflared >/dev/null 2>&1; then
  ARCH="$(dpkg --print-architecture)" # arm64 beklenir
  TMP_DEB="$(mktemp)"
  curl -fsSL -o "${TMP_DEB}" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"
  apt install -y "${TMP_DEB}"
  rm -f "${TMP_DEB}"
fi

CFG_DIR="/home/deploy/.cloudflared"
mkdir -p "${CFG_DIR}"
chown -R deploy:deploy "${CFG_DIR}"

echo
echo "============================================================"
echo "Cloudflare Tunnel için MANUEL adımlar (bir kere):"
echo "  cloudflared tunnel login"
echo "  cloudflared tunnel create pi5-server"
echo "  cloudflared tunnel route dns pi5-server ${DOMAIN}"
echo
echo "Sonra config:"
echo "  sudo -u deploy bash -lc 'cat > ${CFG_DIR}/config.yml <<CONF"
echo "tunnel: pi5-server"
echo "credentials-file: \$(ls -1 ${CFG_DIR}/*.json | head -n1)"
echo "ingress:"
echo "  - hostname: ${DOMAIN}"
echo "    service: http://localhost:80"
echo "  - service: http_status:404"
echo "CONF'"
echo
echo "Servisi başlat:"
echo "  sudo cloudflared service install"
echo "  sudo systemctl enable --now cloudflared"
echo "============================================================"
echo

echo "Kurulum tamam. Yayın klasörün: ${WEB_ROOT}"
echo "Canlı symlink: ${WEB_ROOT}/current"
