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

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReactionTypeEmoji,
    Update,
)
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
        [InlineKeyboardButton("❌ Отклонить", callback_data=f"rej:{post_id}")],
    ])


def _текст_черновика(post_id: int, текст: str) -> str:
    return (
        f"{текст}\n\n— — —\nЧерновик №{post_id}. Нажмите кнопку ниже. "
        "Чтобы поправить, ответьте на это сообщение, что изменить."
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
                           правки: str | None = None, редактируемое_сообщение=None) -> int | None:
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


async def опубликовать(context, post_id: int) -> None:
    """Отправляет текст черновика в канал и запоминает номер сообщения."""
    пост = db.получить_пост(post_id)
    сообщение = await context.bot.send_message(_канал(), пост["text"])
    db.отметить_опубликованным(post_id, сообщение.message_id)


async def нажатие_кнопки_черновика(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != config.OWNER_ID:
        await q.answer()
        return
    действие, post_id = q.data.split(":")
    post_id = int(post_id)
    пост = db.получить_пост(post_id)
    if not пост or пост["status"] != "draft":
        await q.answer("Этот черновик уже обработан.", show_alert=True)
        return

    if действие == "pub":
        try:
            await опубликовать(context, post_id)
        except Exception as e:  # чаще всего — бот не админ канала или неверный CHANNEL_ID
            log.error("Не удалось опубликовать: %s", type(e).__name__)
            await q.answer("Не получилось опубликовать. Проверьте, что бот — админ канала.", show_alert=True)
            return
        await q.answer("Опубликовано!")
        await q.edit_message_text(f"{пост['text']}\n\n— — —\n✅ Опубликовано в канале.")

    elif действие == "rej":
        db.отметить_отклонённым(post_id)
        await q.answer("Отклонено.")
        await q.edit_message_text(f"{пост['text']}\n\n— — —\n❌ Отклонено.")

    elif действие == "rew":
        await q.answer("Переписываю…")
        await сделать_черновик(context, post_id=post_id, редактируемое_сообщение=q.message)


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


async def отправить_отчёт(context: ContextTypes.DEFAULT_TYPE):
    """Недельный отчёт (запускается по расписанию и по команде /report)."""
    данные = _данные_для_отчёта(7)
    if not данные:
        await _написать_владельцу(context, "📊 За эту неделю в канале не было постов, отчёт строить не из чего.")
        return
    try:
        отчёт = await asyncio.to_thread(ai.подготовить_отчёт, данные)
    except (ai.ЛимитИсчерпан, ai.ОшибкаИИ) as e:
        await _написать_владельцу(context, f"⚠️ {e}")
        return
    await _написать_владельцу(context, "📊 Отчёт за неделю\n\n" + отчёт)


async def команда_отчёт(update, context):
    if not await _только_владелец(update):
        return
    await update.message.reply_text("Готовлю отчёт… 📊")
    await отправить_отчёт(context)


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


async def ошибка(update, context: ContextTypes.DEFAULT_TYPE):
    """Любая непредвиденная ошибка: пишем в журнал только тип ошибки (без секретов)."""
    log.error("Ошибка в боте: %s: %s", type(context.error).__name__, str(context.error)[:200])


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
        BotCommand("limit", "Сколько потрачено на ИИ"),
        BotCommand("mode", "Режим: с одобрением или авто"),
        BotCommand("replies", "Ответы на комментарии: вкл/выкл"),
        BotCommand("help", "Что я умею"),
    ])


def собрать_приложение() -> Application:
    config.проверить_секреты()
    db.создать_таблицы()
    настройки = config.загрузить_настройки()

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).post_init(после_запуска).build()

    # Команды (только для владельца — проверка внутри каждой)
    for имя, функция in [
        ("start", команда_старт), ("help", команда_старт), ("post", команда_пост),
        ("idea", команда_идея), ("ideas", команда_идеи), ("stats", команда_статистика),
        ("report", команда_отчёт), ("limit", команда_лимит), ("mode", команда_режим),
        ("replies", команда_ответы),
    ]:
        app.add_handler(CommandHandler(имя, функция))

    # Кнопки
    app.add_handler(CallbackQueryHandler(нажатие_кнопки_черновика, pattern=r"^(pub|rew|rej):\d+$"))
    app.add_handler(CallbackQueryHandler(нажатие_кнопки_ответа, pattern=r"^(crp|crj):\d+$"))

    # Обычные сообщения вам в личку
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, обычное_сообщение))

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
