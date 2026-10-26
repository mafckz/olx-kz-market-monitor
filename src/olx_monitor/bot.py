from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from typing import Any

import keyring
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from olx_monitor.ai import AIService, validate_ai_profile
from olx_monitor.database import Database
from olx_monitor.models import SearchProfile
from olx_monitor.monitor import Monitor, make_one_off_from_text
from olx_monitor.profiles import parse_profile_request
from olx_monitor.reporting import build_digest, split_telegram, write_markdown_report
from olx_monitor.settings import Settings

logger = logging.getLogger(__name__)
KEYRING_SERVICE = "olx-market-monitor"
TELEGRAM_KEY_NAME = "telegram_bot_token"


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["database"]


def _monitor(context: ContextTypes.DEFAULT_TYPE) -> Monitor:
    return context.application.bot_data["monitor"]


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


async def _owner_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or chat.type != ChatType.PRIVATE:
        return None
    owner = _db(context).get_setting("owner_chat_id")
    if owner and str(chat.id) != owner:
        await message.reply_text("Этот бот уже привязан к владельцу и принимает команды только от него.")
        return None
    if not owner:
        return None
    return chat.id


def _profile_by_token(database: Database, token: str) -> SearchProfile | None:
    profiles = database.profiles()
    token = token.strip()
    if token.isdigit():
        index = int(token) - 1
        return profiles[index] if 0 <= index < len(profiles) else None
    for profile in profiles:
        if profile.id.casefold() == token.casefold():
            return profile
    candidates = [profile for profile in profiles if token.casefold() in profile.name.casefold()]
    return candidates[0] if len(candidates) == 1 else None


