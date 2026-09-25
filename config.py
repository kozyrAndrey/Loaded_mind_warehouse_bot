import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*args, **kwargs):
        return False

load_dotenv()


def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return int(default)



BASE_DIR = Path(__file__).resolve().parent

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOOTSTRAP_ADMIN_IDS = {
    value.strip() for value in os.getenv("BOOTSTRAP_ADMIN_IDS", "").split(",") if value.strip()
}

DATABASE_URL = os.getenv("DATABASE_URL", "")

GOOGLE_CREDENTIALS_PATH = os.getenv(
    "GOOGLE_CREDENTIALS_PATH",
    str(BASE_DIR / "google_credentials.json"),
)

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")

GOOGLE_WORKSHEET_NAME = os.getenv(
    "GOOGLE_WORKSHEET_NAME",
    "Оприходование",
)

GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "")

RETURNS_TOPIC_ID = os.getenv("RETURNS_TOPIC_ID", "")
SUPPORT_MANAGER_MENTION = os.getenv("SUPPORT_MANAGER_MENTION", "")

RECEIVING_REPORT_TOPIC_ID = os.getenv("RECEIVING_REPORT_TOPIC_ID", "")
SPECIAL_RECEIVING_REPORT_TOPIC_ID = os.getenv("SPECIAL_RECEIVING_REPORT_TOPIC_ID", "")
# ============================================================
# НАСТРОЙКИ МОДУЛЯ ЗП
# ============================================================

PAYROLL_GOOGLE_SHEET_ID = os.getenv("PAYROLL_GOOGLE_SHEET_ID", "")
PAYROLL_REPORT_TOPIC_ID = os.getenv("PAYROLL_REPORT_TOPIC_ID", "")
AUTO_DAILY_SUMMARY_ENABLED = os.getenv("AUTO_DAILY_SUMMARY_ENABLED", "false").lower() in {"1", "true", "yes"}


# ============================================================
# НАСТРОЙКИ МОДУЛЯ РАСХОДНИКОВ
# ============================================================

CONSUMABLES_TOPIC_ID = os.getenv("CONSUMABLES_TOPIC_ID", "")
DOCUMENT_WORKFLOW_CHAT_ID = os.getenv("DOCUMENT_WORKFLOW_CHAT_ID", "")
WAREHOUSE_INVOICES_TOPIC_ID = os.getenv("WAREHOUSE_INVOICES_TOPIC_ID", "")
ACTS_CLOSING_DOCUMENTS_TOPIC_ID = os.getenv("ACTS_CLOSING_DOCUMENTS_TOPIC_ID", "")


# ============================================================
# НАСТРОЙКИ МОДУЛЯ РЕЗЮМЕ
# ============================================================

RECRUITMENT_TOPIC_ID = os.getenv("RECRUITMENT_TOPIC_ID", "")


# ============================================================
# НАСТРОЙКИ ОПЕРАЦИОННОЙ ТАБЛИЦЫ
# ============================================================
# Здесь хранятся расписание и дежурства.

OPERATIONS_GOOGLE_SHEET_ID = os.getenv("OPERATIONS_GOOGLE_SHEET_ID", "")


# ============================================================
# НАСТРОЙКИ МОДУЛЯ РАСПИСАНИЯ
# ============================================================
# SCHEDULE_EXPORT_TOPIC_ID — тема, куда бот выгружает Excel-файл расписания.
# SCHEDULE_REMINDER_TOPIC_ID — тема, куда бот отправляет пятничное напоминание.
#
# Для обратной совместимости можно оставить старый SCHEDULE_TOPIC_ID:
# если новые переменные не заданы, бот возьмет его как fallback.

SCHEDULE_TOPIC_ID = os.getenv("SCHEDULE_TOPIC_ID", "")
SCHEDULE_EXPORT_TOPIC_ID = os.getenv("SCHEDULE_EXPORT_TOPIC_ID", SCHEDULE_TOPIC_ID)
SCHEDULE_REMINDER_TOPIC_ID = os.getenv("SCHEDULE_REMINDER_TOPIC_ID", SCHEDULE_TOPIC_ID)


# ============================================================
# НАСТРОЙКИ ВНУТРЕННЕГО ИИ-АГЕНТА
# ============================================================

AI_WEATHER_AGENT_ENABLED = os.getenv("AI_WEATHER_AGENT_ENABLED", "false")
AI_WEATHER_CITY = os.getenv("AI_WEATHER_CITY", "Москва")
AI_WEATHER_LATITUDE = os.getenv("AI_WEATHER_LATITUDE", "55.7558")
AI_WEATHER_LONGITUDE = os.getenv("AI_WEATHER_LONGITUDE", "37.6173")


# ============================================================
# НАСТРОЙКИ ОТПРАВКИ ФОТО ЧЕСТНОГО ЗНАКА ПО ВОЗВРАТАМ
# ============================================================

RETURN_CHZ_CHAT_ID = os.getenv("RETURN_CHZ_CHAT_ID", "")
RETURN_CHZ_TOPIC_ID = os.getenv("RETURN_CHZ_TOPIC_ID", "")


# ============================================================
# НАСТРОЙКИ ИНТЕГРАЦИИ С МОИМСКЛАДОМ
# ============================================================

MOYSKLAD_TOKEN = os.getenv("MOYSKLAD_TOKEN", "")
MOYSKLAD_API_BASE_URL = os.getenv(
    "MOYSKLAD_API_BASE_URL",
    "https://api.moysklad.ru/api/remap/1.2",
)
MOYSKLAD_CA_FILE = os.getenv("MOYSKLAD_CA_FILE", "")
MOYSKLAD_SSL_VERIFY = os.getenv("MOYSKLAD_SSL_VERIFY", "true")

MOYSKLAD_SALE_PRICE_TYPE = os.getenv("MOYSKLAD_SALE_PRICE_TYPE", "Цена продажи")

MARKING_ONE_C_TEMPLATE_PATH = os.getenv("MARKING_ONE_C_TEMPLATE_PATH", "")
MARKING_ONE_C_RETAIL_PRICE_TYPE = (
    os.getenv("MARKING_ONE_C_RETAIL_PRICE_TYPE", "") or MOYSKLAD_SALE_PRICE_TYPE
)
MARKING_ONE_C_DEFAULT_GENDER = os.getenv("MARKING_ONE_C_DEFAULT_GENDER", "") or "Unisex"
MARKING_ONE_C_CONSIGNOR = os.getenv("MARKING_ONE_C_CONSIGNOR", "")


# ============================================================
# LAMODA FBS
# ============================================================

LAMODA_CLIENT_ID = os.getenv("LAMODA_CLIENT_ID", "")
LAMODA_CLIENT_SECRET = os.getenv("LAMODA_CLIENT_SECRET", "")
LAMODA_SELLER_ID = os.getenv("LAMODA_SELLER_ID", "120528732")
LAMODA_API_BASE_URL = os.getenv(
    "LAMODA_API_BASE_URL",
    "https://public-api-seller.lamoda.ru/api",
).rstrip("/")
LAMODA_SYNC_INTERVAL_MINUTES = env_int("LAMODA_SYNC_INTERVAL_MINUTES", 10)
LAMODA_REMINDER_TIME = os.getenv("LAMODA_REMINDER_TIME", "10:00")
LAMODA_RETURNS_TOPIC_ID = os.getenv("LAMODA_RETURNS_TOPIC_ID", "")
