"""
Настройки бота: секреты берём из файла .env, всё остальное — из settings.json.

Секреты (токен бота, ключ Anthropic) НИКОГДА не пишутся в код и не печатаются в логи.
"""
import json
import os
import sys
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Папка, где лежит бот. Все файлы (настройки, база данных) ищем рядом с кодом.
BASE_DIR = Path(__file__).resolve().parent

# Папка для данных, которые должны пережить перезапуск сервера (база, переключатели режимов).
# На хостинге сюда подключают постоянный диск (Volume) и задают DATA_DIR=/data.
DATA_DIR = Path(os.environ.get("DATA_DIR", "") or BASE_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)
# Ваши настройки лежат в DATA_DIR (так их не затрёт обновление бота); если там файла нет — берём образец из папки бота.
SETTINGS_PATH = DATA_DIR / "settings.json" if (DATA_DIR / "settings.json").exists() else BASE_DIR / "settings.json"
DB_PATH = DATA_DIR / "bot_data.db"
RUNTIME_PATH = DATA_DIR / "runtime.json"  # сюда пишутся только то, что вы меняете командами в боте

# Какие настройки можно менять командами бота (/mode, /replies)
ИЗМЕНЯЕМЫЕ_КОМАНДАМИ = ("режим_публикации", "ответы_на_комментарии")

# Читаем секреты из файла .env (если он есть) в переменные окружения.
load_dotenv(BASE_DIR / ".env")

# Дни недели так, как их понимает планировщик Telegram-библиотеки: 0 = воскресенье.
ДНИ_НЕДЕЛИ = {"вс": 0, "пн": 1, "вт": 2, "ср": 3, "чт": 4, "пт": 5, "сб": 6}


def _env(name: str) -> str:
    """Берёт значение из окружения (.env). Пустое значение = пустая строка."""
    return os.environ.get(name, "").strip()


TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
CHANNEL_ID = _env("CHANNEL_ID")  # @имя или число вроде -1001234567890
OWNER_ID = int(_env("OWNER_ID")) if _env("OWNER_ID").lstrip("-").isdigit() else None
DISCUSSION_CHAT_ID = (
    int(_env("DISCUSSION_CHAT_ID")) if _env("DISCUSSION_CHAT_ID").lstrip("-").isdigit() else None
)


def проверить_секреты() -> None:
    """Если чего-то важного нет в .env — говорим об этом по-человечески и выходим."""
    нет = []
    if not TELEGRAM_BOT_TOKEN:
        нет.append("TELEGRAM_BOT_TOKEN (токен бота от @BotFather)")
    if not ANTHROPIC_API_KEY:
        нет.append("ANTHROPIC_API_KEY (ключ Anthropic)")
    if not CHANNEL_ID:
        нет.append("CHANNEL_ID (адрес вашего канала)")
    if нет:
        print("В файле .env не заполнено:\n - " + "\n - ".join(нет))
        print("Откройте файл .env и впишите значения (образец — в .env.example).")
        sys.exit(1)


def загрузить_настройки() -> dict:
    """Читает settings.json и поверх него — то, что вы меняли командами бота (runtime.json)."""
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        настройки = json.load(f)
    if RUNTIME_PATH.exists():
        with open(RUNTIME_PATH, encoding="utf-8") as f:
            настройки.update(json.load(f))
    return настройки


def сохранить_настройки(настройки: dict) -> None:
    """Запоминает настройки, которые меняются командами бота (в отдельный файл в DATA_DIR)."""
    with open(RUNTIME_PATH, "w", encoding="utf-8") as f:
        json.dump({k: настройки[k] for k in ИЗМЕНЯЕМЫЕ_КОМАНДАМИ}, f, ensure_ascii=False, indent=2)


def время_из_строки(текст: str, часовой_пояс: str) -> time:
    """'18:30' + 'Europe/Moscow' -> объект времени с часовым поясом."""
    часы, минуты = текст.split(":")
    return time(int(часы), int(минуты), tzinfo=ZoneInfo(часовой_пояс))


def дни_из_списка(дни: list[str]) -> tuple[int, ...]:
    """['вт', 'пт'] -> (2, 5)."""
    return tuple(ДНИ_НЕДЕЛИ[д.strip().lower()] for д in дни)