async def pair_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or chat.type != ChatType.PRIVATE:
        return
    db = _db(context)
    owner = db.get_setting("owner_chat_id")
    if owner:
        if str(chat.id) == owner:
            await message.reply_text("Этот личный чат уже привязан.")
        else:
            await message.reply_text("Бот уже привязан к другому владельцу.")
        return
    if len(context.args) != 1:
        await message.reply_text("Введите локальный одноразовый код: /pair КОД")
        return
    if not db.take_pairing_code(context.args[0].strip().upper()):
        await message.reply_text("Код не подошёл или уже использован. Проверьте вывод окна настройки на компьютере.")
        return
    db.save_setting("owner_chat_id", str(chat.id))
    await message.reply_text(
        "Telegram привязан. Команды принимаются только из этого личного чата.\n\n"
        "Планировщик начнёт первую проверку автоматически. Используйте /help для управления."
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or chat.type != ChatType.PRIVATE:
        return
    db = _db(context)
    owner = db.get_setting("owner_chat_id")
    if not owner:
        await message.reply_text(
            "Личный монитор OLX.kz. Для первой привязки введите код, показанный программой настройки на компьютере:\n"
            "/pair КОД"
        )
        return
    if str(chat.id) != owner:
        await message.reply_text("Бот уже привязан к владельцу и не принимает команды из этого чата.")
        return
    await message.reply_text("Бот работает. Напишите поисковый запрос или используйте /help.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _owner_chat(update, context):
        return
    text = (
        "Управление монитором OLX.kz\n\n"
        "Отправьте обычным сообщением, что искать: бот покажет черновик и спросит подтверждение.\n"
        "/add <запрос> — разобрать запрос локальными правилами\n"
        "/addai <запрос> — разобрать запрос AI и включить AI-режим профиля\n"
        "/profiles — список профилей\n"
        "/pause <номер или id> — приостановить поиск\n"
        "/resume <номер или id> — возобновить поиск\n"
        "/mode <номер или id> classifier|ai — режим анализа\n"
        "/run — проверить все активные профили сейчас\n"
        "/kz <запрос> — разовый поиск по всему Казахстану, профили не меняются\n"
        "/summary — получить Markdown-сводку\n"
        "/review — сомнительные совпадения\n"
        "/status — состояние бота, расписания и AI-бюджета\n"
        "/help — эта справка"
    )
    await update.effective_message.reply_text(text)


async def _start_draft(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    use_ai: bool,
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat:
        return
    data: dict[str, Any] | None = None
    notice: str | None = None
    if use_ai:
        ai_result = await AIService(_settings(context), _db(context)).parse_profile(text)
        if ai_result.data:
            data, error = validate_ai_profile(ai_result.data)
            if error:
                notice = error
        elif ai_result.note:
            notice = ai_result.note
        if data:
            data["mode"] = "ai"
    if data is None:
        parsed, errors = parse_profile_request(text)
        if errors:
            prefix = f"{notice}\n\n" if notice else ""
            await message.reply_text(prefix + "Не удалось собрать профиль:\n• " + "\n• ".join(errors))
            return
        data = parsed
        assert data is not None
        if use_ai:
            data["mode"] = "classifier"
    if data.get("city") is None:
        data["city"] = "Алматы"
        scope_notice = (
            "Для сохранённого профиля выбран Алматы. Поиск по всему Казахстану выполняйте отдельной "
            "командой /kz; постоянный профиль не будет общенациональным."
        )
        notice = f"{notice}\n{scope_notice}" if notice else scope_notice
    draft_id = _db(context).save_draft(chat.id, data)
    mode_label = "AI-анализ включён" if data.get("mode") == "ai" else "режим классификатора"
    conditions = ", ".join(data.get("exclude_terms", [])) or "нет"
    preview = (
        (f"Примечание: {notice}\n\n" if notice else "")
        + "Черновик профиля поиска:\n"
        + f"Название: {data['name']}\n"
        + f"Категория: {data['category_label']}\n"
        + f"Товар/модель: {data['target']}\n"
        + f"Цена: {int(data['min_price']):,}–{int(data['max_price']):,} ₸\n".replace(",", " ")
        + f"География: {data.get('city') or 'весь Казахстан'}\n"
        + f"Исключения: {conditions}\n"
        + f"Режим: {mode_label}\n\n"
        + "Сохранить и активировать этот поиск?"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Сохранить профиль", callback_data=f"profile:confirm:{draft_id}"),
                InlineKeyboardButton("Отмена", callback_data=f"profile:cancel:{draft_id}"),
            ]
        ]
    )
    await message.reply_text(preview, reply_markup=keyboard)


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _owner_chat(update, context):
        return
    text = " ".join(context.args).strip()
    if not text:
        context.user_data["pending_profile_mode"] = "classifier"
        await update.effective_message.reply_text(
            "Напишите, что искать, например: «найди б/у настольный ПК с RTX 3080 за 200–500 тыс. ₸ в Алматы»."
        )
        return
    await _start_draft(update, context, text, use_ai=False)


async def add_ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _owner_chat(update, context):
        return
    text = " ".join(context.args).strip()
    if not text:
        context.user_data["pending_profile_mode"] = "ai"
        await update.effective_message.reply_text("Опишите, какой товар искать. Черновик разберёт AI.")
        return
    await _start_draft(update, context, text, use_ai=True)


async def free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _owner_chat(update, context):
        return
    message = update.effective_message
    text = message.text.strip() if message and message.text else ""
    mode = context.user_data.pop("pending_profile_mode", "classifier")
    await _start_draft(update, context, text, use_ai=mode == "ai")


async def profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    if not query or not chat or chat.type != ChatType.PRIVATE:
        return
    db = _db(context)
    if db.get_setting("owner_chat_id") != str(chat.id):
        await query.answer("Нет доступа", show_alert=True)
        return
    try:
        _, action, draft_id = query.data.split(":", 2)
    except (AttributeError, ValueError):
        await query.answer("Неизвестное действие", show_alert=True)
        return
    if action == "cancel":
        with db.connection() as conn:
            conn.execute("DELETE FROM profile_drafts WHERE id=? AND chat_id=?", (draft_id, chat.id))
        await query.answer("Черновик удалён")
        await query.edit_message_text("Черновик отменён.")
        return
    if action == "confirm":
        profile_id = db.confirm_draft(draft_id, chat.id)
        if not profile_id:
            await query.answer("Черновик устарел или не найден", show_alert=True)
            return
        profile = db.get_profile(profile_id)
        await query.answer("Профиль сохранён")
        await query.edit_message_text(f"Профиль сохранён и включён: {profile.name if profile else profile_id}.")
        context.application.create_task(_run_and_reply(context.application, chat.id, profile_id))


async def profiles_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _owner_chat(update, context):
        return
    profiles = _db(context).profiles()
    if not profiles:
        await update.effective_message.reply_text("Профилей пока нет.")
        return
    lines = ["Профили поиска:"]
    for index, profile in enumerate(profiles, 1):
        status = "активен" if profile.is_active else "пауза"
        lines.append(
            f"{index}. {profile.name} [{status}; {profile.mode}]\n"
            f"   Цена {profile.min_price:,}–{profile.max_price:,} ₸; {profile.city or 'Казахстан'}\n"
            f"   id: {profile.id}".replace(",", " ")
        )
    await update.effective_message.reply_text("\n".join(lines))


async def toggle_profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _owner_chat(update, context):
        return
    command = update.effective_message.text.split()[0].split("@")[0].lstrip("/")
    if not context.args:
        await update.effective_message.reply_text(f"Формат: /{command} номер_или_id")
        return
    profile = _profile_by_token(_db(context), " ".join(context.args))
    if not profile:
        await update.effective_message.reply_text("Профиль не найден. Посмотрите список через /profiles.")
        return
    active = command == "resume"
    _db(context).set_profile_active(profile.id, active)
    await update.effective_message.reply_text(f"Поиск {'возобновлён' if active else 'приостановлен'}: {profile.name}.")


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _owner_chat(update, context):
        return
    args = context.args
    if len(args) < 2:
        await update.effective_message.reply_text("Формат: /mode номер_или_id classifier|ai")
        return
    profile = _profile_by_token(_db(context), " ".join(args[:-1]))
    mode = args[-1].casefold()
    if mode not in {"classifier", "ai"}:
        await update.effective_message.reply_text("Режим: classifier или ai.")
        return
    if not profile:
        await update.effective_message.reply_text("Профиль не найден. Посмотрите список через /profiles.")
        return
    if mode == "ai" and not AIService(_settings(context), _db(context)).available():
        await update.effective_message.reply_text(
            "AI пока не настроен. Запустите на компьютере `python -m olx_monitor configure` "
            "и добавьте OpenAI API key с месячным лимитом."
        )
        return
    _db(context).set_profile_mode(profile.id, mode)
    if mode == "ai":
        _db(context).reset_ai_checks(profile.id)
    await update.effective_message.reply_text(f"Для профиля «{profile.name}» установлен режим {mode}.")
    if mode == "ai":
        context.application.create_task(
            _run_and_reply(context.application, update.effective_chat.id, profile_id=profile.id)
        )


async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _owner_chat(update, context):
        return
    await update.effective_message.reply_text("Запускаю проверку активных профилей. Отправлю итог после завершения.")
    context.application.create_task(_run_and_reply(context.application, update.effective_chat.id))


async def country_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _owner_chat(update, context):
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await update.effective_message.reply_text("Формат: /kz <что искать и диапазон цены>")
        return
    data, errors = parse_profile_request(raw)
    if errors:
        await update.effective_message.reply_text("Не удалось разобрать разовый запрос:\n• " + "\n• ".join(errors))
        return
    assert data
    profile = make_one_off_from_text(data)
    await update.effective_message.reply_text(
        f"Запускаю разовый поиск по всему Казахстану: {profile.target}, "
        f"{profile.min_price:,}–{profile.max_price:,} ₸. Сохранённые профили не изменятся.".replace(",", " ")
    )
    context.application.create_task(_run_and_reply(context.application, update.effective_chat.id, profile=profile))


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _owner_chat(update, context):
        return
    settings = _settings(context)
    path = write_markdown_report(_db(context), settings.home)
    content = path.read_bytes()
    await update.effective_message.reply_document(
        document=BytesIO(content),
        filename="olx-market-monitor.md",
        caption="Текущая сводка по поисковым профилям.",
    )


async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _owner_chat(update, context):
        return
    rows = _db(context).uncertain_listings()
    if not rows:
        await update.effective_message.reply_text("Очередь сомнительных совпадений пуста.")
        return
    lines = ["Совпадения для ручной проверки:"]
    for row in rows[:15]:
        price = f"{row['price']:,} ₸".replace(",", " ") if row["price"] else "цена не указана"
        lines.extend(["", f"{row['title']} · {price} · {row['location']}", row["match_reason"], row["url"]])
    for chunk in split_telegram("\n".join(lines)):
        await update.effective_message.reply_text(chunk)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _owner_chat(update, context):
        return
    db = _db(context)
    settings = _settings(context)
    stats = db.recent_stats()
    last = stats["last_run"]
    run_text = "ещё не запускался"
    if last:
        run_text = f"{last['status']} · {last['started_at']}"
        if last["error_text"]:
            run_text += f" · {last['error_text'][:300]}"
    ai = AIService(settings, db)
    ai_state = "настроен" if ai.available() else "выключен"
    await update.effective_message.reply_text(
        f"Активных объявлений: {stats['active']}; сомнительных: {stats['uncertain']}.\n"
        f"Последний запуск: {run_text}.\n"
        f"Расписание: каждые {settings.poll_hours} ч.; глубокая проверка — каждые {settings.deep_check_hours} ч.\n"
        f"Отчёт: {settings.home / 'reports' / 'latest.md'}\n"
        f"AI: {ai_state}; расходы в этом месяце ${db.ai_month_spend():.4f} / ${settings.ai_monthly_budget_usd:.2f}."
    )


async def _run_and_reply(
    application: Application,
    chat_id: int,
    profile_id: str | None = None,
    profile: SearchProfile | None = None,
) -> None:
    db: Database = application.bot_data["database"]
    monitor: Monitor = application.bot_data["monitor"]
    profiles = [profile] if profile else [db.get_profile(profile_id)] if profile_id else None
    profiles = [item for item in (profiles or []) if item is not None] if profiles is not None else None
    try:
        stats = await monitor.run(profiles)
        known = {item.id: item for item in db.profiles()}
        if profile:
            known[profile.id] = profile
        digest = build_digest(stats, known)
        if not digest:
            digest = "Проверка завершена. Новых объявлений и существенных изменений нет."
        for chunk in split_telegram(digest):
            await application.bot.send_message(chat_id=chat_id, text=chunk, disable_web_page_preview=True)
    except Exception:
        logger.exception("Manual monitor run failed")
        await application.bot.send_message(
            chat_id=chat_id,
            text="Не удалось выполнить проверку из-за внутренней ошибки. Подробности записаны в локальный журнал.",
        )


async def _scheduled_runs(application: Application) -> None:
    db: Database = application.bot_data["database"]
    monitor: Monitor = application.bot_data["monitor"]
    settings: Settings = application.bot_data["settings"]
    while True:
        owner = db.get_setting("owner_chat_id")
        if not owner:
            await asyncio.sleep(30)
            continue
        try:
            stats = await monitor.run()
            digest = build_digest(stats, {profile.id: profile for profile in db.profiles()})
            if digest and (stats.notifications or stats.error):
                for chunk in split_telegram(digest):
                    await application.bot.send_message(chat_id=int(owner), text=chunk, disable_web_page_preview=True)
        except Exception:
            logger.exception("Scheduled monitor run failed")
            try:
                await application.bot.send_message(
                    chat_id=int(owner),
                    text="Плановая проверка завершилась внутренней ошибкой. Подробности в локальном журнале.",
                )
            except Exception:
                logger.exception("Could not send scheduled-run error to Telegram")
        await asyncio.sleep(settings.poll_hours * 60 * 60)


async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("help", "Справка"),
            BotCommand("profiles", "Список поисков"),
            BotCommand("add", "Добавить профиль"),
            BotCommand("addai", "Добавить профиль с AI"),
            BotCommand("run", "Проверить сейчас"),
            BotCommand("kz", "Разовый поиск по Казахстану"),
            BotCommand("summary", "Получить сводку"),
            BotCommand("status", "Состояние монитора"),
            BotCommand("review", "Сомнительные совпадения"),
        ]
    )
    application.bot_data["scheduler_task"] = application.create_task(_scheduled_runs(application))


