"""
Настройки бота: секреты берём из файла .env, всё остальное — из settings.json.

Секреты (токен бота, ключ Anthropic) НИКОГДА не пишутся в код и не печатаются в логи.
"""
import json
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Папка, где лежит бот. Все файлы (настройки, база данных) ищем рядом с кодом.
BASE_DIR = Path(__file__).resolve().parent

# Папка для данных, которые должны пережить перезапуск сервера (база, переключатели режимов).
# На хостинге сюда подключают постоянный диск (Volume) и задают DATA_DIR=/data.
DATA_DIR = Path(os.environ.get("DATA_DIR", "") or BASE_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Читаем секреты из файла .env в переменные окружения: сначала из папки данных, потом из папки бота.
load_dotenv(DATA_DIR / ".env")
load_dotenv(BASE_DIR / ".env")
# Ваши настройки лежат в DATA_DIR (так их не затрёт обновление бота); если там файла нет — берём образец из папки бота.
SETTINGS_PATH = DATA_DIR / "settings.json" if (DATA_DIR / "settings.json").exists() else BASE_DIR / "settings.json"
DB_PATH = DATA_DIR / "bot_data.db"
RUNTIME_PATH = DATA_DIR / "runtime.json"  # сюда пишутся только то, что вы меняете командами в боте

# Какие настройки можно менять командами бота (/mode, /replies)
ИЗМЕНЯЕМЫЕ_КОМАНДАМИ = ("режим_публикации", "ответы_на_комментарии")


# Дни недели так, как их понимает планировщик Telegram-библиотеки: 0 = воскресенье.
ДНИ_НЕДЕЛИ = {"вс": 0, "пн": 1, "вт": 2, "ср": 3, "чт": 4, "пт": 5, "сб": 6}


def _env(name: str) -> str:
    """Берёт значение из окружения (.env). Пустое значение = пустая строка."""
    return os.environ.get(name, "").strip()


TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
# Если ключ от посредника (а не напрямую от Anthropic) — здесь адрес его сервера. Обычно пусто.
ANTHROPIC_BASE_URL = "" if _env("ANTHROPIC_BASE_URL") == "-" else _env("ANTHROPIC_BASE_URL")  # "-" значит «не нужен»
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


# Обновления стиля по умолчанию: (старый текст -> новый). Меняем только то, что вы сами не правили.
ОБНОВЛЕНИЯ_СТИЛЯ = {
    "о_чём": (
        "Личный блог автора. Главное — личные мысли и жизнь. Также: фитнес и спорт, хобби, игра на гитаре, работа с нейросетями (ИИ), создание мультиков, творчество и креаторство.",
        "Канал «В здоровом теле — мемный контент». Личный блог Димы — персонального тренера, который живёт и работает в Москве. Главное — честно и от души про свою жизнь и мысли. Темы: фитнес без занудства, жизнь в Москве и за её пределами, пробовать новое — гитара, нейросети (ИИ), мультики, креаторство.",
    ),
    "тон": (
        "Дружелюбный и дружеский, с юмором и самоиронией. Когда пост про фитнес и спорт — иногда энергичный и мотивирующий.",
        "Дружеский и живой, с лёгким юмором: игра слов, самоирония, капля сарказма — легко и без натужности. Когда пост про фитнес и спорт — энергичный и мотивирующий.",
    ),
    "длина": (
        "По-разному, но в целом коротко, чтобы читатель не уставал: от 2–3 предложений до одного небольшого абзаца. Длинные посты — только если тема правда этого требует.",
        "В целом коротко, чтобы читатель не уставал: обычно 2–4 коротких абзаца. Длинные посты — только если тема правда этого требует.",
    ),
    "эмодзи": (
        "Немного — 1–3 на пост, для настроения.",
        "Побольше, но со вкусом: примерно 3–6 на пост, по смыслу — как акценты в абзацах, не в каждом предложении.",
    ),
}


def обновить_стиль_по_умолчанию() -> list[str]:
    """
    Если в вашем settings.json остались старые формулировки стиля «по умолчанию» —
    заменяем их новыми. Строки, которые вы меняли сами, не трогаем. Возвращает, что обновили.
    """
    if not SETTINGS_PATH.exists():
        return []
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        настройки = json.load(f)
    канал = настройки.get("канал", {})
    обновлено = []
    for ключ, (старое, новое) in ОБНОВЛЕНИЯ_СТИЛЯ.items():
        if канал.get(ключ) == старое:
            канал[ключ] = новое
            обновлено.append(ключ)
    if обновлено:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(настройки, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return обновлено


def загрузить_примеры() -> list[str]:
    """
    Образцы ваших постов, по которым бот копирует манеру письма.
    Свой файл можно положить в папку данных (channel-bot-data/примеры_постов.txt) — он главнее.
    Посты в файле разделяются строкой =====.
    """
    for путь in (DATA_DIR / "примеры_постов.txt", BASE_DIR / "примеры_постов.txt"):
        if путь.exists():
            текст = путь.read_text(encoding="utf-8")
            return [п.strip() for п in текст.split("\n=====\n") if п.strip()]
    return []


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


def последний_срок(дни: tuple[int, ...], время: time) -> datetime | None:
    """Последний момент по расписанию, который уже наступил (не раньше чем за неделю)."""
    сейчас = datetime.now(время.tzinfo)
    for назад in range(8):
        день = (сейчас - timedelta(days=назад)).date()
        if (день.weekday() + 1) % 7 in дни:  # у нас 0 = воскресенье, у Python 0 = понедельник
            срок = datetime.combine(день, время.replace(tzinfo=None), время.tzinfo)
            if срок <= сейчас:
                return срок
    return None
