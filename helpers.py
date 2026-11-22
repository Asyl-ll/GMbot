import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("reminder-bot")

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


# --- Погода: Open-Meteo, запасной источник MET Norway (оба без ключа) --------

_LAT, _LON = 51.1694, 71.4491
_UA = "GMbot/1.0 (https://github.com/Asyl-II/GMbot)"
_cache: tuple[float, dict] | None = None

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


def _met_symbol(code: str) -> tuple[str, str]:
    c = code.split("_")[0]
    heavy, light = c.startswith("heavy"), c.startswith("light")
    base = c.removeprefix("heavy").removeprefix("light")
    pre = "сильный " if heavy else ("небольшой " if light else "")
    if "thunder" in base:
        return "⛈", "Гроза"
    if "sleet" in base:
        return "🌨", "Мокрый снег"
    if "snow" in base:
        word = "снегопад" if "showers" in base else "снег"
        return ("❄️" if heavy else "🌨"), (pre + word).capitalize()
    if "rain" in base:
        word = "ливень" if "showers" in base else "дождь"
        return "🌧", (pre + word).capitalize()
    if "fog" in base:
        return "🌫", "Туман"
    if base == "clearsky":
        return "☀️", "Ясно"
    if base == "fair":
        return "🌤", "Преимущественно ясно"
    if base == "partlycloudy":
        return "⛅", "Переменная облачность"
    if base == "cloudy":
        return "☁️", "Пасмурно"
    return "🌡", "Погода"


async def _open_meteo(client: httpx.AsyncClient) -> dict:
    params = {
        "latitude": _LAT,
        "longitude": _LON,
        "current": "temperature_2m,apparent_temperature,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "wind_speed_unit": "ms",
        "timezone": "Asia/Almaty",
        "forecast_days": 1,
    }
    last: Exception | None = None
    for delay in (0, 3, 8):
        if delay:
            await asyncio.sleep(delay)
        try:
            r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            if r.status_code == 429 or r.status_code >= 500:
                last = RuntimeError(f"Open-Meteo HTTP {r.status_code}")
                continue
            r.raise_for_status()
            data = r.json()
            break
        except httpx.HTTPError as e:
            last = e
    else:
        raise last  # type: ignore[misc]
    cur, day = data["current"], data["daily"]
    emoji, desc = WMO.get(day["weather_code"][0], ("🌡", "Погода"))
    return {
        "emoji": emoji, "desc": desc,
        "temp": cur["temperature_2m"], "feels": cur["apparent_temperature"],
        "tmax": day["temperature_2m_max"][0], "tmin": day["temperature_2m_min"][0],
        "wind": cur["wind_speed_10m"], "precip": day["precipitation_probability_max"][0],
    }


async def _met_no(client: httpx.AsyncClient) -> dict:
    r = await client.get(
        "https://api.met.no/weatherapi/locationforecast/2.0/compact",
        params={"lat": round(_LAT, 4), "lon": round(_LON, 4)},
        headers={"User-Agent": _UA},
    )
    r.raise_for_status()
    series = r.json()["properties"]["timeseries"]
    first = series[0]
    details = first["data"]["instant"]["details"]
    start = datetime.fromisoformat(first["time"].replace("Z", "+00:00"))
    temps = []
    for item in series:
        t = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
        if t - start > timedelta(hours=24):
            break
        temps.append(item["data"]["instant"]["details"]["air_temperature"])
    block = first["data"].get("next_6_hours") or first["data"].get("next_1_hours") or {}
    emoji, desc = _met_symbol(block.get("summary", {}).get("symbol_code", ""))
    return {
        "emoji": emoji, "desc": desc,
        "temp": details["air_temperature"], "feels": None,
        "tmax": max(temps), "tmin": min(temps),
        "wind": details.get("wind_speed", 0), "precip": None,
    }


def _format(w: dict) -> str:
    head = f"{w['emoji']} <b>Астана:</b> {_t(w['temp'])}"
    if w.get("feels") is not None:
        head += f", ощущается как {_t(w['feels'])}"
    extra = [f"💨 ветер {round(w['wind'])} м/с"]
    if w.get("precip") is not None:
        extra.append(f"осадки {w['precip']}%")
    return "\n".join([
        head,
        f"{w['desc']}. Днём до {_t(w['tmax'])}, ночью до {_t(w['tmin'])}",
        ", ".join(extra),
    ])


async def get_weather() -> str:
    global _cache
    if _cache and time.time() - _cache[0] < 1200:  # кэш на 20 минут
        return _format(_cache[1])
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": _UA}) as client:
        try:
            w = await _open_meteo(client)
        except Exception as e:
            log.warning("Open-Meteo не ответил (%s), пробую MET Norway", e)
            w = await _met_no(client)
    _cache = (time.time(), w)
    return _format(w)
