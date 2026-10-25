#!/bin/bash
# Установка (и обновление) бота на сервер Ubuntu/Debian одной командой.
# Запускать под root:  curl -fsSL https://raw.githubusercontent.com/Domennnos/channel-bot/main/install.sh | bash
set -e

ПАПКА=/opt/channel-bot          # где лежит код бота
ДАННЫЕ=/var/lib/channel-bot     # где лежат база, ваши настройки и переключатели режимов

if [ "$(id -u)" -ne 0 ]; then
  echo "Запустите установку от имени root (в консоли сервера это обычно так и есть)."; exit 1
fi

echo "==> 1/5 Ставлю нужные программы (Python, git)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -q
apt-get install -y -q python3 python3-venv python3-pip git

echo "==> 2/5 Скачиваю код бота…"
if [ -d "$ПАПКА/.git" ]; then
  git -C "$ПАПКА" pull -q --ff-only
else
  git clone -q https://github.com/Domennnos/channel-bot "$ПАПКА"
fi
cd "$ПАПКА"

echo "==> 3/5 Ставлю библиотеки бота…"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

mkdir -p "$ДАННЫЕ"
# Файл настроек (стиль, расписание) копируем один раз; дальше его правите вы.
[ -f "$ДАННЫЕ/settings.json" ] || cp settings.json "$ДАННЫЕ/settings.json"

echo "==> 4/5 Настраиваю секреты (файл .env)."
touch .env && chmod 600 .env
# Если какого-то значения нет в .env — спрашиваем. Ввод скрыт: символы на экране не появляются.
спросить() {  # имя  подсказка  [необязательно]
  if grep -q "^$1=." .env; then return; fi
  while true; do
    read -r -s -p "$2: " ЗНАЧЕНИЕ </dev/tty; echo
    ЗНАЧЕНИЕ=$(echo "$ЗНАЧЕНИЕ" | tr -d '[:space:]')
    if [ -n "$ЗНАЧЕНИЕ" ] || [ "$3" = "необязательно" ]; then break; fi
    echo "Пусто. Вставьте значение и нажмите Enter."
  done
  if [ -n "$ЗНАЧЕНИЕ" ]; then sed -i "/^$1=/d" .env; echo "$1=$ЗНАЧЕНИЕ" >> .env; fi
}
спросить TELEGRAM_BOT_TOKEN "Вставьте токен бота от BotFather и нажмите Enter"
спросить ANTHROPIC_API_KEY  "Вставьте ключ Anthropic (sk-ant-…) и нажмите Enter"
спросить CHANNEL_ID         "Вставьте адрес канала, например @mychannel, и нажмите Enter"
спросить OWNER_ID           "Ваш Telegram-ID (число). Если ещё не знаете — просто Enter" необязательно
sed -i '/^DATA_DIR=/d' .env; echo "DATA_DIR=$ДАННЫЕ" >> .env

echo "==> 5/5 Включаю автозапуск (бот стартует сам и после перезагрузки сервера)…"
cat > /etc/systemd/system/channel-bot.service <<UNIT
[Unit]
Description=Telegram-бот для канала
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$ПАПКА
ExecStart=$ПАПКА/.venv/bin/python bot.py
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
