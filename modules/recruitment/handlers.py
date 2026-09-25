import logging
import os
import re
import tempfile
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import GROUP_CHAT_ID, RECRUITMENT_TOPIC_ID
from core.access import find_registered_employee, is_registered_bot_user
from modules.employees.roles import has_role
from modules.recruitment.excel import create_applications_xlsx
from modules.recruitment.storage import create_job_application
from modules.recruitment.storage import get_all_applications


(
    POSITION,
    FULL_NAME,
    AGE,
    SETTLEMENT,
    EXPERIENCE,
    SHIFTS,
    HOURS,
    CONFIRM,
) = range(700, 708)


POSITIONS = {
    "warehouse_worker": "Кладовщик",
}

RECRUITMENT_ENABLED = False
RECRUITMENT_DISABLED_TEXT = "Подача анкет временно закрыта."


def is_recruitment_tester(user):
    employee = find_registered_employee(user)
    if not employee:
        return False
    return has_role(employee, "warehouse_manager")


def build_candidate_start_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 Подать резюме", callback_data="recruit:start")],
        ]
    )


def build_position_keyboard():
    rows = [
        [InlineKeyboardButton(label, callback_data=f"recruit:position:{key}")]
        for key, label in POSITIONS.items()
    ]
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="recruit:cancel")])
    return InlineKeyboardMarkup(rows)


def build_navigation_keyboard(back_target=None):
    rows = []
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"recruit:back:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="recruit:cancel")])
    return InlineKeyboardMarkup(rows)


def build_value_keyboard(prefix, values, back_target=None):
    rows = []
    row = []
    for value in values:
        row.append(InlineKeyboardButton(str(value), callback_data=f"{prefix}:{value}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"recruit:back:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="recruit:cancel")])
    return InlineKeyboardMarkup(rows)


def build_confirm_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Отправить", callback_data="recruit:confirm")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="recruit:back:hours")],
            [InlineKeyboardButton("❌ Отмена", callback_data="recruit:cancel")],
        ]
    )


def normalize_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def parse_age(text):
    text = normalize_text(text)
    if not text.isdigit():
        return None
    age = int(text)
    if 16 <= age <= 75:
        return age
    return None


def is_reasonable_full_name(text):
    parts = [part for part in normalize_text(text).split(" ") if part]
    return len(parts) >= 2 and all(len(part) >= 2 for part in parts)


def is_reasonable_settlement(text):
    value = normalize_text(text)
    if len(value) < 2 or len(value) > 120:
        return False
    return bool(re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9 .,'’`-]+", value))


def format_application_summary(data):
    return (
        "Проверьте анкету:\n\n"
        f"Должность: {data['position']}\n"
        f"ФИО: {data['full_name']}\n"
        f"Возраст: {data['age']}\n"
        f"Проживание: {data['settlement']}\n"
        f"Опыт: {data['experience']}\n"
        f"Смен в неделю: {data['shifts_per_week']}\n"
        f"Часов за смену: {data['hours_per_shift']}"
    )


def format_application_for_topic(application):
    username = application.get("telegram_username")
    username_text = f"@{username}" if username else "-"
    created_at = application["created_at"].strftime("%d.%m.%Y %H:%M")
    return (
        "📝 Новое резюме\n\n"
        f"ID заявки: {application['id']}\n"
        f"Дата: {created_at}\n"
        f"Telegram ID: {application['telegram_user_id']}\n"
        f"Username: {username_text}\n"
        f"Telegram имя: {application.get('telegram_full_name') or '-'}\n\n"
        f"Должность: {application['position']}\n"
        f"ФИО: {application['full_name']}\n"
        f"Возраст: {application['age']}\n"
        f"Проживание: {application['settlement']}\n"
        f"Смен в неделю: {application['shifts_per_week']}\n"
        f"Часов за смену: {application['hours_per_shift']}\n\n"
        f"Опыт:\n{application['experience']}"
    )


async def send_application_to_topic(context, application):
    if not GROUP_CHAT_ID or not RECRUITMENT_TOPIC_ID:
        return

    kwargs = {
        "chat_id": int(GROUP_CHAT_ID),
        "message_thread_id": int(RECRUITMENT_TOPIC_ID),
        "text": format_application_for_topic(application),
    }
    await context.bot.send_message(**kwargs)


async def export_applications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not RECRUITMENT_ENABLED:
        await query.edit_message_text(RECRUITMENT_DISABLED_TEXT)
        return

    if not is_recruitment_tester(update.effective_user):
        await query.edit_message_text("Выгрузка резюме доступна только руководителю склада.")
        return

    applications = get_all_applications()
    if not applications:
        await query.edit_message_text("Резюме пока нет.")
        return

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = create_applications_xlsx(applications, tmp.name)

    filename = f"job_applications_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    try:
        with open(path, "rb") as file:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=file,
                filename=filename,
                caption=f"📊 Резюме кандидатов: {len(applications)}",
            )
    finally:
        try:
            os.unlink(path)
        except OSError:
            logging.exception("Не удалось удалить временный файл выгрузки резюме")

    await query.edit_message_text("Выгрузка резюме сформирована ✅")


