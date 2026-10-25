#!/bin/bash
# Запуск бота на Mac двойным щелчком. Окно должно оставаться открытым, пока бот работает.
# Первый запуск: установит нужное и спросит токены (ввод скрыт). Дальше будет запускаться сразу.
cd "$(dirname "$0")" || exit 1

pause() { echo; read -r -p "Нажмите Enter, чтобы закрыть окно…" _; }

# 1. Проверяем Python
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "Не нашёл Python версии 3.10 или новее."
  echo "Скачайте и установите его с https://www.python.org/downloads/ (кнопка Download Python),"
  echo "потом снова дважды щёлкните по этому файлу."
  pause; exit 1
fi

# 2. Готовим «рабочую папку» с библиотеками (один раз; потом только быстро проверяем)
if [ ! -d .venv ]; then
  echo "Первый запуск: устанавливаю нужное, это займёт минуту…"
  python3 -m venv .venv || { echo "Не получилось подготовить Python."; pause; exit 1; }
fi
source .venv/bin/activate
pip install -q -r requirements.txt || { echo "Не получилось установить библиотеки (проверьте интернет)."; pause; exit 1; }

# 3. Если в файле .env чего-то нет — спрашиваем (символы при вводе не видны, это нормально)
touch .env; chmod 600 .env
# Если в .env когда-то попал мусор — оставляем только правильные строки.
LC_ALL=C grep -E '^(TELEGRAM_BOT_TOKEN|ANTHROPIC_API_KEY|ANTHROPIC_BASE_URL|CHANNEL_ID|OWNER_ID|DISCUSSION_CHAT_ID)=[A-Za-z0-9:_@./-]+$' .env > .env.clean
mv .env.clean .env; chmod 600 .env
ask() {  # имя  подсказка  [необязательно]
  if grep -q "^$1=." .env; then return; fi
  while true; do
    read -r -s -p "$2: " val; echo
    val=$(echo "$val" | tr -d '[:space:]')
    if [ -n "$val" ] || [ "$3" = "необязательно" ]; then break; fi
    echo "Пусто. Вставьте val и нажмите Enter."
  done
  if [ -n "$val" ]; then
    grep -v "^$1=" .env > .env.tmp; echo "$1=$val" >> .env.tmp; mv .env.tmp .env; chmod 600 .env
  fi
}
ask TELEGRAM_BOT_TOKEN "Вставьте токен бота от BotFather и нажмите Enter"
ask ANTHROPIC_API_KEY  "Вставьте ключ Anthropic (sk-ant-…) и нажмите Enter"
ask CHANNEL_ID         "Вставьте адрес канала, например @mychannel, и нажмите Enter"
ask ANTHROPIC_BASE_URL     "Адрес сервера ИИ (если ключ от посредника, например https://api.getapi.trade). Если ключ напрямую от Anthropic — впишите - и Enter"
ask OWNER_ID           "Ваш Telegram-ID (число). Если ещё не знаете — просто Enter" необязательно

# 4. Запускаем. caffeinate не даёт Mac уснуть, пока бот работает.
echo
echo "Бот запущен. Не закрывайте это окно. Остановить: Ctrl+C или закройте окно."
caffeinate -i python bot.py
echo; echo "Бот остановлен."
pause
