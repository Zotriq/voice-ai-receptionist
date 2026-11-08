"""Appointment book: opening hours, free slots, bookings and cancellations (SQLite)."""

from __future__ import annotations

import datetime as dt
import json
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

CLINIC_FILE = Path(__file__).with_name("clinic.json")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS appointments (
    code        TEXT PRIMARY KEY,
    patient     TEXT NOT NULL,
    phone       TEXT NOT NULL,
    service     TEXT NOT NULL,
    starts_at   TEXT NOT NULL,   -- local clinic time, ISO format
    ends_at     TEXT NOT NULL,
    notes       TEXT,
    status      TEXT NOT NULL DEFAULT 'booked',
    call_id     TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calls (
    call_id       TEXT PRIMARY KEY,
    caller        TEXT,
    ended_reason  TEXT,
    summary       TEXT,
    transcript    TEXT,
    recording_url TEXT,
    created_at    TEXT NOT NULL
);
"""


class BookingError(ValueError):
    """A problem the assistant should explain to the caller."""


@dataclass
class Clinic:
    config: dict

    @classmethod
    def load(cls, path: Path = CLINIC_FILE) -> "Clinic":
        return cls(json.loads(path.read_text()))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.config["timezone"])

    def now(self) -> dt.datetime:
        return dt.datetime.now(self.tz).replace(tzinfo=None)

    def service(self, key: str) -> dict:
        services = self.config["services"]
        key = (key or "").strip().lower()
        if key not in services:
            raise BookingError(f"Unknown service '{key}'. We offer: {', '.join(services)}.")
        return services[key]

    def open_hours(self, day: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
        hours = self.config["hours"][DAYS[day.weekday()]]
        if not hours:
            return None
        start, end = (dt.datetime.combine(day, dt.time.fromisoformat(h)) for h in hours)
        return start, end

    def in_lunch(self, start: dt.datetime, end: dt.datetime) -> bool:
        ls, le = (dt.datetime.combine(start.date(), dt.time.fromisoformat(h)) for h in self.config["lunch"])
        return start < le and end > ls


class AppointmentBook:
    def __init__(self, db_path: str | Path, clinic: Clinic | None = None):
        self.clinic = clinic or Clinic.load()
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---------- queries ----------
    def _booked(self, day: dt.date) -> list[tuple[dt.datetime, dt.datetime]]:
        rows = self.conn.execute(
            "SELECT starts_at, ends_at FROM appointments WHERE status = 'booked' AND substr(starts_at, 1, 10) = ?",
            (day.isoformat(),),
        )
        return [(dt.datetime.fromisoformat(r["starts_at"]), dt.datetime.fromisoformat(r["ends_at"])) for r in rows]

    def _is_free(self, start: dt.datetime, end: dt.datetime) -> bool:
        return all(not (start < b_end and end > b_start) for b_start, b_end in self._booked(start.date()))

    def free_slots(self, day: dt.date, service: str) -> list[dt.time]:
        minutes = self.clinic.service(service)["minutes"]
        hours = self.clinic.open_hours(day)
        if not hours:
            return []
        open_at, close_at = hours
        step = dt.timedelta(minutes=self.clinic.config["slot_minutes"])
        length = dt.timedelta(minutes=minutes)
        earliest = self.clinic.now() + dt.timedelta(hours=1)
        slots, t = [], open_at
        while t + length <= close_at:
            if t >= earliest and not self.clinic.in_lunch(t, t + length) and self._is_free(t, t + length):
                slots.append(t.time())
            t += step
        return slots

    def next_available(self, service: str, start_day: dt.date, days: int = 14) -> tuple[dt.date, list[dt.time]] | None:
        for i in range(days):
            day = start_day + dt.timedelta(days=i)
            slots = self.free_slots(day, service)
            if slots:
                return day, slots
        return None

    # ---------- changes ----------
    def book(self, *, patient: str, phone: str, service: str, day: dt.date, time: dt.time,
             notes: str = "", call_id: str | None = None) -> dict:
        info = self.clinic.service(service)
        if not patient.strip():
            raise BookingError("I need the patient's name to book.")
        if len("".join(ch for ch in phone if ch.isdigit())) < 7:
            raise BookingError("I need a valid phone number to book.")
        start = dt.datetime.combine(day, time)
        end = start + dt.timedelta(minutes=info["minutes"])
        if time not in self.free_slots(day, service):
            raise BookingError(f"{start:%A %B %-d at %-I:%M %p} is not available for a {info['label'].lower()}.")

        code = secrets.token_hex(3).upper()
        with self.conn:
            # Re-check inside the transaction so two callers can't take the same slot.
            self.conn.execute("BEGIN IMMEDIATE")
            if not self._is_free(start, end):
                raise BookingError("That time was just taken. Let me find another one.")
            self.conn.execute(
                "INSERT INTO appointments (code, patient, phone, service, starts_at, ends_at, notes, call_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code, patient.strip(), phone.strip(), service, start.isoformat(), end.isoformat(), notes, call_id,
                 dt.datetime.now(dt.timezone.utc).isoformat()),
            )
        return {"code": code, "service": info["label"], "starts_at": start, "ends_at": end}

    def find(self, phone: str) -> list[sqlite3.Row]:
        digits = "".join(ch for ch in phone if ch.isdigit())[-10:]
        rows = self.conn.execute(
            "SELECT * FROM appointments WHERE status = 'booked' AND starts_at >= ? ORDER BY starts_at",
            (self.clinic.now().isoformat(),),
        ).fetchall()
        return [r for r in rows if "".join(ch for ch in r["phone"] if ch.isdigit()).endswith(digits)]

    def cancel(self, code: str) -> sqlite3.Row:
        code = code.strip().upper()
        row = self.conn.execute("SELECT * FROM appointments WHERE code = ? AND status = 'booked'", (code,)).fetchone()
        if not row:
            raise BookingError(f"I couldn't find an active appointment with code {code}.")
        with self.conn:
            self.conn.execute("UPDATE appointments SET status = 'cancelled' WHERE code = ?", (code,))
        return row

    def log_call(self, *, call_id: str, caller: str | None, ended_reason: str | None, summary: str | None,
                 transcript: str | None, recording_url: str | None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO calls VALUES (?, ?, ?, ?, ?, ?, ?)",
                (call_id, caller, ended_reason, summary, transcript, recording_url,
                 dt.datetime.now(dt.timezone.utc).isoformat()),
            )
