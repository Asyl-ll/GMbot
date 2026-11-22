import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

TZ = ZoneInfo("Asia/Almaty")  # Астана, UTC+5

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

_RELATIVE = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
_WEEKDAY_ALIASES = {
    "пн": 0, "понедельник": 0,
    "вт": 1, "вторник": 1,
    "ср": 2, "среда": 2, "среду": 2,
    "чт": 3, "четверг": 3,
    "пт": 4, "пятница": 4, "пятницу": 4,
    "сб": 5, "суббота": 5, "субботу": 5,
    "вс": 6, "воскресенье": 6,
}


def now_local() -> datetime:
    return datetime.now(TZ)


def today_local() -> date:
    return now_local().date()


def short_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def fmt_date(d: date) -> str:
    s = f"{WEEKDAYS[d.weekday()]}, {short_date(d)}"
    if d.year != today_local().year:
        s += f" {d.year}"
    return s


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(token: str, today: date) -> date | None:
    t = token.lower().strip().rstrip(",:")
    if t in _RELATIVE:
        return today + timedelta(days=_RELATIVE[t])
    if t in _WEEKDAY_ALIASES:
        delta = (_WEEKDAY_ALIASES[t] - today.weekday()) % 7 or 7
        return today + timedelta(days=delta)
    m = re.fullmatch(r"\+(\d{1,3})", t)
    if m:
        return today + timedelta(days=int(m.group(1)))
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        y, mo, d = map(int, m.groups())
        return _safe(y, mo, d)
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2}|\d{4}))?", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        if m.group(3):
            y = int(m.group(3))
            if y < 100:
                y += 2000
            return _safe(y, mo, d)
        res = _safe(today.year, mo, d)
        if res and res < today:  # дата без года уже прошла -> следующий год
            res = _safe(today.year + 1, mo, d)
        return res
    return None


def parse_task(text: str, today: date) -> tuple[date, str] | None:
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    d = parse_date(parts[0], today)
    if d is None:
        return None
    return d, parts[1].strip()


# --- Погода (Open-Meteo, без ключа) ---------------------------------------

WMO = {
    0: ("☀️", "Ясно"), 1: ("🌤", "Преимущественно ясно"), 2: ("⛅", "Переменная облачность"),
    3: ("☁️", "Пасмурно"), 45: ("🌫", "Туман"), 48: ("🌫", "Изморозный туман"),
    51: ("🌦", "Лёгкая морось"), 53: ("🌦", "Морось"), 55: ("🌧", "Сильная морось"),
    56: ("🌧", "Ледяная морось"), 57: ("🌧", "Сильная ледяная морось"),
    61: ("🌧", "Небольшой дождь"), 63: ("🌧", "Дождь"), 65: ("🌧", "Сильный дождь"),
    66: ("🌧", "Ледяной дождь"), 67: ("🌧", "Сильный ледяной дождь"),
    71: ("🌨", "Небольшой снег"), 73: ("🌨", "Снег"), 75: ("❄️", "Сильный снег"),
    77: ("🌨", "Снежные зёрна"),
    80: ("🌦", "Небольшой ливень"), 81: ("🌧", "Ливень"), 82: ("⛈", "Сильный ливень"),
    85: ("🌨", "Снегопад"), 86: ("❄️", "Сильный снегопад"),
    95: ("⛈", "Гроза"), 96: ("⛈", "Гроза с градом"), 99: ("⛈", "Сильная гроза с градом"),
}


def _t(x: float) -> str:
    v = round(x)
    return "0°" if v == 0 else f"{v:+d}°".replace("-", "−")


async def get_weather() -> str:
    params = {
        "latitude": 51.1694,
        "longitude": 71.4491,
        "current": "temperature_2m,apparent_temperature,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "wind_speed_unit": "ms",
        "timezone": "Asia/Almaty",
        "forecast_days": 1,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
        r.raise_for_status()
        data = r.json()
    cur, day = data["current"], data["daily"]
    emoji, desc = WMO.get(day["weather_code"][0], ("🌡", "Погода"))
    extra = [f"💨 ветер {round(cur['wind_speed_10m'])} м/с"]
    p = day["precipitation_probability_max"][0]
    if p is not None:
        extra.append(f"осадки {p}%")
    return (
        f"{emoji} <b>Астана:</b> {_t(cur['temperature_2m'])}, "
        f"ощущается как {_t(cur['apparent_temperature'])}\n"
        f"{desc}. Днём до {_t(day['temperature_2m_max'][0])}, "
        f"ночью до {_t(day['temperature_2m_min'][0])}\n"
        + ", ".join(extra)
    )
