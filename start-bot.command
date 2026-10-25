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

# 3. Токены и база живут в отдельной папке ~/channel-bot-data — так их не потеряешь при обновлении бота.
export DATA_DIR="$HOME/channel-bot-data"
mkdir -p "$DATA_DIR"
ENV_FILE="$DATA_DIR/.env"
# Переезд: если в новой папке ещё пусто — забираем данные из старой копии бота рядом (или из этой).
if [ ! -s "$ENV_FILE" ]; then
  for old in ./ ../channel-bot-main*/; do
    if [ -s "$old.env" ]; then
      cp "$old.env" "$ENV_FILE"
      for f in bot_data.db runtime.json; do
        [ -f "$old$f" ] && [ ! -f "$DATA_DIR/$f" ] && cp "$old$f" "$DATA_DIR/$f"
      done
      echo "Нашёл сохранённые настройки в $old — перенёс."
      break
    fi
  done
fi
touch "$ENV_FILE"; chmod 600 "$ENV_FILE"
# Если в .env когда-то попал мусор — оставляем только правильные строки.
LC_ALL=C grep -E '^(TELEGRAM_BOT_TOKEN|ANTHROPIC_API_KEY|ANTHROPIC_BASE_URL|CHANNEL_ID|OWNER_ID|DISCUSSION_CHAT_ID)=[A-Za-z0-9:_@./-]+$' "$ENV_FILE" > "$ENV_FILE.clean"
mv "$ENV_FILE.clean" "$ENV_FILE"; chmod 600 "$ENV_FILE"
# Спрашиваем то, чего не хватает (символы при вводе не видны, это нормально).
ask() {  # имя  подсказка  [необязательно]
  if grep -q "^$1=." "$ENV_FILE"; then return; fi
  while true; do
    read -r -s -p "$2: " val; echo
    val=$(echo "$val" | tr -d '[:space:]')
    if [ -n "$val" ] || [ "$3" = "необязательно" ]; then break; fi
    echo "Пусто. Вставьте значение и нажмите Enter."
  done
  if [ -n "$val" ]; then
    grep -v "^$1=" "$ENV_FILE" > "$ENV_FILE.tmp"; echo "$1=$val" >> "$ENV_FILE.tmp"
    mv "$ENV_FILE.tmp" "$ENV_FILE"; chmod 600 "$ENV_FILE"
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
