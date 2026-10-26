"""
Telegram-бот-помощник для ведения вашего канала.

Что умеет:
  • писать черновики постов в вашем стиле (по расписанию, по команде или по вашей заметке);
  • присылать черновик вам в личку с кнопками «Опубликовать / Переписать / Отклонить»;
  • собирать реакции и комментарии к постам;
  • раз в неделю присылать отчёт с идеями;
  • (по желанию) предлагать ответы на комментарии — тоже только с вашего одобрения.

Запуск:  python bot.py
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReactionTypeEmoji,
    Update,
)
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    MessageReactionHandler,
    filters,
)

import ai
import config
import db

log = logging.getLogger("channel-bot")


# ======================================================================
#                          Вспомогательное
# ======================================================================

def _канал():
    """Адрес канала: число (-100…) или @имя."""
    c = config.CHANNEL_ID
    return int(c) if c.lstrip("-").isdigit() else c


def _это_наш_канал(chat) -> bool:
    c = config.CHANNEL_ID
    if c.lstrip("-").isdigit():
        return chat.id == int(c)
    return (chat.username or "").lower() == c.lstrip("@").lower()


async def _только_владелец(update: Update) -> bool:
    """Пускаем к командам только вас. Чужих молча игнорируем."""
    пользователь = update.effective_user
    if пользователь is None:
        return False
    if config.OWNER_ID is None:
        # Первый запуск: ID ещё не вписан — подсказываем его в личке.
        if update.effective_chat and update.effective_chat.type == "private":
            await update.effective_message.reply_text(
                f"Привет! Ваш Telegram-ID: {пользователь.id}\n\n"
                f"Впишите его в файл .env в строку OWNER_ID={пользователь.id} "
                "и перезапустите бота — после этого я буду слушаться только вас."
            )
        return False
    return пользователь.id == config.OWNER_ID


async def _написать_владельцу(context: ContextTypes.DEFAULT_TYPE, текст: str, **kw):
    """Пишем вам в личку; длинное режем на части (в Telegram лимит 4096 символов)."""
    куски = [текст[i:i + 4000] for i in range(0, len(текст), 4000)] or [""]
    последнее = None
    for i, кусок in enumerate(куски):
        # Кнопки (если есть) прикрепляем только к последней части.
        параметры = kw if i == len(куски) - 1 else {}
        последнее = await context.bot.send_message(config.OWNER_ID, кусок, **параметры)
    return последнее


def _кнопки_черновика(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Опубликовать", callback_data=f"pub:{post_id}"),
            InlineKeyboardButton("🔄 Переписать", callback_data=f"rew:{post_id}"),
        ],
        [
            InlineKeyboardButton("⏰ Позже", callback_data=f"later:{post_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"rej:{post_id}"),
        ],
        *_ряд_убрать_медиа(post_id),
    ])


def _ряд_убрать_медиа(post_id: int) -> list:
    """Кнопка «Убрать видео» — только если к посту что-то прикреплено."""
    пост = db.получить_пост(post_id)
    if not пост or not пост["media_type"]:
        return []
    название = НАЗВАНИЯ_МЕДИА.get(пост["media_type"], "вложение")
    return [[InlineKeyboardButton(f"🗑 Убрать {название}", callback_data=f"nomedia:{post_id}")]]


def _пояс() -> ZoneInfo:
    return ZoneInfo(config.загрузить_настройки()["расписание"]["часовой_пояс"])


def _ближайшее(час: int, минута: int) -> tuple[datetime, str]:
    """Ближайшее наступающее ЧЧ:ММ (сегодня, если ещё не прошло, иначе завтра) и подпись для кнопки."""
    сейчас = datetime.now(_пояс())
    когда = сейчас.replace(hour=час, minute=минута, second=0, microsecond=0)
    if когда <= сейчас + timedelta(minutes=1):
        return когда + timedelta(days=1), f"Завтра {час:02d}:{минута:02d}"
    return когда, f"Сегодня {час:02d}:{минута:02d}"


# Готовые варианты времени для кнопки «Позже»: ключ -> функция, возвращающая (когда, подпись)
ВАРИАНТЫ_ВРЕМЕНИ = {
    "h1": lambda: (datetime.now(_пояс()).replace(second=0, microsecond=0) + timedelta(hours=1), "Через 1 час"),
    "h3": lambda: (datetime.now(_пояс()).replace(second=0, microsecond=0) + timedelta(hours=3), "Через 3 часа"),
    "t10": lambda: _ближайшее(10, 0),
    "t1830": lambda: _ближайшее(18, 30),
}


def _кнопки_времени(post_id: int) -> InlineKeyboardMarkup:
    def кнопка(ключ):
        return InlineKeyboardButton(ВАРИАНТЫ_ВРЕМЕНИ[ключ]()[1], callback_data=f"at:{post_id}:{ключ}")
    return InlineKeyboardMarkup([
        [кнопка("h1"), кнопка("h3")],
        [кнопка("t10"), кнопка("t1830")],
        [InlineKeyboardButton("✍️ Своё время", callback_data=f"at:{post_id}:own")],
        [InlineKeyboardButton("← Назад", callback_data=f"back:{post_id}")],
    ])


def _кнопки_запланированного(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Опубликовать сейчас", callback_data=f"pub:{post_id}")],
        [InlineKeyboardButton("↩️ Отменить отправку", callback_data=f"unsch:{post_id}")],
    ])


def _когда_по_местному(когда: datetime | str) -> str:
    if isinstance(когда, str):
        когда = datetime.fromisoformat(когда)
    return f"{когда.astimezone(_пояс()):%d.%m в %H:%M}"


def _текст_запланированного(post_id: int, текст: str, когда) -> str:
    return (
        f"{текст}\n\n— — —\n{_строка_медиа(post_id)}⏰ Запланировано на {_когда_по_местному(когда)} (черновик №{post_id}). "
        "Mac в это время должен быть включён, а бот — запущен."
    )


def разобрать_время(текст: str) -> tuple[datetime | None, str]:
    """
    Понимает: «21:00», «завтра 9:30», «28.10 19:30», «28.10.2026 19:30».
    Возвращает (время, "") или (None, "почему не получилось").
    """
    пояс = _пояс()
    сейчас = datetime.now(пояс)
    т = текст.strip().lower().replace("в ", " ").replace("  ", " ").strip()
    try:
        if м := re.fullmatch(r"(\d{1,2})[:.](\d{2})", т):
            когда = сейчас.replace(hour=int(м[1]), minute=int(м[2]), second=0, microsecond=0)
            if когда <= сейчас:
                когда += timedelta(days=1)  # это время сегодня уже прошло — значит, завтра
        elif м := re.fullmatch(r"(сегодня|завтра|послезавтра) (\d{1,2})[:.](\d{2})", т):
            сдвиг = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[м[1]]
            когда = (сейчас + timedelta(days=сдвиг)).replace(
                hour=int(м[2]), minute=int(м[3]), second=0, microsecond=0)
        elif м := re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))? (\d{1,2})[:.](\d{2})", т):
            год = int(м[3]) if м[3] else сейчас.year
            if год < 100:
                год += 2000
            когда = datetime(год, int(м[2]), int(м[1]), int(м[4]), int(м[5]), tzinfo=пояс)
            if not м[3] and когда < сейчас - timedelta(days=1):
                когда = когда.replace(year=год + 1)  # «05.01» в декабре — это следующий год
        else:
            return None, "Не понял время."
    except ValueError:
        return None, "Такой даты или времени не бывает."
    if когда <= сейчас:
        return None, "Это время уже прошло."
    if когда > сейчас + timedelta(days=60):
        return None, "Слишком далеко — можно запланировать не больше чем на 60 дней вперёд."
    return когда, ""


ПОДСКАЗКА_ВРЕМЕНИ = (
    "Напишите, когда опубликовать, например:\n"
    "• 21:00\n• завтра 9:30\n• 28.10 19:30\n\n"
    "Чтобы передумать, напишите «отмена»."
)


# Как называем вложения по-русски
НАЗВАНИЯ_МЕДИА = {"video": "видео", "photo": "фото", "animation": "GIF", "document": "файл"}
ЛИМИТ_ПОДПИСИ = 1024  # столько символов Telegram разрешает в подписи под видео/фото


def _строка_медиа(post_id: int) -> str:
    пост = db.получить_пост(post_id)
    if пост and пост["media_type"]:
        return f"📎 Прикреплено {НАЗВАНИЯ_МЕДИА.get(пост['media_type'], 'вложение')}.\n"
    return ""


def _текст_черновика(post_id: int, текст: str) -> str:
    return (
        f"{текст}\n\n— — —\n{_строка_медиа(post_id)}Черновик №{post_id}. Нажмите кнопку ниже. "
        "Чтобы поправить, ответьте на это сообщение, что изменить. "
        "Чтобы добавить видео или фото — ответьте на это сообщение видео или фото."
    )


def _сумма_реакций(channel_msg_id: int) -> int:
    return sum(db.реакции_поста(channel_msg_id).values())


def _контекст_для_ии() -> tuple[list[str], list[str]]:
    """Недавние посты (для стиля) и самые «зашедшие» посты (по реакциям)."""
    недавние = db.последние_опубликованные(30)
    прошлые = [п["text"] for п in недавние[:6]]
    с_оценкой = sorted(
        ((_сумма_реакций(п["channel_msg_id"]), п["text"]) for п in недавние), reverse=True
    )
    лучшие = [т for сумма, т in с_оценкой[:3] if сумма > 0]
    return прошлые, лучшие


# ======================================================================
#                 Черновики: создать, опубликовать, переписать
# ======================================================================

async def сделать_черновик(context, *, заметка: str | None = None, post_id: int | None = None,
                           правки: str | None = None, редактируемое_сообщение=None,
                           медиа: tuple[str, str] | None = None) -> int | None:
    """
    Просит Claude написать пост (или переписать черновик) и показывает его вам.
    Если режим «авто» — сразу публикует.
    """
    прошлые, лучшие = _контекст_для_ии()
    черновик = db.получить_пост(post_id)["text"] if post_id else None
    try:
        текст = await asyncio.to_thread(
            ai.написать_пост, заметка=заметка, прошлые_посты=прошлые, лучшие_посты=лучшие,
            правки=правки, черновик=черновик,
        )
    except (ai.ЛимитИсчерпан, ai.ОшибкаИИ) as e:
        await _написать_владельцу(context, f"⚠️ {e}")
        return None
    текст = текст[:4000]

    if post_id is None:
        post_id = db.добавить_черновик(текст)
        if медиа:
            db.прикрепить_медиа(post_id, *медиа)
    else:
        db.обновить_текст(post_id, текст)

    режим = config.загрузить_настройки()["режим_публикации"]
    if режим == "авто" and редактируемое_сообщение is None:
        await опубликовать(context, post_id)
        await _написать_владельцу(context, f"🤖 Опубликовал сам (режим «авто»):\n\n{текст}")
        return post_id

    if редактируемое_сообщение is not None:
        await редактируемое_сообщение.edit_text(
            _текст_черновика(post_id, текст), reply_markup=_кнопки_черновика(post_id)
        )
    else:
        сообщение = await _написать_владельцу(
            context, _текст_черновика(post_id, текст), reply_markup=_кнопки_черновика(post_id)
        )
        db.запомнить_сообщение_черновика(post_id, сообщение.message_id)
    return post_id


async def _отправить_медиа(bot, chat_id, тип: str, file_id: str, подпись: str | None):
    """Отправляет видео/фото/GIF/файл по его номеру на серверах Telegram (заново ничего не загружаем)."""
    способ = getattr(bot, {"video": "send_video", "photo": "send_photo",
                           "animation": "send_animation", "document": "send_document"}[тип])
    return await способ(chat_id, file_id, caption=подпись)


async def опубликовать(context, post_id: int) -> None:
    """Отправляет пост в канал (с видео/фото, если прикреплено) и запоминает номер сообщения."""
    пост = db.получить_пост(post_id)
    if not пост["media_type"]:
        сообщение = await context.bot.send_message(_канал(), пост["text"])
    elif len(пост["text"]) <= ЛИМИТ_ПОДПИСИ:
        # Обычный случай: видео и текст под ним одним сообщением.
        сообщение = await _отправить_медиа(context.bot, _канал(), пост["media_type"], пост["media_file_id"], пост["text"])
    else:
        # Текст длиннее, чем помещается в подпись: сначала видео, сразу за ним — текст.
        await _отправить_медиа(context.bot, _канал(), пост["media_type"], пост["media_file_id"], None)
        сообщение = await context.bot.send_message(_канал(), пост["text"])
    db.отметить_опубликованным(post_id, сообщение.message_id)


# Черновики, с которыми бот прямо сейчас что-то делает (защита от двойного нажатия).
_в_работе: set[int] = set()


async def нажатие_кнопки_черновика(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != config.OWNER_ID:
        await q.answer()
        return
    действие, post_id, *вариант = q.data.split(":")
    post_id = int(post_id)
    пост = db.получить_пост(post_id)
    # В каком состоянии пост должен быть, чтобы кнопка имела смысл
    можно_когда = {"pub": ("draft", "scheduled"), "rej": ("draft", "scheduled"), "unsch": ("scheduled",)}
    if not пост or пост["status"] not in можно_когда.get(действие, ("draft",)):
        await q.answer("Этот черновик уже обработан.", show_alert=True)
        return
    if post_id in _в_работе:
        await q.answer("Уже выполняю, подождите немного…")
        return

    _в_работе.add(post_id)
    try:
        if действие == "pub":
            await q.answer("Публикую…")  # отвечаем на нажатие сразу, чтобы кнопка «отжалась»
            try:
                await опубликовать(context, post_id)
            except Exception as e:  # чаще всего — бот не админ канала, неверный CHANNEL_ID или медленная сеть
                log.error("Не удалось опубликовать: %s: %s", type(e).__name__, str(e)[:200])
                await _написать_владельцу(
                    context,
                    f"⚠️ Не получилось опубликовать черновик №{post_id} ({type(e).__name__}).\n"
                    "Проверьте, что бот — администратор канала с правом публикации, и нажмите кнопку ещё раз.",
                )
                return
            await q.edit_message_text(f"{пост['text']}\n\n— — —\n✅ Опубликовано в канале.")

        elif действие == "rej":
            db.отметить_отклонённым(post_id)
            await q.answer("Отклонено.")
            await q.edit_message_text(f"{пост['text']}\n\n— — —\n❌ Отклонено.")

        elif действие == "rew":
            await q.answer("Переписываю… это займёт 1–2 минуты.")
            await сделать_черновик(context, post_id=post_id, редактируемое_сообщение=q.message)

        elif действие == "nomedia":
            db.прикрепить_медиа(post_id, None, None)
            await q.answer("Убрал вложение.")
            await q.edit_message_text(_текст_черновика(post_id, пост["text"]),
                                      reply_markup=_кнопки_черновика(post_id))

        elif действие == "later":
            await q.answer("Когда опубликовать?")
            await q.edit_message_reply_markup(_кнопки_времени(post_id))

        elif действие == "back":
            await q.answer()
            await q.edit_message_reply_markup(_кнопки_черновика(post_id))

        elif действие == "at" and вариант == ["own"]:
            context.user_data["ждём_время_для"] = post_id
            await q.answer()
            await _написать_владельцу(context, ПОДСКАЗКА_ВРЕМЕНИ)

        elif действие == "at" and вариант and вариант[0] in ВАРИАНТЫ_ВРЕМЕНИ:
            когда, _ = ВАРИАНТЫ_ВРЕМЕНИ[вариант[0]]()
            db.запланировать(post_id, когда)
            await q.answer(f"Запланировано на {_когда_по_местному(когда)}")
            await q.edit_message_text(
                _текст_запланированного(post_id, пост["text"], когда),
                reply_markup=_кнопки_запланированного(post_id),
            )

        elif действие == "unsch":
            db.снять_с_расписания(post_id)
            await q.answer("Отправка отменена — это снова черновик.")
            await q.edit_message_text(_текст_черновика(post_id, пост["text"]),
                                      reply_markup=_кнопки_черновика(post_id))
    finally:
        _в_работе.discard(post_id)


# ======================================================================
#                              Команды
# ======================================================================

СПРАВКА = (
    "Я помогаю вести ваш канал. Что умею:\n\n"
    "• Просто напишите мне заметку или тему — я сделаю по ней черновик поста.\n"
    "• /post — черновик на свободную тему (или по вашей сохранённой идее).\n"
    "• /idea текст — сохранить идею/заметку на будущее (возьму её для ближайшего поста).\n"
    "• /ideas — список сохранённых идей.\n"
    "• /stats — как зашли посты за неделю (реакции и комментарии).\n"
    "• /report — недельный отчёт с идеями прямо сейчас.\n"
    "• /report_all — отчёт за всё время ведения канала.\n"
    "• /scheduled — запланированные посты (кнопка «⏰ Позже» под черновиком).\n"
    "• /limit — сколько потрачено на ИИ.\n"
    "• /mode — режим публикации (с одобрением или авто).\n"
    "• /replies — предлагать ли ответы на комментарии.\n\n"
    "Расписание и стиль постов лежат в файле settings.json."
)


async def команда_старт(update, context):
    if not await _только_владелец(update):
        return
    await update.message.reply_text("Привет! 👋\n\n" + СПРАВКА)


async def команда_пост(update, context):
    if not await _только_владелец(update):
        return
    заметка = " ".join(context.args).strip() or None
    if заметка is None:
        идея = db.взять_идею()
        заметка = идея["text"] if идея else None
    await update.message.reply_text("Пишу черновик, минутку… ✍️")
    await сделать_черновик(context, заметка=заметка)


async def команда_идея(update, context):
    if not await _только_владелец(update):
        return
    текст = " ".join(context.args).strip()
    if not текст:
        await update.message.reply_text("Напишите идею после команды, например:\n/idea снял первый мультик про кота")
        return
    db.добавить_идею(текст)
    await update.message.reply_text("Записал! Возьму её для ближайшего поста. 👍")


async def команда_идеи(update, context):
    if not await _только_владелец(update):
        return
    идеи = db.неиспользованные_идеи()
    if not идеи:
        await update.message.reply_text("Сохранённых идей пока нет. Добавить: /idea текст")
        return
    await update.message.reply_text("Ваши идеи в очереди:\n\n" + "\n".join(f"{i+1}. {x['text']}" for i, x in enumerate(идеи)))


def _сводка_постов(дней: int) -> str:
    посты = db.посты_за_дни(дней)
    if not посты:
        return ""
    строки = []
    for п in посты:
        реакции = db.реакции_поста(п["channel_msg_id"])
        комментариев = len(db.комментарии_поста(п["channel_msg_id"], 1000))
        r = " ".join(f"{e}{n}" for e, n in реакции.items()) or "реакций нет"
        строки.append(f"• «{п['text'][:50].strip()}…» — {r}; комментариев: {комментариев}")
    return "\n".join(строки)


async def команда_статистика(update, context):
    if not await _только_владелец(update):
        return
    сводка = _сводка_постов(7)
    await update.message.reply_text(
        "Посты за 7 дней:\n\n" + сводка if сводка else "За последние 7 дней постов пока нет."
    )


def _данные_для_отчёта(дней: int) -> str:
    блоки = []
    for п in db.посты_за_дни(дней):
        реакции = db.реакции_поста(п["channel_msg_id"])
        комментарии = [к["text"][:200] for к in db.комментарии_поста(п["channel_msg_id"], 5)]
        блоки.append(
            f"ПОСТ: {п['text'][:700]}\n"
            f"Реакции: {', '.join(f'{e} x{n}' for e, n in реакции.items()) or 'нет'}\n"
            f"Комментарии: {' | '.join(комментарии) or 'нет'}"
        )
    return "\n\n".join(блоки)


async def отправить_отчёт(context: ContextTypes.DEFAULT_TYPE, *, молча_если_пусто: bool = False):
    """Недельный отчёт (запускается по расписанию и по команде /report)."""
    данные = _данные_для_отчёта(7)
    if not данные:
        if not молча_если_пусто:
            await _написать_владельцу(context, "📊 За эту неделю в канале не было постов, отчёт строить не из чего.")
        db.записать_отметку("последний_отчёт")
        return
    try:
        отчёт = await asyncio.to_thread(ai.подготовить_отчёт, данные)
    except (ai.ЛимитИсчерпан, ai.ОшибкаИИ) as e:
        await _написать_владельцу(context, f"⚠️ {e}")
        return
    await _написать_владельцу(context, "📊 Отчёт за неделю\n\n" + отчёт)
    db.записать_отметку("последний_отчёт")


async def команда_отчёт(update, context):
    if not await _только_владелец(update):
        return
    await update.message.reply_text("Готовлю отчёт… 📊")
    await отправить_отчёт(context)


ДНИ_ПО_РУССКИ = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
МИНИМУМ_ПОСТОВ_ДЛЯ_ОТЧЁТА = 3
САМЫХ_ЗАМЕТНЫХ = 10  # столько лучших и худших постов отдаём ИИ целиком


def _данные_за_всё_время() -> tuple[int, str]:
    """Сводка по всем постам: общие цифры + список постов + тексты лучших и худших."""
    посты = db.все_опубликованные()
    if not посты:
        return 0, ""
    пояс = ZoneInfo(config.загрузить_настройки()["расписание"]["часовой_пояс"])
    строки, оценённые = [], []
    всего_реакций = всего_комментариев = 0
    for п in посты:
        реакции = db.реакции_поста(п["channel_msg_id"])
        комментарии = db.комментарии_поста(п["channel_msg_id"], 1000)
        сумма = sum(реакции.values())
        всего_реакций += сумма
        всего_комментариев += len(комментарии)
        когда = datetime.fromisoformat(п["published_at"]).astimezone(пояс)
        строки.append(
            f"{когда:%d.%m.%Y} {ДНИ_ПО_РУССКИ[когда.weekday()]} {когда:%H:%M} | {len(п['text'])} знаков | "
            f"реакций {сумма} | комментариев {len(комментарии)} | «{п['text'][:80].strip()}…»"
        )
        оценённые.append((сумма + len(комментарии), п, реакции, комментарии))

    первый = datetime.fromisoformat(посты[0]["published_at"]).astimezone(пояс)
    итог = [
        f"Постов: {len(посты)} (с {первый:%d.%m.%Y}). Всего реакций: {всего_реакций}, "
        f"комментариев: {всего_комментариев}. В среднем на пост: "
        f"{всего_реакций / len(посты):.1f} реакций, {всего_комментариев / len(посты):.1f} комментариев.",
        "",
        "Все посты (дата, день, время, длина, реакции, комментарии, начало текста):",
        *строки[-150:],  # если постов очень много — берём последние 150, чтобы не тратить лишнего
    ]
    оценённые.sort(key=lambda x: x[0], reverse=True)
    for заголовок, выборка in (("ЛУЧШИЕ ПОСТЫ", оценённые[:САМЫХ_ЗАМЕТНЫХ]),
                               ("ХУДШИЕ ПОСТЫ", оценённые[-САМЫХ_ЗАМЕТНЫХ:][::-1])):
        if len(оценённые) <= САМЫХ_ЗАМЕТНЫХ and заголовок == "ХУДШИЕ ПОСТЫ":
            break  # постов мало — все они уже есть в списке выше
        итог += ["", f"{заголовок}:"]
        for _, п, реакции, комментарии in выборка:
            итог.append(
                f"ПОСТ: {п['text'][:400]}\n"
                f"Реакции: {', '.join(f'{e} x{n}' for e, n in реакции.items()) or 'нет'}\n"
                f"Комментарии: {' | '.join(к['text'][:150] for к in комментарии[:3]) or 'нет'}"
            )
    return len(посты), "\n".join(итог)


async def команда_отчёт_за_всё(update, context):
    if not await _только_владелец(update):
        return
    сколько, данные = _данные_за_всё_время()
    if сколько < МИНИМУМ_ПОСТОВ_ДЛЯ_ОТЧЁТА:
        await update.message.reply_text(
            f"Пока я знаю о {сколько} опубликованных постах — для отчёта за всё время нужно хотя бы "
            f"{МИНИМУМ_ПОСТОВ_ДЛЯ_ОТЧЁТА}. Я вижу посты только с момента подключения к каналу; "
            "загляните сюда через пару недель. 🙂"
        )
        return
    await update.message.reply_text("Готовлю отчёт за всё время, это займёт 1–2 минуты… 📈")
    try:
        отчёт = await asyncio.to_thread(ai.подготовить_отчёт_за_всё_время, данные)
    except (ai.ЛимитИсчерпан, ai.ОшибкаИИ) as e:
        await _написать_владельцу(context, f"⚠️ {e}")
        return
    await _написать_владельцу(context, "📈 Отчёт за всё время\n\n" + отчёт)


async def команда_лимит(update, context):
    if not await _только_владелец(update):
        return
    л = config.загрузить_настройки()["лимиты"]
    запросов, доллары = db.расход_за_сегодня()
    await update.message.reply_text(
        f"Сегодня: {запросов} из {л['запросов_в_сутки']} запросов, "
        f"${доллары:.3f} из ${л['долларов_в_сутки']:.2f}.\n"
        f"За 30 дней потрачено: ${db.расход_за_30_дней():.2f}."
    )


async def команда_режим(update, context):
    if not await _только_владелец(update):
        return
    настройки = config.загрузить_настройки()
    аргумент = " ".join(context.args).strip().lower()
    if аргумент in ("авто", "auto"):
        настройки["режим_публикации"] = "авто"
    elif аргумент in ("одобрение", "с_одобрением", "approval"):
        настройки["режим_публикации"] = "с_одобрением"
    elif аргумент:
        await update.message.reply_text("Не понял. Варианты: /mode авто  или  /mode одобрение")
        return
    config.сохранить_настройки(настройки)
    текущий = настройки["режим_публикации"]
    пояснение = ("бот публикует сам, без вашего нажатия" if текущий == "авто"
                 else "бот присылает черновик, публикует только после вашей кнопки")
    await update.message.reply_text(
        f"Режим сейчас: «{текущий}» — {пояснение}.\nСменить: /mode авто  или  /mode одобрение"
    )


async def команда_ответы(update, context):
    if not await _только_владелец(update):
        return
    настройки = config.загрузить_настройки()
    аргумент = " ".join(context.args).strip().lower()
    if аргумент in ("вкл", "on"):
        настройки["ответы_на_комментарии"] = True
    elif аргумент in ("выкл", "off"):
        настройки["ответы_на_комментарии"] = False
    elif аргумент:
        await update.message.reply_text("Не понял. Варианты: /replies вкл  или  /replies выкл")
        return
    config.сохранить_настройки(настройки)
    состояние = "включены" if настройки["ответы_на_комментарии"] else "выключены"
    await update.message.reply_text(
        f"Предложения ответов на комментарии сейчас {состояние}.\nСменить: /replies вкл  или  /replies выкл"
    )


async def обычное_сообщение(update, context):
    """Любой обычный текст от вас: либо правки к черновику (ответом), либо тема для нового поста."""
    if not await _только_владелец(update):
        return
    сообщение = update.message
    if not сообщение.text:
        return

    # Вы нажали «Своё время» — значит, это сообщение и есть время публикации.
    post_id = context.user_data.get("ждём_время_для")
    if post_id is not None:
        if сообщение.text.strip().lower() in ("отмена", "отменить", "стоп"):
            context.user_data.pop("ждём_время_для", None)
            await сообщение.reply_text("Хорошо, не планирую. Черновик остался с кнопками.")
            return
        когда, ошибка = разобрать_время(сообщение.text)
        if когда is None:
            await сообщение.reply_text(f"{ошибка}\n\n{ПОДСКАЗКА_ВРЕМЕНИ}")
            return
        context.user_data.pop("ждём_время_для", None)
        пост = db.получить_пост(post_id)
        if not пост or пост["status"] != "draft":
            await сообщение.reply_text("Этот черновик уже обработан.")
            return
        db.запланировать(post_id, когда)
        текст = _текст_запланированного(post_id, пост["text"], когда)
        try:
            await context.bot.edit_message_text(
                текст, chat_id=config.OWNER_ID, message_id=пост["draft_chat_msg_id"],
                reply_markup=_кнопки_запланированного(post_id),
            )
        except Exception:
            # Старое сообщение не отредактировать (например, удалено) — пришлём новое.
            новое = await _написать_владельцу(context, текст, reply_markup=_кнопки_запланированного(post_id))
            db.запомнить_сообщение_черновика(post_id, новое.message_id)
        await сообщение.reply_text(f"⏰ Готово! Опубликую {_когда_по_местному(когда)}.")
        return

    if сообщение.reply_to_message:
        пост = db.пост_по_сообщению_черновика(сообщение.reply_to_message.message_id)
        if пост:
            await сообщение.reply_text("Переделываю по вашим правкам… ✍️")
            await сделать_черновик(
                context, post_id=пост["id"], правки=сообщение.text,
                редактируемое_сообщение=сообщение.reply_to_message,
            )
            return
    await сообщение.reply_text("Принял тему, пишу черновик… ✍️")
    await сделать_черновик(context, заметка=сообщение.text)


async def медиа_сообщение(update, context):
    """
    Вы прислали видео/фото/GIF/файл:
      • ответом на черновик — прикрепляем к этому черновику (подпись = правки к тексту);
      • с подписью, но не ответом — пишем по подписи новый пост и прикрепляем к нему;
      • без подписи и не ответом — прикрепляем к самому свежему черновику.
    """
    if not await _только_владелец(update):
        return
    м = update.message
    if м.video:
        тип, file_id = "video", м.video.file_id
    elif м.photo:
        тип, file_id = "photo", м.photo[-1].file_id  # последняя — самого лучшего качества
    elif м.animation:
        тип, file_id = "animation", м.animation.file_id
    elif м.document:
        тип, file_id = "document", м.document.file_id
    else:
        return
    название = НАЗВАНИЯ_МЕДИА[тип]
    подпись = (м.caption or "").strip()

    # 1) Ответ на черновик
    if м.reply_to_message:
        пост = db.пост_по_сообщению_черновика(м.reply_to_message.message_id)
        if not пост:
            await м.reply_text("Это сообщение не черновик (или он уже опубликован). Ответьте видео на актуальный черновик.")
            return
        db.прикрепить_медиа(пост["id"], тип, file_id)
        if подпись:
            await м.reply_text(f"Прикрепил {название} и переделываю текст по вашей подписи… ✍️")
            await сделать_черновик(context, post_id=пост["id"], правки=подпись,
                                   редактируемое_сообщение=м.reply_to_message)
        else:
            await _обновить_черновик(context, пост["id"])
            await м.reply_text(f"📎 Прикрепил {название} к черновику №{пост['id']}.")
        return

    # 2) С подписью — новый пост по подписи
    if подпись:
        await м.reply_text(f"Пишу пост по вашей подписи и прикреплю {название}… ✍️")
        await сделать_черновик(context, заметка=подпись, медиа=(тип, file_id))
        return

    # 3) Без подписи — к самому свежему черновику
    пост = db.последний_черновик()
    if not пост:
        await м.reply_text(
            f"Не к чему прикрепить {название}: черновиков нет.\n"
            "Отправьте его с подписью — я напишу по ней пост, или сначала сделайте черновик командой /post."
        )
        return
    db.прикрепить_медиа(пост["id"], тип, file_id)
    await _обновить_черновик(context, пост["id"])
    await м.reply_text(
        f"📎 Прикрепил {название} к последнему черновику №{пост['id']} "
        f"(«{пост['text'][:40].strip()}…»).\nЕсли не туда — нажмите «🗑 Убрать» под черновиком."
    )


async def _обновить_черновик(context, post_id: int) -> None:
    """Перерисовывает сообщение с черновиком (текст + кнопки), например после прикрепления видео."""
    пост = db.получить_пост(post_id)
    if пост["status"] == "scheduled":
        текст = _текст_запланированного(post_id, пост["text"], пост["scheduled_at"])
        кнопки = _кнопки_запланированного(post_id)
    else:
        текст, кнопки = _текст_черновика(post_id, пост["text"]), _кнопки_черновика(post_id)
    try:
        await context.bot.edit_message_text(текст, chat_id=config.OWNER_ID,
                                            message_id=пост["draft_chat_msg_id"], reply_markup=кнопки)
    except Exception:
        новое = await _написать_владельцу(context, текст, reply_markup=кнопки)
        db.запомнить_сообщение_черновика(post_id, новое.message_id)


# ======================================================================
#          Слежение за каналом: посты, реакции, комментарии
# ======================================================================

async def новый_пост_в_канале(update: Update, context):
    """Пост, выложенный в канал не через бота (вами вручную) — запоминаем для отчётов."""
    пост = update.channel_post
    if not _это_наш_канал(пост.chat):
        return
    db.записать_пост_из_канала(пост.message_id, пост.text or пост.caption or "[пост без текста]")


async def реакции_обновились(update: Update, context):
    """Telegram присылает общее число реакций по посту (нужен админ-доступ бота к каналу)."""
    r = update.message_reaction_count
    if r is None or not _это_наш_канал(r.chat):
        return
    итог: dict[str, int] = {}
    for реакция in r.reactions:
        if isinstance(реакция.type, ReactionTypeEmoji):
            ключ = реакция.type.emoji
        else:
            ключ = "⭐"  # платные и кастомные реакции складываем в одну «звёздочку»
        итог[ключ] = итог.get(ключ, 0) + реакция.total_count
    db.сохранить_реакции(r.message_id, итог)


async def сообщение_в_обсуждении(update: Update, context):
    """Комментарии к постам приходят как сообщения в чате обсуждения канала."""
    m = update.effective_message
    if m is None:
        return
    if config.DISCUSSION_CHAT_ID and m.chat_id != config.DISCUSSION_CHAT_ID:
        return

    # Сам пост канала, автоматически пересланный в чат обсуждения — это «корень» ветки комментариев.
    if m.is_automatic_forward:
        origin = m.forward_origin
        if origin is not None and getattr(origin, "message_id", None):
            db.запомнить_корень_обсуждения(m.message_id, origin.message_id)
        return

    # К какому посту относится комментарий?
    if m.reply_to_message is None:
        return
    channel_msg_id = db.пост_канала_для_сообщения_чата(m.reply_to_message.message_id)
    if channel_msg_id is None and m.reply_to_message.is_automatic_forward:
        origin = m.reply_to_message.forward_origin
        channel_msg_id = getattr(origin, "message_id", None)
        if channel_msg_id:
            db.запомнить_корень_обсуждения(m.reply_to_message.message_id, channel_msg_id)
    if channel_msg_id is None:
        return

    текст = m.text or m.caption
    if not текст:
        return
    от_владельца = m.from_user is not None and m.from_user.id == config.OWNER_ID
    автор = "автор канала" if от_владельца else (m.from_user.first_name if m.from_user else "аноним")
    comment_id = db.добавить_комментарий(channel_msg_id, m.chat_id, m.message_id, автор, текст)
    if comment_id is None or от_владельца:
        return

    if config.загрузить_настройки()["ответы_на_комментарии"]:
        await предложить_ответ(context, comment_id)


async def предложить_ответ(context, comment_id: int):
    """Просит Claude набросать ответ и присылает вам с кнопками (сам ничего не публикует)."""
    к = db.получить_комментарий(comment_id)
    пост = db.пост_по_сообщению_канала(к["channel_msg_id"])
    try:
        ответ = await asyncio.to_thread(
            ai.предложить_ответ,
            текст_поста=пост["text"] if пост else "(текст поста неизвестен)",
            комментарий=к["text"],
        )
    except (ai.ЛимитИсчерпан, ai.ОшибкаИИ) as e:
        log.info("Ответ на комментарий не предложен: %s", e)
        return
    if ответ.strip().upper().startswith("ПРОПУСТИТЬ"):
        db.обновить_комментарий(comment_id, status="skipped")
        return
    db.обновить_комментарий(comment_id, suggestion=ответ)
    кнопки = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ответить", callback_data=f"crp:{comment_id}"),
        InlineKeyboardButton("❌ Не отвечать", callback_data=f"crj:{comment_id}"),
    ]])
    await _написать_владельцу(
        context,
        f"💬 Новый комментарий ({к['author']}):\n«{к['text'][:500]}»\n\nМой вариант ответа:\n{ответ}",
        reply_markup=кнопки,
    )


async def нажатие_кнопки_ответа(update: Update, context):
    q = update.callback_query
    if q.from_user.id != config.OWNER_ID:
        await q.answer()
        return
    действие, comment_id = q.data.split(":")
    к = db.получить_комментарий(int(comment_id))
    if not к or к["status"] != "new":
        await q.answer("Уже обработано.", show_alert=True)
        return
    if действие == "crp":
        try:
            await context.bot.send_message(
                к["group_chat_id"], к["suggestion"], reply_to_message_id=к["group_msg_id"]
            )
        except Exception as e:
            log.error("Не удалось ответить на комментарий: %s", type(e).__name__)
            await q.answer("Не получилось отправить ответ. Бот должен быть участником чата обсуждения.", show_alert=True)
            return
        db.обновить_комментарий(к["id"], status="replied")
        await q.answer("Ответ отправлен!")
        await q.edit_message_text(q.message.text + "\n\n✅ Ответ отправлен.")
    else:
        db.обновить_комментарий(к["id"], status="skipped")
        await q.answer("Ок, не отвечаем.")
        await q.edit_message_text(q.message.text + "\n\n❌ Не отвечаем.")


# ======================================================================
#                         Задания по расписанию
# ======================================================================

async def задание_черновик(context: ContextTypes.DEFAULT_TYPE):
    """Срабатывает в дни и время из settings.json: готовит пост."""
    идея = db.взять_идею()
    await сделать_черновик(context, заметка=идея["text"] if идея else None)


async def задание_отчёт(context: ContextTypes.DEFAULT_TYPE):
    await отправить_отчёт(context)


ОПОЗДАНИЕ_ДЛЯ_АВТОПУБЛИКАЦИИ = timedelta(hours=12)  # опоздали сильнее — сначала спросим вас


async def задание_отложенные(context: ContextTypes.DEFAULT_TYPE):
    """Каждую минуту: публикуем запланированные посты, время которых наступило."""
    сейчас = datetime.now(timezone.utc)
    for пост in db.пора_публиковать():
        post_id = пост["id"]
        if post_id in _в_работе:
            continue
        _в_работе.add(post_id)
        try:
            опоздание = сейчас - datetime.fromisoformat(пост["scheduled_at"])
            if опоздание > ОПОЗДАНИЕ_ДЛЯ_АВТОПУБЛИКАЦИИ:
                # Mac был выключен слишком долго — публиковать молча не будем, спросим вас.
                db.снять_с_расписания(post_id)
                сообщение = await _написать_владельцу(
                    context,
                    f"⏰ Этот пост был запланирован на {_когда_по_местному(пост['scheduled_at'])}, "
                    "но компьютер был выключен. Что делаем?\n\n" + _текст_черновика(post_id, пост["text"]),
                    reply_markup=_кнопки_черновика(post_id),
                )
                db.запомнить_сообщение_черновика(post_id, сообщение.message_id)
                continue
            try:
                await опубликовать(context, post_id)
            except Exception as e:  # сеть моргнула — попробуем через минуту
                log.error("Отложенная публикация не удалась: %s: %s", type(e).__name__, str(e)[:200])
                continue
            try:
                await context.bot.edit_message_text(
                    f"{пост['text']}\n\n— — —\n✅ Опубликовано по расписанию "
                    f"({_когда_по_местному(пост['scheduled_at'])}).",
                    chat_id=config.OWNER_ID, message_id=пост["draft_chat_msg_id"],
                )
            except Exception:
                pass
            await _написать_владельцу(context, f"✅ Опубликовал запланированный пост: «{пост['text'][:60].strip()}…»")
        finally:
            _в_работе.discard(post_id)


async def команда_запланированные(update, context):
    if not await _только_владелец(update):
        return
    посты = db.запланированные()
    if not посты:
        await update.message.reply_text(
            "Запланированных постов нет. Чтобы запланировать, нажмите «⏰ Позже» под черновиком."
        )
        return
    await update.message.reply_text(f"Запланировано постов: {len(посты)}")
    for п in посты[:20]:
        await update.message.reply_text(
            f"⏰ {_когда_по_местному(п['scheduled_at'])}\n\n{п['text'][:300]}",
            reply_markup=_кнопки_запланированного(п["id"]),
        )


ДОГОНЯЕМ_ПРОПУЩЕННОЕ_ДО = timedelta(hours=30)  # если срок был не дольше этого назад — догоняем


async def догнать_пропущенное(context: ContextTypes.DEFAULT_TYPE):
    """
    Вы включили компьютер позже назначенного времени? Тогда сразу после запуска бот проверяет,
    не пропустил ли он недавний срок (черновик или отчёт), и если да — делает это сейчас.
    """
    настройки = config.загрузить_настройки()
    р, о = настройки["расписание"], настройки["еженедельный_отчёт"]
    сейчас = datetime.now(timezone.utc)

    срок = config.последний_срок(
        config.дни_из_списка(р["дни"]), config.время_из_строки(р["время"], р["часовой_пояс"])
    )
    последний = db.последний_черновик_бота()
    if срок and сейчас - срок <= ДОГОНЯЕМ_ПРОПУЩЕННОЕ_ДО and (последний is None or последний < срок):
        await _написать_владельцу(context, "Компьютер был выключен в назначенное время — готовлю пропущенный черновик.")
        await задание_черновик(context)

    срок = config.последний_срок(
        config.дни_из_списка([о["день"]]), config.время_из_строки(о["время"], р["часовой_пояс"])
    )
    отчёт = db.отметка("последний_отчёт")
    if срок and сейчас - срок <= ДОГОНЯЕМ_ПРОПУЩЕННОЕ_ДО and (отчёт is None or отчёт < срок):
        await отправить_отчёт(context, молча_если_пусто=True)


async def ошибка(update, context: ContextTypes.DEFAULT_TYPE):
    """Любая непредвиденная ошибка: пишем в журнал только тип ошибки (без секретов)."""
    log.error("Ошибка в боте: %s: %s", type(context.error).__name__, str(context.error)[:200])
    # Если ошибка случилась при вашем действии (а не просто сеть моргнула) — коротко сообщаем вам.
    if update is not None and not isinstance(context.error, NetworkError) and config.OWNER_ID:
        try:
            await _написать_владельцу(context, f"⚠️ Что-то пошло не так: {type(context.error).__name__}. Попробуйте ещё раз.")
        except Exception:
            pass


# ======================================================================
#                               Запуск
# ======================================================================

async def после_запуска(app: Application):
    """Показываем список команд в меню Telegram."""
    await app.bot.set_my_commands([
        BotCommand("post", "Черновик поста сейчас"),
        BotCommand("idea", "Сохранить идею на будущее"),
        BotCommand("ideas", "Список идей"),
        BotCommand("stats", "Как зашли посты за неделю"),
        BotCommand("report", "Недельный отчёт сейчас"),
        BotCommand("report_all", "Отчёт за всё время"),
        BotCommand("scheduled", "Запланированные посты"),
        BotCommand("limit", "Сколько потрачено на ИИ"),
        BotCommand("mode", "Режим: с одобрением или авто"),
        BotCommand("replies", "Ответы на комментарии: вкл/выкл"),
        BotCommand("help", "Что я умею"),
    ])


def собрать_приложение() -> Application:
    config.проверить_секреты()
    db.создать_таблицы()
    настройки = config.загрузить_настройки()

    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(после_запуска)
        # Обрабатываем сообщения и кнопки параллельно: пока ИИ думает, кнопки продолжают работать.
        .concurrent_updates(True)
        # Связь с Telegram бывает медленной — ждём ответа подольше, чтобы не ловить «тайм-аут».
        .connect_timeout(30).read_timeout(60).write_timeout(60).pool_timeout(30)
        .get_updates_connect_timeout(30).get_updates_read_timeout(60)
        .build()
    )

    # Команды (только для владельца — проверка внутри каждой)
    for имя, функция in [
        ("start", команда_старт), ("help", команда_старт), ("post", команда_пост),
        ("idea", команда_идея), ("ideas", команда_идеи), ("stats", команда_статистика),
        ("report", команда_отчёт), ("report_all", команда_отчёт_за_всё),
        ("scheduled", команда_запланированные), ("limit", команда_лимит), ("mode", команда_режим),
        ("replies", команда_ответы),
    ]:
        app.add_handler(CommandHandler(имя, функция))

    # Кнопки
    app.add_handler(CallbackQueryHandler(нажатие_кнопки_черновика, pattern=r"^((pub|rew|rej|later|back|unsch|nomedia):\d+|at:\d+:\w+)$"))
    app.add_handler(CallbackQueryHandler(нажатие_кнопки_ответа, pattern=r"^(crp|crj):\d+$"))

    # Обычные сообщения вам в личку
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, обычное_сообщение))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & (filters.VIDEO | filters.PHOTO | filters.ANIMATION | filters.Document.ALL),
        медиа_сообщение,
    ))

    # Слежение за каналом и обсуждением
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, новый_пост_в_канале))
    app.add_handler(MessageReactionHandler(
        реакции_обновились, message_reaction_types=MessageReactionHandler.MESSAGE_REACTION_COUNT_UPDATED
    ))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, сообщение_в_обсуждении))
    app.add_error_handler(ошибка)

    # Расписание
    р = настройки["расписание"]
    app.job_queue.run_daily(
        задание_черновик, config.время_из_строки(р["время"], р["часовой_пояс"]),
        days=config.дни_из_списка(р["дни"]), name="черновик",
    )
    о = настройки["еженедельный_отчёт"]
    app.job_queue.run_daily(
        задание_отчёт, config.время_из_строки(о["время"], р["часовой_пояс"]),
        days=config.дни_из_списка([о["день"]]), name="отчёт",
    )
    # Раз в минуту проверяем отложенные посты (первый раз — через 15 секунд после запуска).
    app.job_queue.run_repeating(задание_отложенные, interval=60, first=15, name="отложенные")
    # Через 20 секунд после запуска проверяем, не пропустили ли мы срок, пока компьютер был выключен.
    if config.OWNER_ID:
        app.job_queue.run_once(догнать_пропущенное, when=20, name="догнать")
    return app


def main():
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
    # ВАЖНО: эти библиотеки пишут в журнал адреса запросов, а в них зашит токен бота. Глушим.
    for шумная in ("httpx", "httpcore", "telegram", "apscheduler"):
        logging.getLogger(шумная).setLevel(logging.WARNING)

    app = собрать_приложение()
    log.info("Бот запущен. Остановить: Ctrl+C.")
    # Явно просим Telegram присылать реакции (по умолчанию они отключены).
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