async def candidate_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        sender = query.edit_message_text
    else:
        sender = update.message.reply_text

    context.user_data.clear()

    if not RECRUITMENT_ENABLED:
        await sender(RECRUITMENT_DISABLED_TEXT)
        return ConversationHandler.END

    if is_registered_bot_user(update.effective_user) and not is_recruitment_tester(update.effective_user):
        await sender("Подача резюме доступна только внешним кандидатам и руководителю склада для тестирования.")
        return ConversationHandler.END

    context.user_data["recruitment_active"] = True
    await sender(
        "Выберите должность, на которую хотите откликнуться:",
        reply_markup=build_position_keyboard(),
    )
    return POSITION


async def position_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    position_key = query.data.replace("recruit:position:", "", 1)
    position = POSITIONS.get(position_key)
    if not position:
        await query.edit_message_text("Должность не найдена. Выберите заново:", reply_markup=build_position_keyboard())
        return POSITION

    context.user_data["position"] = position
    await query.edit_message_text("Введите ФИО:", reply_markup=build_navigation_keyboard("position"))
    return FULL_NAME


async def full_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    full_name = normalize_text(update.message.text)
    if not is_reasonable_full_name(full_name):
        await update.message.reply_text("Введите ФИО полностью, минимум имя и фамилию:", reply_markup=build_navigation_keyboard("position"))
        return FULL_NAME

    context.user_data["full_name"] = full_name
    await update.message.reply_text("Введите возраст числом:", reply_markup=build_navigation_keyboard("full_name"))
    return AGE


async def age_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    age = parse_age(update.message.text)
    if age is None:
        await update.message.reply_text("Введите корректный возраст числом от 16 до 75:", reply_markup=build_navigation_keyboard("full_name"))
        return AGE

    context.user_data["age"] = age
    await update.message.reply_text("Где территориально проживаете? Укажите город, поселок или населенный пункт:", reply_markup=build_navigation_keyboard("age"))
    return SETTLEMENT


async def settlement_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settlement = normalize_text(update.message.text)
    if not is_reasonable_settlement(settlement):
        await update.message.reply_text("Введите населенный пункт текстом, без ссылок и спецсимволов:", reply_markup=build_navigation_keyboard("age"))
        return SETTLEMENT

    context.user_data["settlement"] = settlement
    await update.message.reply_text("Кратко опишите опыт работы:", reply_markup=build_navigation_keyboard("settlement"))
    return EXPERIENCE


async def experience_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    experience = normalize_text(update.message.text)
    if len(experience) < 10:
        await update.message.reply_text("Добавьте чуть больше деталей об опыте, хотя бы одно-два предложения:", reply_markup=build_navigation_keyboard("settlement"))
        return EXPERIENCE
    if len(experience) > 3000:
        await update.message.reply_text("Слишком длинный текст. Сократите резюме до 3000 символов:", reply_markup=build_navigation_keyboard("settlement"))
        return EXPERIENCE

    context.user_data["experience"] = experience
    await update.message.reply_text(
        "Сколько рабочих смен в неделю готовы выходить?",
        reply_markup=build_value_keyboard("recruit:shifts", range(3, 7), "experience"),
    )
    return SHIFTS


async def shifts_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    shifts = int(query.data.rsplit(":", 1)[1])
    context.user_data["shifts_per_week"] = shifts
    await query.edit_message_text(
        "Сколько часов за смену в среднем готовы работать?",
        reply_markup=build_value_keyboard("recruit:hours", range(8, 13), "shifts"),
    )
    return HOURS


