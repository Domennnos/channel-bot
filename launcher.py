"""
Запуск бота на Windows (вызывается из start-bot.bat двойным щелчком).

Что делает по порядку:
  1. проверяет обновления на GitHub (не дольше 10 секунд; если медленно — качает в фоне);
  2. готовит «рабочую папку» с библиотеками (.venv) — только при первом запуске;
  3. спрашивает токены, которых ещё нет (ввод скрыт), и хранит их в папке channel-bot-data;
  4. запускает бота. Окно должно оставаться открытым, пока бот работает.
"""
import filecmp
import getpass
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR") or Path.home() / "channel-bot-data")
ENV_FILE = DATA_DIR / ".env"
STAGE = DATA_DIR / "update"  # сюда фоновая загрузка кладёт скачанное обновление
ZIP_URL = os.environ.get(
    "BOT_ZIP_URL", "https://github.com/Domennnos/channel-bot/archive/refs/heads/main.zip"
)
# start-bot.bat не обновляем на ходу: Windows читает .bat построчно, и замена во время работы его ломает.
НЕ_ОБНОВЛЯТЬ = {"start-bot.bat"}
БЫСТРАЯ_ПРОВЕРКА_СЕК = 10
ФОНОВАЯ_ЗАГРУЗКА_СЕК = 600

# Что можно хранить в .env и в каком виде (всё прочее — мусор, выбрасываем)
РАЗРЕШЁННЫЕ = re.compile(
    r"^(TELEGRAM_BOT_TOKEN|ANTHROPIC_API_KEY|ANTHROPIC_BASE_URL|CHANNEL_ID|OWNER_ID|DISCUSSION_CHAT_ID)"
    r"=[A-Za-z0-9:_@./-]+$"
)
ВОПРОСЫ = [  # (имя, подсказка, обязательно ли)
    ("TELEGRAM_BOT_TOKEN", "Вставьте токен бота от BotFather и нажмите Enter", True),
    ("ANTHROPIC_API_KEY", "Вставьте ключ ИИ (с getapi.trade или sk-ant-…) и нажмите Enter", True),
    ("CHANNEL_ID", "Вставьте адрес канала, например @mychannel, и нажмите Enter", True),
    ("ANTHROPIC_BASE_URL",
     "Адрес сервера ИИ (для getapi: https://api.getapi.trade). Если ключ напрямую от Anthropic — впишите - и Enter",
     True),
    ("OWNER_ID", "Ваш Telegram-ID (число). Если ещё не знаете — просто Enter", False),
]


# ---------------------------------------------------------------- обновление

def скачать(куда: Path, лимит_сек: float) -> None:
    """Скачивает архив свежей версии; если не уложились в лимит — ошибка."""
    конец = time.monotonic() + лимит_сек
    # Сколько ждать ответа сервера: быстрой проверке — до 10 секунд, фоновой загрузке — до минуты.
    with urllib.request.urlopen(ZIP_URL, timeout=min(60, лимит_сек)) as ответ, open(куда, "wb") as f:
        while кусок := ответ.read(64 * 1024):
            f.write(кусок)
            if time.monotonic() > конец:
                raise TimeoutError("слишком медленно")


def применить(архив: Path) -> tuple[bool, bool]:
    """Заменяет изменившиеся файлы кода. Возвращает (что-то обновилось, обновился ли этот скрипт)."""
    обновлено = сам_обновился = False
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(архив) as z:
            z.extractall(tmp)
        папки = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if not папки:
            return False, False
        for файл in папки[0].iterdir():
            if not файл.is_file() or файл.name in НЕ_ОБНОВЛЯТЬ:
                continue
            цель = BOT_DIR / файл.name
            if цель.exists() and filecmp.cmp(файл, цель, shallow=False):
                continue
            временный = цель.with_name(цель.name + ".new")
            shutil.copyfile(файл, временный)
            os.replace(временный, цель)
            обновлено = True
            if файл.name == "launcher.py":
                сам_обновился = True
    return обновлено, сам_обновился


