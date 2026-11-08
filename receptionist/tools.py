"""The functions the voice assistant can call. Each returns one short, speakable sentence or two.

Vapi requires tool results to be flat strings, and the model reads them out
loud, so results are written as natural speech: weekday names, 12-hour times,
and at most a handful of options.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from .scheduling import AppointmentBook, BookingError

MAX_SLOTS_SPOKEN = 4


def _spoken_time(t: dt.time) -> str:
    return dt.datetime.combine(dt.date.today(), t).strftime("%-I:%M %p").replace(":00 ", " ")


def _spoken_day(d: dt.date) -> str:
    return d.strftime("%A, %B %-d")


def _parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value.strip())
    except (ValueError, AttributeError) as exc:
        raise BookingError("Please give the date as year-month-day, for example 2026-11-20.") from exc


def _parse_time(value: str) -> dt.time:
    value = value.strip().upper().replace(".", "")
    for fmt in ("%H:%M", "%I:%M %p", "%I %p", "%I:%M%p", "%I%p"):
        try:
            return dt.datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise BookingError(f"I didn't understand the time '{value}'.")


def _slot_list(slots: list[dt.time]) -> str:
    spoken = [_spoken_time(s) for s in slots[:MAX_SLOTS_SPOKEN]]
    more = f", and {len(slots) - MAX_SLOTS_SPOKEN} more" if len(slots) > MAX_SLOTS_SPOKEN else ""
    return ", ".join(spoken[:-1]) + (f" or {spoken[-1]}" if len(spoken) > 1 else spoken[0]) + more


def make_tools(book: AppointmentBook) -> dict[str, Callable[..., str]]:
    clinic = book.clinic

    def check_availability(service: str, date: str | None = None, **_: Any) -> str:
        info = clinic.service(service)
        if date:
            day = _parse_date(date)
            slots = book.free_slots(day, service)
            if slots:
                return f"On {_spoken_day(day)} I have {_slot_list(slots)} for a {info['label'].lower()}."
            nxt = book.next_available(service, day + dt.timedelta(days=1))
        else:
            nxt = book.next_available(service, clinic.now().date())
        if not nxt:
            return "I don't see any openings in the next two weeks. I can take a message for the front desk."
        day, slots = nxt
        prefix = f"We're fully booked on {_spoken_day(_parse_date(date))}. " if date else ""
        return f"{prefix}The next openings are on {_spoken_day(day)}: {_slot_list(slots)}."

    def book_appointment(patient_name: str, phone: str, service: str, date: str, time: str, notes: str = "",
                         _call_id: str | None = None, **_: Any) -> str:
        appt = book.book(patient=patient_name, phone=phone, service=service, day=_parse_date(date),
                         time=_parse_time(time), notes=notes, call_id=_call_id)
        return (
            f"Booked: {appt['service']} for {patient_name} on {_spoken_day(appt['starts_at'].date())} "
            f"at {_spoken_time(appt['starts_at'].time())}. The confirmation code is "
            f"{' '.join(appt['code'])}."
        )

    def find_appointment(phone: str, **_: Any) -> str:
        rows = book.find(phone)
        if not rows:
            return "I don't see any upcoming appointments for that number."
        parts = [
            f"{clinic.config['services'][r['service']]['label'].lower()} on "
            f"{_spoken_day(dt.datetime.fromisoformat(r['starts_at']).date())} at "
            f"{_spoken_time(dt.datetime.fromisoformat(r['starts_at']).time())}, code {' '.join(r['code'])}"
            for r in rows[:3]
        ]
        return "I found: " + "; ".join(parts) + "."

    def cancel_appointment(confirmation_code: str, **_: Any) -> str:
        row = book.cancel(confirmation_code.replace(" ", ""))
        start = dt.datetime.fromisoformat(row["starts_at"])
        return f"Done. The appointment on {_spoken_day(start.date())} at {_spoken_time(start.time())} is cancelled."

    def get_clinic_info(topic: str, **_: Any) -> str:
        topic = topic.strip().lower()
        cfg = clinic.config
        if topic in ("hours", "opening hours"):
            days = [f"{d.title()} {h[0]} to {h[1]}" for d, h in cfg["hours"].items() if h]
            return "We're open " + ", ".join(days) + ". Closed on weekends."
        if topic in ("address", "location", "directions"):
            return f"We're at {cfg['address']}. {cfg['faq']['parking']}"
        if topic in ("price", "prices", "pricing", "cost"):
            return "; ".join(f"{s['label']}: {s['price']}" for s in cfg["services"].values()) + "."
        if topic in cfg["faq"]:
            return cfg["faq"][topic]
        return "I don't have that information. I can take a message and the front desk will call you back."

    return {
        "check_availability": check_availability,
        "book_appointment": book_appointment,
        "find_appointment": find_appointment,
        "cancel_appointment": cancel_appointment,
        "get_clinic_info": get_clinic_info,
    }
