"""Расписание университета. Пока пустое, заполним позже.

День недели: 0 = пн, 1 = вт, ... 6 = вс.
Элемент списка: (время, предмет, аудитория).
"""
from datetime import date

SCHEDULE: dict[int, list[tuple[str, str, str]]] = {
    # Пример:
    # 0: [("09:00", "Дискретная математика", "ауд. 301"),
    #     ("10:50", "Программирование", "ауд. 205")],
}


def lessons_for(d: date) -> list[str]:
    items = sorted(SCHEDULE.get(d.weekday(), []))
    return [f"{t} — {name}" + (f" ({room})" if room else "") for t, name, room in items]
