"""Booking engine: availability, conflict detection, booking, cancel.

Real work against the real store: a booking is either a row in
appointments with a confirmation code, or it did not happen. Overlap
semantics: an appointment occupies [start, end + buffer). Candidate
slots must fit inside business hours, clear the break, and not
intersect any existing appointment's occupied interval for that
staff member.
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import date, datetime, time, timedelta

from . import audit

GRID_MIN = 15  # slot grid


def _t(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%d %H:%M")


def get_service(conn, tenant_id: int, service_id: int):
    return conn.execute(
        "SELECT * FROM services WHERE tenant_id = ? AND id = ?",
        (tenant_id, service_id)).fetchone()


def staff_for_service(conn, tenant_id: int, service) -> list:
    return conn.execute(
        "SELECT * FROM staff WHERE tenant_id = ? AND role = ?"
        " ORDER BY id", (tenant_id, service["staff_role"])).fetchall()


def _occupied(conn, tenant_id: int, staff_id: int, day: date):
    """(start, end, occupied_end) per booked appointment for a staff
    member/day, where occupied_end = end + that service's buffer."""
    rows = conn.execute(
        "SELECT a.start_ts, a.end_ts, s.buffer_min FROM appointments a"
        " JOIN services s ON s.id = a.service_id"
        " WHERE a.tenant_id = ? AND a.staff_id = ?"
        " AND a.status = 'booked' AND substr(a.start_ts, 1, 10) = ?",
        (tenant_id, staff_id, day.isoformat())).fetchall()
    out = []
    for r in rows:
        end = _parse(r["end_ts"])
        out.append((_parse(r["start_ts"]), end,
                    end + timedelta(minutes=r["buffer_min"])))
    return out


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def _clashes(start: datetime, end: datetime, buffer_min: int,
             occupied: list) -> bool:
    """True if a candidate appointment (with its own buffer) collides
    with any existing appointment or that appointment's buffer."""
    cand_occ_end = end + timedelta(minutes=buffer_min)
    for o_s, o_e, o_occ in occupied:
        if _overlaps(start, cand_occ_end, o_s, o_e):
            return True
        if _overlaps(start, end, o_s, o_occ):
            return True
    return False


def slots_for_date(conn, tenant_id: int, service, day: date,
                   staff_id: int | None = None) -> list[dict]:
    """All bookable slots for a service on a date, per staff member."""
    hours = conn.execute(
        "SELECT * FROM business_hours WHERE tenant_id = ? AND weekday = ?",
        (tenant_id, day.weekday())).fetchone()
    if hours is None or hours["open"] is None:
        return []
    open_dt = datetime.combine(day, _t(hours["open"]))
    close_dt = datetime.combine(day, _t(hours["close"]))
    brk = None
    if hours["break_start"]:
        brk = (datetime.combine(day, _t(hours["break_start"])),
               datetime.combine(day, _t(hours["break_end"])))
    dur = timedelta(minutes=service["duration_min"])
    staff_rows = staff_for_service(conn, tenant_id, service)
    if staff_id is not None:
        staff_rows = [s for s in staff_rows if s["id"] == staff_id]
    slots: list[dict] = []
    for member in staff_rows:
        occupied = _occupied(conn, tenant_id, member["id"], day)
        start = open_dt
        while start + dur <= close_dt:
            end = start + dur
            if brk and _overlaps(start, end, brk[0], brk[1]):
                start += timedelta(minutes=GRID_MIN)
                continue
            if _clashes(start, end, service["buffer_min"], occupied):
                start += timedelta(minutes=GRID_MIN)
                continue
            slots.append({"staff_id": member["id"],
                          "staff_name": member["name"],
                          "start": _fmt(start), "end": _fmt(end)})
            start += timedelta(minutes=GRID_MIN)
    slots.sort(key=lambda s: (s["start"], s["staff_id"]))
    return slots


def _conflict(conn, tenant_id: int, service, staff_id: int,
              start: datetime, end: datetime) -> bool:
    occupied = _occupied(conn, tenant_id, staff_id, start.date())
    return _clashes(start, end, service["buffer_min"], occupied)


def book(conn, tenant_id: int, service_id: int, start_ts: str,
         customer_name: str, customer_phone: str, channel: str = "web",
         session_id: str = "api", staff_id: int | None = None) -> dict:
    """Attempt a booking. Returns {ok, confirmation_code, ...} or
    {ok: False, error, alternatives} — never a simulated success."""
    service = get_service(conn, tenant_id, service_id)
    if service is None:
        return {"ok": False, "error": "unknown_service"}
    try:
        start = _parse(start_ts)
    except ValueError:
        return {"ok": False, "error": "bad_start_format"}
    end = start + timedelta(minutes=service["duration_min"])

    day_slots = slots_for_date(conn, tenant_id, service, start.date())
    candidates = [s for s in day_slots if s["start"] == _fmt(start)]
    if staff_id is not None:
        candidates = [s for s in candidates if s["staff_id"] == staff_id]
    if not candidates:
        alts = day_slots[:3]
        audit.log_interaction(
            conn, tenant_id, session_id, channel, "system",
            f"Booking refused (unavailable) {service['name']} {start_ts}",
            intent="booking", outcome="conflict",
            detail="slot_unavailable")
        return {"ok": False, "error": "slot_unavailable",
                "alternatives": alts}

    chosen = candidates[0]
    conn.execute("BEGIN IMMEDIATE")
    try:
        if _conflict(conn, tenant_id, service, chosen["staff_id"],
                     start, end):
            conn.execute("ROLLBACK")
            audit.log_interaction(
                conn, tenant_id, session_id, channel, "system",
                f"Booking conflict {service['name']} {start_ts}",
                intent="booking", outcome="conflict",
                detail="overlap_detected")
            return {"ok": False, "error": "conflict",
                    "alternatives": day_slots[:3]}
        code = "RD-" + secrets.token_hex(3).upper()
        conn.execute(
            "INSERT INTO appointments (tenant_id, service_id, staff_id,"
            " customer_name, customer_phone, start_ts, end_ts, status,"
            " confirmation_code, channel, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'booked', ?, ?, ?)",
            (tenant_id, service_id, chosen["staff_id"], customer_name,
             customer_phone, _fmt(start), _fmt(end), code, channel,
             audit.now_iso()))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    audit.log_interaction(
        conn, tenant_id, session_id, channel, "system",
        f"Booked {service['name']} {start_ts} for {customer_name} ({code})",
        intent="booking", outcome="booked", detail=code)
    return {"ok": True, "confirmation_code": code,
            "staff_name": chosen["staff_name"],
            "start": _fmt(start), "end": _fmt(end),
            "service": service["name"]}


def cancel(conn, tenant_id: int, confirmation_code: str,
           session_id: str = "api", channel: str = "web") -> dict:
    row = conn.execute(
        "SELECT * FROM appointments WHERE tenant_id = ?"
        " AND confirmation_code = ?", (tenant_id, confirmation_code)
        ).fetchone()
    if row is None:
        return {"ok": False, "error": "unknown_code"}
    if row["status"] != "booked":
        return {"ok": False, "error": "not_booked"}
    conn.execute(
        "UPDATE appointments SET status = 'cancelled' WHERE id = ?",
        (row["id"],))
    conn.commit()
    audit.log_interaction(
        conn, tenant_id, session_id, channel, "system",
        f"Cancelled appointment {confirmation_code}",
        intent="cancellation", outcome="cancelled",
        detail=confirmation_code)
    return {"ok": True, "confirmation_code": confirmation_code}