async def _post_shutdown(application: Application) -> None:
    task = application.bot_data.get("scheduler_task")
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def run_bot(settings: Settings, database: Database) -> None:
    token = keyring.get_password(KEYRING_SERVICE, TELEGRAM_KEY_NAME)
    if not token:
        raise RuntimeError("Telegram Bot Token не найден. Сначала запустите python -m olx_monitor configure.")
    monitor = Monitor(settings, database)
    application = ApplicationBuilder().token(token).post_init(_post_init).post_shutdown(_post_shutdown).build()
    application.bot_data.update(settings=settings, database=database, monitor=monitor)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("pair", pair_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("addai", add_ai_command))
    application.add_handler(CommandHandler("profiles", profiles_command))
    application.add_handler(CommandHandler("pause", toggle_profile_command))
    application.add_handler(CommandHandler("resume", toggle_profile_command))
    application.add_handler(CommandHandler("mode", mode_command))
    application.add_handler(CommandHandler("run", run_command))
    application.add_handler(CommandHandler("kz", country_command))
    application.add_handler(CommandHandler("summary", summary_command))
    application.add_handler(CommandHandler("review", review_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CallbackQueryHandler(profile_callback, pattern=r"^profile:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))
    application.run_polling(allowed_updates=["message", "callback_query"], bootstrap_retries=-1)
