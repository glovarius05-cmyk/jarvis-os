#!/usr/bin/env bash
# ==============================================================================
# Jarvis OS - Nginx Reverse Proxy & Let's Encrypt SSL Automation for VPS
# ==============================================================================
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"

echo "========================================================"
echo "  Jarvis OS - Настройка Nginx Reverse Proxy и SSL (HTTPS)"
echo "========================================================"

if [ -z "$DOMAIN" ]; then
    echo "Использование: $0 <ваш-домен> [email-для-ssl]"
    echo "Пример: $0 jarvis.mydomain.com admin@mydomain.com"
    echo ""
    read -rp "Введите доменное имя для Jarvis OS (например: jarvis.domain.com): " DOMAIN
fi

if [ -z "$DOMAIN" ]; then
    echo "Ошибка: доменное имя не указано. Завершение работы."
    exit 1
fi

if [ -z "$EMAIL" ]; then
    EMAIL="admin@${DOMAIN}"
fi

echo "[1/5] Проверка прав root..."
if [ "$(id -u)" -ne 0 ]; then
    echo "Ошибка: скрипт должен быть запущен с правами root (sudo)."
    exit 1
fi

echo "[2/5] Установка Nginx и Certbot..."
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx

echo "[3/5] Создание конфигурации Nginx для ${DOMAIN}..."
NGINX_CONF="/etc/nginx/sites-available/jarvis.conf"

cat << EOF > "$NGINX_CONF"
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    # Максимальный размер загружаемых файлов в Second Brain
    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:7777;
        proxy_http_version 1.1;

        # Заголовки проксирования
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Полнодуплексный WebSocket для чата и стриминга (/ws/chat)
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        # Отключение буферизации для плавного стриминга ответов LLM
        proxy_buffering off;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
EOF

# Активация конфигурации
ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Проверка конфигурации Nginx
nginx -t
systemctl reload nginx

echo "[4/5] Выпуск бесплатного SSL-сертификата Let's Encrypt для ${DOMAIN}..."
if certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m "${EMAIL}" --redirect; then
    echo "✅ SSL-сертификат успешно установлен и настроен редирект с HTTP на HTTPS!"
else
    echo "⚠️ Certbot не смог автоматически выпустить сертификат."
    echo "Проверьте, что DNS A-запись для ${DOMAIN} указывает на IP этого сервера."
    echo "Nginx пока работает по протоколу HTTP: http://${DOMAIN}"
fi

echo "[5/5] Перезапуск Nginx..."
systemctl restart nginx

echo "========================================================"
echo "🎉 Готово! Jarvis OS теперь доступен по адресу:"
echo "   https://${DOMAIN}"
echo "========================================================"
