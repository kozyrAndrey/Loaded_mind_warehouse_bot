import asyncio
import json
import logging
import ssl
from datetime import time
from urllib.parse import urlencode
from urllib.request import urlopen

import certifi
from telegram.ext import ContextTypes

from config import (
    AI_WEATHER_AGENT_ENABLED,
    AI_WEATHER_CITY,
    AI_WEATHER_LATITUDE,
    AI_WEATHER_LONGITUDE,
    GROUP_CHAT_ID,
    SCHEDULE_REMINDER_TOPIC_ID,
)
from modules.schedule.config import MSK_TZ, today_msk


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

RAIN_WEATHER_CODES = {
    51, 53, 55,
    56, 57,
    61, 63, 65,
    66, 67,
    80, 81, 82,
    95, 96, 99,
}


def is_enabled(value):
    return str(value or "").strip().lower() not in {"false", "0", "no", "нет", "off"}


def fetch_moscow_weather_forecast():
    params = {
        "latitude": AI_WEATHER_LATITUDE,
        "longitude": AI_WEATHER_LONGITUDE,
        "daily": "weather_code,precipitation_sum,precipitation_probability_max",
        "forecast_days": 1,
        "timezone": "Europe/Moscow",
    }
    url = f"{OPEN_METEO_URL}?{urlencode(params)}"

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(url, timeout=20, context=ssl_context) as response:
        return json.loads(response.read().decode("utf-8"))


def forecast_promises_rain(payload):
    daily = payload.get("daily") or {}
    weather_codes = daily.get("weather_code") or []
    precipitation_sums = daily.get("precipitation_sum") or []
    precipitation_probs = daily.get("precipitation_probability_max") or []

    weather_code = int(weather_codes[0]) if weather_codes else 0
    precipitation_sum = float(precipitation_sums[0] or 0) if precipitation_sums else 0
    precipitation_probability = int(precipitation_probs[0] or 0) if precipitation_probs else 0

    return {
        "weather_code": weather_code,
        "precipitation_sum": precipitation_sum,
        "precipitation_probability": precipitation_probability,
        "has_rain": (
            weather_code in RAIN_WEATHER_CODES
            or precipitation_sum > 0
            or precipitation_probability >= 50
        ),
    }


def build_rain_warning_text(forecast):
    return (
        f"Сегодня в городе {AI_WEATHER_CITY} обещают дождь.\n\n"
        "В помещении «Глори» необходимо сделать проверку на протечку крыши "
        "и добавить фото в отчет о порядке.\n\n"
        "Данные погодного агента: "
        f"вероятность осадков {forecast['precipitation_probability']}%, "
        f"осадки {forecast['precipitation_sum']} мм."
    )


async def weather_check_job(context: ContextTypes.DEFAULT_TYPE):
    if not is_enabled(AI_WEATHER_AGENT_ENABLED):
        return

    if not GROUP_CHAT_ID or not SCHEDULE_REMINDER_TOPIC_ID:
        logging.warning("Погодный агент не запущен: не настроены GROUP_CHAT_ID или SCHEDULE_REMINDER_TOPIC_ID")
        return

    try:
        payload = await asyncio.to_thread(fetch_moscow_weather_forecast)
        forecast = forecast_promises_rain(payload)
    except Exception:
        logging.exception("Погодный агент не смог получить прогноз")
        return

    if not forecast["has_rain"]:
        logging.info("Погодный агент: дождь на %s не найден", today_msk().strftime("%d.%m.%Y"))
        return

    await context.bot.send_message(
        chat_id=int(GROUP_CHAT_ID),
        message_thread_id=int(SCHEDULE_REMINDER_TOPIC_ID),
        text=build_rain_warning_text(forecast),
    )


def setup_ai_agent_jobs(app):
    if not app.job_queue:
        logging.warning("JobQueue не включен. Погодный агент работать не будет.")
        return

    app.job_queue.run_daily(
        weather_check_job,
        time=time(hour=9, minute=40, tzinfo=MSK_TZ),
        name="ai_weather_agent_moscow",
    )
