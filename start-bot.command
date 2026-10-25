#!/bin/bash
# Запуск бота на Mac двойным щелчком. Окно должно оставаться открытым, пока бот работает.
# Первый запуск: установит нужное и спросит токены (ввод скрыт). Дальше будет запускаться сразу.
cd "$(dirname "$0")" || exit 1

пауза() { echo; read -r -p "Нажмите Enter, чтобы закрыть окно…" _; }

# 1. Проверяем Python
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "Не нашёл Python версии 3.10 или новее."
  echo "Скачайте и установите его с https://www.python.org/downloads/ (кнопка Download Python),"
  echo "потом снова дважды щёлкните по этому файлу."
  пауза; exit 1
fi

# 2. Готовим «рабочую папку» с библиотеками (один раз; потом только быстро проверяем)
if [ ! -d .venv ]; then
  echo "Первый запуск: устанавливаю нужное, это займёт минуту…"
  python3 -m venv .venv || { echo "Не получилось подготовить Python."; пауза; exit 1; }
fi
source .venv/bin/activate
pip install -q -r requirements.txt || { echo "Не получилось установить библиотеки (проверьте интернет)."; пауза; exit 1; }

# 3. Если в файле .env чего-то нет — спрашиваем (символы при вводе не видны, это нормально)
touch .env; chmod 600 .env
спросить() {  # имя  подсказка  [необязательно]
  if grep -q "^$1=." .env; then return; fi
  while true; do
    read -r -s -p "$2: " значение; echo
    значение=$(echo "$значение" | tr -d '[:space:]')
    if [ -n "$значение" ] || [ "$3" = "необязательно" ]; then break; fi
    echo "Пусто. Вставьте значение и нажмите Enter."
  done
  if [ -n "$значение" ]; then
    grep -v "^$1=" .env > .env.tmp; echo "$1=$значение" >> .env.tmp; mv .env.tmp .env; chmod 600 .env
  fi
}
спросить TELEGRAM_BOT_TOKEN "Вставьте токен бота от BotFather и нажмите Enter"
спросить ANTHROPIC_API_KEY  "Вставьте ключ Anthropic (sk-ant-…) и нажмите Enter"
спросить CHANNEL_ID         "Вставьте адрес канала, например @mychannel, и нажмите Enter"
спросить OWNER_ID           "Ваш Telegram-ID (число). Если ещё не знаете — просто Enter" необязательно

# 4. Запускаем. caffeinate не даёт Mac уснуть, пока бот работает.
echo
echo "Бот запущен. Не закрывайте это окно. Остановить: Ctrl+C или закройте окно."
caffeinate -i python bot.py
echo; echo "Бот остановлен."
пауза
