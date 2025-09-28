#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   sudo bash pi5-setup-myapp.sh --app myapp --domain myapp.quamity.com
# Notes:
# - İlk kurulum içindir. Nginx site, klasör yapısı, deploy user, sudoers ve cloudflared iskeletini hazırlar.
# - Cloudflare login adımı etkileşimlidir, onu ayrıca çalıştırırsın (aşağıda ekrana yazar).

APP_NAME=""
DOMAIN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)    APP_NAME="$2"; shift 2;;
    --domain) DOMAIN="$2"; shift 2;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done

if [[ -z "${APP_NAME}" || -z "${DOMAIN}" ]]; then
  echo "Gerekli parametreler: --app <name> --domain <host.example.com>" >&2
  exit 2
fi

echo "===> System update & packages"
apt update -y
apt install -y nginx curl git rsync

echo "===> Web root"
WEB_ROOT="/var/www/${APP_NAME}"
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

echo "===> Nginx site"
SITE_FILE="/etc/nginx/sites-available/${APP_NAME}"
cat > "${SITE_FILE}" <<EOF
server {
  listen 80;
  server_name ${DOMAIN};

  root ${WEB_ROOT}/current;
  index index.html;

  location / { try_files \$uri /index.html; }
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

echo "===> Cloudflared (tunnel) kurulum"
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
echo "Cloudflare Tunnel için şu üç adımı MANUEL tamamla:"
echo "1) cloudflared tunnel login"
echo "2) cloudflared tunnel create pi5-server"
echo "3) cloudflared tunnel route dns pi5-server ${DOMAIN}"
echo
echo "Sonra şu config dosyasını oluştur:"
echo "  sudo -u deploy bash -lc 'cat > ${CFG_DIR}/config.yml <<CONF
tunnel: pi5-server
credentials-file: \$(ls -1 ${CFG_DIR}/*.json | head -n1)
ingress:
  - hostname: ${DOMAIN}
    service: http://localhost:80
  - service: http_status:404
CONF'"
echo
echo "Servisi başlat:"
echo "  sudo cloudflared service install"
echo "  sudo systemctl enable --now cloudflared"
echo "============================================================"
echo

echo "Kurulum bitti. Yayın klasörün: ${WEB_ROOT}"
echo "Canlı symlink: ${WEB_ROOT}/current"
