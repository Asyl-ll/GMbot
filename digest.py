import html
import logging
from datetime import date, timedelta

import db
import helpers
import schedule

log = logging.getLogger("reminder-bot")


def _e(s: str) -> str:
    return html.escape(s)


async def build_digest(today: date, with_weather: bool = True) -> str:
    title = "☀️ <b>Доброе утро!</b>" if with_weather else "📋 <b>План на сегодня</b>"
    parts = [f"{title}\n{helpers.fmt_date(today).capitalize()}"]

    if with_weather:
        try:
            parts.append(await helpers.get_weather())
        except Exception:
            log.exception("Не удалось получить погоду")
            parts.append("🌡 Не получилось узнать погоду.")

    lessons = schedule.lessons_for(today)
    if lessons:
        parts.append("🎓 <b>Пары:</b>\n" + "\n".join(_e(x) for x in lessons))

    tasks = await db.tasks_on(today)
    if tasks:
        parts.append(
            "📌 <b>Задачи на сегодня:</b>\n"
            + "\n".join(f"• {_e(t['text'])}  <i>#{t['id']}</i>" for t in tasks)
        )
    else:
        parts.append("📌 На сегодня задач нет.")

    late = await db.overdue(today)
    if late:
        parts.append(
            "⚠️ <b>Просрочено:</b>\n"
            + "\n".join(
                f"• {_e(t['text'])} (было {helpers.short_date(t['due'])})  <i>#{t['id']}</i>"
                for t in late
            )
        )

    soon = await db.between(today + timedelta(days=1), today + timedelta(days=3))
    if soon:
        parts.append(
            "🗓 <b>Скоро:</b>\n"
            + "\n".join(
                f"• {helpers.fmt_date(t['due'])}: {_e(t['text'])}  <i>#{t['id']}</i>" for t in soon
            )
        )

    if tasks or late:
        parts.append("Отметить выполненной: /done номер")
    return "\n\n".join(parts)