def запустить_фоновую_загрузку() -> None:
    """Качаем обновление в отдельном процессе — бот тем временем уже работает."""
    параметры = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.name == "nt":
        параметры["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        параметры["start_new_session"] = True
    subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--background"], **параметры)


def фоновая_загрузка() -> None:
    STAGE.mkdir(parents=True, exist_ok=True)
    часть = STAGE / "bot.zip.part"
    try:
        скачать(часть, ФОНОВАЯ_ЗАГРУЗКА_СЕК)
        os.replace(часть, STAGE / "bot.zip")
    except Exception:
        часть.unlink(missing_ok=True)


def обновиться() -> bool:
    """Возвращает True, если обновился сам этот скрипт (тогда его нужно перезапустить)."""
    STAGE.mkdir(parents=True, exist_ok=True)
    готовое = STAGE / "bot.zip"
    try:
        if готовое.exists():
            # В прошлый раз обновление скачалось в фоне — применяем мгновенно.
            обновлено, сам = применить(готовое)
            готовое.unlink(missing_ok=True)
        else:
            print(f"Проверяю обновления (до {БЫСТРАЯ_ПРОВЕРКА_СЕК} секунд)…")
            быстрое = STAGE / "quick.zip"
            try:
                скачать(быстрое, БЫСТРАЯ_ПРОВЕРКА_СЕК)
            except Exception:
                быстрое.unlink(missing_ok=True)
                print("GitHub отвечает медленно — запускаю бота сразу, обновление скачаю в фоне "
                      "(применится при следующем запуске).")
                запустить_фоновую_загрузку()
                return False
            обновлено, сам = применить(быстрое)
            быстрое.unlink(missing_ok=True)
    except Exception as e:  # битый архив и т.п. — не мешаем запуску
        print(f"Обновление пропущено ({type(e).__name__}) — запускаю текущую версию.")
        return False
    print("Бот обновлён до свежей версии." if обновлено else "У вас самая свежая версия.")
    return сам


# ---------------------------------------------------------------- подготовка

def путь_к_python_в_venv() -> Path:
    venv = BOT_DIR / ".venv"
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def подготовить_библиотеки() -> Path:
    python = путь_к_python_в_venv()
    if not python.exists():
        print("Первый запуск: устанавливаю нужное, это займёт пару минут…")
        subprocess.run([sys.executable, "-m", "venv", str(BOT_DIR / ".venv")], check=True)
    subprocess.run(
        [str(python), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r",
         str(BOT_DIR / "requirements.txt")],
        check=True,
    )
    return python


def прочитать_env() -> list[str]:
    if not ENV_FILE.exists():
        return []
    строки = ENV_FILE.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    return [s.strip() for s in строки if РАЗРЕШЁННЫЕ.match(s.strip())]


def записать_env(строки: list[str]) -> None:
    ENV_FILE.write_text("\n".join(строки) + "\n", encoding="utf-8")
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass


def спросить_недостающее(спросить=getpass.getpass) -> None:
    """Спрашивает значения, которых нет в .env. Символы при вводе не видны — так и задумано."""
    строки = прочитать_env()
    for имя, подсказка, обязательно in ВОПРОСЫ:
        if any(s.startswith(имя + "=") for s in строки):
            continue
        while True:
            значение = "".join(спросить(подсказка + ": ").split())
            if значение and РАЗРЕШЁННЫЕ.match(f"{имя}={значение}"):
                break
            if not значение and not обязательно:
                break
            print("Пусто или лишние символы. Вставьте значение ещё раз и нажмите Enter.")
        if значение:
            строки = [s for s in строки if not s.startswith(имя + "=")] + [f"{имя}={значение}"]
            записать_env(строки)
    записать_env(строки)  # заодно выбрасываем мусор, если он был


def подготовить_данные() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Ваш файл настроек (стиль, расписание) живёт рядом с токенами, чтобы обновления его не затирали.
    if not (DATA_DIR / "settings.json").exists():
        shutil.copyfile(BOT_DIR / "settings.json", DATA_DIR / "settings.json")
    спросить_недостающее()


# ---------------------------------------------------------------- запуск

def пауза() -> None:
    try:
        input("\nНажмите Enter, чтобы закрыть окно…")
    except (EOFError, KeyboardInterrupt):
        pass


def main() -> int:
    if "--background" in sys.argv:
        фоновая_загрузка()
        return 0
    if sys.version_info < (3, 10):
        print("Нужен Python 3.10 или новее: https://www.python.org/downloads/")
        пауза()
        return 1

    if not os.environ.get("BOT_JUST_UPDATED") and обновиться():
        # Обновился сам этот скрипт — перезапускаем его уже новой версией.
        окружение = dict(os.environ, BOT_JUST_UPDATED="1")
        return subprocess.call([sys.executable, "-X", "utf8", str(Path(__file__).resolve())], env=окружение)

    try:
        python = подготовить_библиотеки()
    except subprocess.CalledProcessError:
        print("Не получилось установить библиотеки (проверьте интернет и запустите ещё раз).")
        пауза()
        return 1
    подготовить_данные()

    окружение = dict(os.environ, DATA_DIR=str(DATA_DIR), PYTHONUTF8="1")
    print("\nБот запущен. Не закрывайте это окно. Остановить: Ctrl+C или закройте окно.")
    try:
        код = subprocess.call([str(python), str(BOT_DIR / "bot.py")], env=окружение, cwd=BOT_DIR)
    except KeyboardInterrupt:
        код = 0
    print("\nБот остановлен.")
    пауза()
    return код


if __name__ == "__main__":
    sys.exit(main())
