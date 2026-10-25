#!/bin/bash
# Установка (и обновление) бота на сервер Ubuntu/Debian одной командой.
# Запускать под root:  curl -fsSL https://raw.githubusercontent.com/Domennnos/channel-bot/main/install.sh | bash
set -e

APP_DIR=/opt/channel-bot          # где лежит код бота
DATA_PATH=/var/lib/channel-bot     # где лежат база, ваши настройки и переключатели режимов

if [ "$(id -u)" -ne 0 ]; then
  echo "Запустите установку от имени root (в консоли сервера это обычно так и есть)."; exit 1
fi

echo "==> 1/5 Ставлю нужные программы (Python, git)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -q
apt-get install -y -q python3 python3-venv python3-pip git

echo "==> 2/5 Скачиваю код бота…"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull -q --ff-only
else
  git clone -q https://github.com/Domennnos/channel-bot "$APP_DIR"
fi
cd "$APP_DIR"

echo "==> 3/5 Ставлю библиотеки бота…"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

mkdir -p "$DATA_PATH"
# Файл настроек (стиль, расписание) копируем один раз; дальше его правите вы.
[ -f "$DATA_PATH/settings.json" ] || cp settings.json "$DATA_PATH/settings.json"

echo "==> 4/5 Настраиваю секреты (файл .env)."
touch .env && chmod 600 .env
# Если какого-то значения нет в .env — спрашиваем. Ввод скрыт: символы на экране не появляются.
ask() {  # имя  подсказка  [необязательно]
  if grep -q "^$1=." .env; then return; fi
  while true; do
    read -r -s -p "$2: " VAL </dev/tty; echo
    VAL=$(echo "$VAL" | tr -d '[:space:]')
    if [ -n "$VAL" ] || [ "$3" = "необязательно" ]; then break; fi
    echo "Пусто. Вставьте значение и нажмите Enter."
  done
  if [ -n "$VAL" ]; then sed -i "/^$1=/d" .env; echo "$1=$VAL" >> .env; fi
}
ask TELEGRAM_BOT_TOKEN "Вставьте токен бота от BotFather и нажмите Enter"
ask ANTHROPIC_API_KEY  "Вставьте ключ Anthropic (sk-ant-…) и нажмите Enter"
ask CHANNEL_ID         "Вставьте адрес канала, например @mychannel, и нажмите Enter"
ask OWNER_ID           "Ваш Telegram-ID (число). Если ещё не знаете — просто Enter" необязательно
sed -i '/^DATA_DIR=/d' .env; echo "DATA_DIR=$DATA_PATH" >> .env

echo "==> 5/5 Включаю автозапуск (бот стартует сам и после перезагрузки сервера)…"
cat > /etc/systemd/system/channel-bot.service <<UNIT
[Unit]
Description=Telegram-бот для канала
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q channel-bot
systemctl restart channel-bot
sleep 4

echo
if systemctl is-active -q channel-bot; then
  echo "ГОТОВО: бот запущен и работает. Напишите ему в Telegram /start."
else
  echo "Бот не запустился. Покажите разработчику вывод команды: journalctl -u channel-bot -n 30 --no-pager"
fi
