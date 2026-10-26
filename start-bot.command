#!/bin/bash
# Запуск бота на Mac двойным щелчком. Окно должно оставаться открытым, пока бот работает.
# Первый запуск: установит нужное и спросит токены (ввод скрыт). Дальше будет запускаться сразу.
cd "$(dirname "$0")" || exit 1

pause() { echo; read -r -p "Нажмите Enter, чтобы закрыть окно…" _; }

# 0. Автообновление: берём свежую версию бота с GitHub и обновляем файлы кода.
#    Токены, база и ваши настройки лежат в ~/channel-bot-data и не затрагиваются.
#    Ждём не больше 10 секунд; если GitHub медленный — качаем в фоне, применим при следующем запуске.
BOT_ZIP_URL="${BOT_ZIP_URL:-https://github.com/Domennnos/channel-bot/archive/refs/heads/main.zip}"
STAGE="$HOME/channel-bot-data/update"   # сюда фоновая загрузка кладёт скачанное обновление
mkdir -p "$STAGE"
self_updated=0

apply_zip() {  # распаковывает архив $1 и заменяет изменившиеся файлы в папке бота
  local tmp src f name updated=0
  tmp=$(mktemp -d)
  # Распаковываем силами Python (системный unzip на Mac иногда зависает на русских именах файлов).
  if python3 -I -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$1" "$tmp" </dev/null 2>/dev/null; then
    src=$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -n 1)
    for f in "$src"/*; do
      [ -f "$f" ] || continue
      name=$(basename "$f")
      if ! cmp -s "$f" "./$name"; then
        cp "$f" "./$name.new" && mv "./$name.new" "./$name"   # mv — чтобы безопасно заменить и этот скрипт
        updated=1
        [ "$name" = "start-bot.command" ] && self_updated=1
      fi
    done
    chmod +x ./start-bot.command
  fi
  [ -n "$tmp" ] && [ -d "$tmp" ] && rm -r "$tmp"
  if [ "$updated" = 1 ]; then echo "Бот обновлён до свежей версии."; else echo "У вас самая свежая версия."; fi
}

if [ -z "$BOT_JUST_UPDATED" ]; then
  if [ -s "$STAGE/bot.zip" ]; then
    # В прошлый раз обновление скачалось в фоне — применяем его мгновенно.
    apply_zip "$STAGE/bot.zip"
    rm -f "$STAGE/bot.zip"
  else
    echo "Проверяю обновления (до 10 секунд)…"
    if curl -fsSL --connect-timeout 5 --max-time 10 "$BOT_ZIP_URL" -o "$STAGE/quick.zip" 2>/dev/null; then
      apply_zip "$STAGE/quick.zip"
    else
      echo "GitHub отвечает медленно — запускаю бота сразу, обновление скачаю в фоне (применится при следующем запуске)."
      ( curl -fsSL --connect-timeout 20 --max-time 600 "$BOT_ZIP_URL" -o "$STAGE/bot.zip.part" \
          && mv "$STAGE/bot.zip.part" "$STAGE/bot.zip" ) >/dev/null 2>&1 &
    fi
    rm -f "$STAGE/quick.zip"
  fi
  if [ "$self_updated" = 1 ]; then
    # Обновился сам скрипт запуска — перезапускаем его уже новой версией.
    BOT_JUST_UPDATED=1 exec bash "./start-bot.command"
  fi
fi

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
# Ваш файл настроек (стиль, расписание) живёт рядом с токенами, чтобы обновления его не затирали.
[ -f "$DATA_DIR/settings.json" ] || cp settings.json "$DATA_DIR/settings.json"
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