async def hours_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    hours = int(query.data.rsplit(":", 1)[1])
    context.user_data["hours_per_shift"] = hours
    await query.edit_message_text(
        format_application_summary(context.user_data),
        reply_markup=build_confirm_keyboard(),
    )
    return CONFIRM


async def application_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        application = create_job_application(
            user=update.effective_user,
            position=context.user_data["position"],
            full_name=context.user_data["full_name"],
            age=context.user_data["age"],
            settlement=context.user_data["settlement"],
            experience=context.user_data["experience"],
            shifts_per_week=context.user_data["shifts_per_week"],
            hours_per_shift=context.user_data["hours_per_shift"],
        )
    except Exception as error:
        logging.exception("Не удалось сохранить резюме")
        await query.edit_message_text(f"Не удалось сохранить резюме. Попробуйте позже.\n\nОшибка: {error}")
        return ConversationHandler.END

    try:
        await send_application_to_topic(context, application)
    except Exception:
        logging.exception("Резюме сохранено, но не удалось отправить уведомление в тему")

    context.user_data.clear()
    await query.edit_message_text(
        "Спасибо! Резюме отправлено ✅\n\n"
        "Если понадобится уточнение, с вами свяжутся в Telegram."
    )
    return ConversationHandler.END


async def recruitment_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target = query.data.rsplit(":", 1)[-1]

    prompts = {
        "position": (POSITION, "Выберите должность, на которую хотите откликнуться:", build_position_keyboard()),
        "full_name": (FULL_NAME, "Введите ФИО:", build_navigation_keyboard("position")),
        "age": (AGE, "Введите возраст числом:", build_navigation_keyboard("full_name")),
        "settlement": (SETTLEMENT, "Где территориально проживаете?", build_navigation_keyboard("age")),
        "experience": (EXPERIENCE, "Кратко опишите опыт работы:", build_navigation_keyboard("settlement")),
        "shifts": (SHIFTS, "Сколько рабочих смен в неделю готовы выходить?", build_value_keyboard("recruit:shifts", range(3, 7), "experience")),
        "hours": (HOURS, "Сколько часов за смену в среднем готовы работать?", build_value_keyboard("recruit:hours", range(8, 13), "shifts")),
    }
    state, text, markup = prompts[target]
    await query.edit_message_text(text, reply_markup=markup)
    return state


async def recruitment_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("Подача резюме отменена.")
    else:
        await update.message.reply_text("Подача резюме отменена.")

    return ConversationHandler.END


def get_recruitment_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(candidate_entry, pattern=r"^recruit:start$"),
            CommandHandler("apply", candidate_entry),
        ],
        states={
            POSITION: [
                CallbackQueryHandler(position_selected, pattern=r"^recruit:position:"),
                CallbackQueryHandler(recruitment_cancel, pattern=r"^recruit:cancel$"),
            ],
            FULL_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, full_name_received),
                CallbackQueryHandler(recruitment_cancel, pattern=r"^recruit:cancel$"),
            ],
            AGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, age_received),
                CallbackQueryHandler(recruitment_cancel, pattern=r"^recruit:cancel$"),
            ],
            SETTLEMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, settlement_received),
                CallbackQueryHandler(recruitment_cancel, pattern=r"^recruit:cancel$"),
            ],
            EXPERIENCE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, experience_received),
                CallbackQueryHandler(recruitment_cancel, pattern=r"^recruit:cancel$"),
            ],
            SHIFTS: [
                CallbackQueryHandler(shifts_selected, pattern=r"^recruit:shifts:[3-6]$"),
                CallbackQueryHandler(recruitment_cancel, pattern=r"^recruit:cancel$"),
            ],
            HOURS: [
                CallbackQueryHandler(hours_selected, pattern=r"^recruit:hours:(8|9|10|11|12)$"),
                CallbackQueryHandler(recruitment_cancel, pattern=r"^recruit:cancel$"),
            ],
            CONFIRM: [
                CallbackQueryHandler(application_confirmed, pattern=r"^recruit:confirm$"),
                CallbackQueryHandler(recruitment_cancel, pattern=r"^recruit:cancel$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(recruitment_back, pattern=r"^recruit:back:(position|full_name|age|settlement|experience|shifts|hours)$"),
            CommandHandler("cancel", recruitment_cancel),
        ],
    )


def get_recruitment_handlers():
    return [
        CallbackQueryHandler(export_applications, pattern=r"^recruit:export$"),
        get_recruitment_conversation_handler(),
    ]
