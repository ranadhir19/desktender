"""Simulated front door: the caller-facing conversation.

A deterministic state machine that exercises the REAL booking engine
and the REAL grounded Q&A — the same code paths the telephony webhooks
will call in production. Every turn is written to the audit trail.
Booking is a guided flow (service -> date -> slot -> name -> phone ->
confirm); questions go to the KB; anything the KB cannot cover is
refused with a human path, never improvised.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta

from . import audit, engine, qa

_SESSIONS: dict[str, dict] = {}

BOOK_WORDS = ("book", "appointment", "schedule", "reserve", "see the",
              "checkup", "cleaning", "visit")
HUMAN_WORDS = ("human", "real person", "receptionist", "someone real",
               "call me back", "callback", "call back")
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def reset_sessions() -> None:
    _SESSIONS.clear()


def _session(tenant_id: int, session_id: str) -> dict:
    key = f"{tenant_id}:{session_id}"
    if key not in _SESSIONS:
        _SESSIONS[key] = {"stage": "idle"}
    return _SESSIONS[key]


def _phone_in(text: str) -> str | None:
    m = re.search(r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
                  text)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    return digits[-10:]


def _name_in(text: str) -> str | None:
    m = re.search(r"(?:my name is|i am|i'm|this is)\s+([A-Z][a-z]+"
                  r"(?:\s+[A-Z][a-z]+)?)", text)
    return m.group(1) if m else None


def parse_service(conn, tenant_id: int, text: str):
    """Match a service by token overlap: name tokens weigh double,
    keyword tokens single; ties break toward the more specific
    (smaller) keyword set, then lowest id. Requires a real signal."""
    tokens = set(re.findall(r"[a-z]+", text.lower()))
    services = conn.execute(
        "SELECT * FROM services WHERE tenant_id = ? ORDER BY id",
        (tenant_id,)).fetchall()
    best, best_key = None, None
    for svc in services:
        name_tokens = set(re.findall(r"[a-z]+", svc["name"].lower()))
        kw_tokens = set(svc["keywords"].split())
        score = 2 * len(tokens & name_tokens) + len(tokens & kw_tokens)
        if svc["name"].lower() in text.lower():
            score += 5
        key = (score, -len(kw_tokens), -svc["id"])
        if score >= 2 and (best_key is None or key > best_key):
            best, best_key = svc, key
    return best


def parse_date(text: str, today: date) -> date | None:
    low = text.lower()
    if "today" in low:
        return today
    if "tomorrow" in low:
        return today + timedelta(days=1)
    m = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", low)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                  r"[a-z]*\s+(\d{1,2})\b", low)
    if m:
        month, day = MONTHS[m.group(1)], int(m.group(2))
        year = today.year + (1 if (month, day) < (today.month, today.day)
                             else 0)
        return date(year, month, day)
    for name, wd in WEEKDAYS.items():
        if name in low:
            delta = (wd - today.weekday()) % 7
            return today + timedelta(days=delta)
    return None


def _time_pref(text: str) -> str | None:
    low = text.lower()
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", low)
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0)
        return f"{h:02d}:{m.group(2) or '00'}"
    if "morning" in low:
        return "morning"
    if "afternoon" in low:
        return "afternoon"
    return None


def _present_slots(state: dict, slots: list[dict], pref: str | None):
    if pref == "morning":
        slots = [s for s in slots if s["start"][11:16] < "12:00"] or slots
    elif pref == "afternoon":
        slots = [s for s in slots if s["start"][11:16] >= "12:00"] or slots
    elif pref:  # exact 'HH:MM' preference floats to the front
        slots = sorted(slots,
                       key=lambda s: s["start"][11:16] != pref)
    shown = slots[:6]
    state["slots"] = shown
    lines = [f"{i + 1}. {s['start'][11:16]} with {s['staff_name']}"
             for i, s in enumerate(shown)]
    return shown, lines


def _reply(conn, tenant, session_id, channel, text_in, reply, intent,
           outcome, detail=""):
    audit.log_interaction(conn, tenant["id"], session_id, channel,
                          "assistant", reply, intent=intent,
                          outcome=outcome, detail=detail)
    return {"reply": reply, "intent": intent, "outcome": outcome,
            "detail": detail}


def _start_booking(conn, tenant, session_id, channel, text, today,
                   state):
    svc = parse_service(conn, tenant["id"], text)
    services = conn.execute(
        "SELECT * FROM services WHERE tenant_id = ? ORDER BY id",
        (tenant["id"],)).fetchall()
    if svc is None:
        state.update({"stage": "need_service"})
        listing = "; ".join(f"{s['name']} ({s['price_text']})"
                            for s in services)
        return _reply(conn, tenant, session_id, channel, text,
                      "I can book that for you. Which service do you "
                      f"need? We offer: {listing}.",
                      "booking", "info", "need_service")
    state.update({"stage": "need_date", "service_id": svc["id"]})
    day = parse_date(text, today)
    if day is not None:
        return _offer_slots(conn, tenant, session_id, channel, text,
                            today, state, day)
    return _reply(conn, tenant, session_id, channel, text,
                  f"Great — {svc['name']}. Which day works for you?",
                  "booking", "info", "need_date")


def _offer_slots(conn, tenant, session_id, channel, text, today, state,
                 day):
    svc = engine.get_service(conn, tenant["id"], state["service_id"])
    slots = engine.slots_for_date(conn, tenant["id"], svc, day)
    if not slots:
        state["stage"] = "need_date"
        return _reply(conn, tenant, session_id, channel, text,
                      f"I'm sorry, we have no {svc['name']} openings on "
                      f"{day.isoformat()}. Would another day work?",
                      "booking", "info", "no_slots")
    state["date"] = day.isoformat()
    shown, lines = _present_slots(state, slots, _time_pref(text))
    state["stage"] = "need_slot"
    return _reply(conn, tenant, session_id, channel, text,
                  f"For {svc['name']} on {day.isoformat()} I have:\n"
                  + "\n".join(lines)
                  + "\nReply with a number to choose.",
                  "booking", "info", "slots_offered")


def _booking_flow(conn, tenant, session_id, channel, text, today, state):
    low = text.lower()
    if any(w in low for w in ("never mind", "nevermind", "stop",
                              "cancel that", "forget it")):
        state.clear()
        state["stage"] = "idle"
        return _reply(conn, tenant, session_id, channel, text,
                      "No problem — I've cancelled that booking request. "
                      "Is there anything else I can help with?",
                      "booking", "info", "flow_aborted")
    stage = state.get("stage")
    if stage == "need_service":
        svc = parse_service(conn, tenant["id"], text)
        if svc is None:
            return _reply(conn, tenant, session_id, channel, text,
                          "I didn't catch which service you need — "
                          "could you say it again?",
                          "booking", "info", "need_service")
        state.update({"stage": "need_date", "service_id": svc["id"]})
        day = parse_date(text, today)
        if day is not None:
            return _offer_slots(conn, tenant, session_id, channel, text,
                                today, state, day)
        return _reply(conn, tenant, session_id, channel, text,
                      f"And which day would you like the {svc['name']}?",
                      "booking", "info", "need_date")
    if stage == "need_date":
        day = parse_date(text, today)
        if day is None:
            return _reply(conn, tenant, session_id, channel, text,
                          "Which day works for you? You can say a date "
                          "like 2026-11-16, or 'tomorrow'.",
                          "booking", "info", "need_date")
        return _offer_slots(conn, tenant, session_id, channel, text,
                            today, state, day)
    if stage == "need_slot":
        slots = state.get("slots", [])
        choice = None
        m = re.search(r"\b(\d)\b", text)
        pref = _time_pref(text)
        if m and 1 <= int(m.group(1)) <= len(slots):
            choice = slots[int(m.group(1)) - 1]
        elif pref and pref not in ("morning", "afternoon"):
            for s in slots:
                if s["start"][11:16] == pref:
                    choice = s
                    break
        if choice is None:
            return _reply(conn, tenant, session_id, channel, text,
                          "Please reply with the number of the time "
                          "you'd like.", "booking", "info", "need_slot")
        state.update({"stage": "need_name", "slot": choice})
        return _reply(conn, tenant, session_id, channel, text,
                      f"{choice['start'][11:16]} with "
                      f"{choice['staff_name']} — great. What name "
                      "should I book it under?",
                      "booking", "info", "need_name")
    if stage == "need_name":
        name = text.strip().title()
        if len(name) < 2:
            return _reply(conn, tenant, session_id, channel, text,
                          "What name should I book it under?",
                          "booking", "info", "need_name")
        state.update({"stage": "need_phone", "name": name})
        return _reply(conn, tenant, session_id, channel, text,
                      f"Thanks, {name.split()[0]}. What's the best "
                      "phone number for your confirmation text?",
                      "booking", "info", "need_phone")
    if stage == "need_phone":
        phone = _phone_in(text)
        if phone is None:
            return _reply(conn, tenant, session_id, channel, text,
                          "I need a 10-digit phone number for the "
                          "confirmation text.", "booking", "info",
                          "need_phone")
        slot = state["slot"]
        svc = engine.get_service(conn, tenant["id"], state["service_id"])
        state.update({"stage": "need_confirm", "phone": phone})
        return _reply(conn, tenant, session_id, channel, text,
                      f"To confirm: {svc['name']} on {slot['start']} "
                      f"with {slot['staff_name']}, for {state['name']}, "
                      f"phone {phone}. Reply YES to book it.",
                      "booking", "info", "need_confirm")
    if stage == "need_confirm":
        if low.startswith(("yes", "y", "confirm", "book it", "ok")):
            slot = state["slot"]
            result = engine.book(
                conn, tenant["id"], state["service_id"], slot["start"],
                state["name"], state["phone"], channel=channel,
                session_id=session_id, staff_id=slot["staff_id"])
            name = state["name"]
            state.clear()
            state["stage"] = "idle"
            if result["ok"]:
                return _reply(
                    conn, tenant, session_id, channel, text,
                    f"You're booked, {name.split()[0]}! "
                    f"{result['service']} on {result['start']} with "
                    f"{result['staff_name']}. Your confirmation code "
                    f"is {result['confirmation_code']}. We'll text a "
                    "reminder 24 hours before.",
                    "booking", "booked", result["confirmation_code"])
            return _reply(
                conn, tenant, session_id, channel, text,
                "I'm sorry — that time was just taken. Please start "
                "again and I'll find you the next opening.",
                "booking", "conflict", result.get("error", ""))
        state.clear()
        state["stage"] = "idle"
        return _reply(conn, tenant, session_id, channel, text,
                      "Okay, I haven't booked anything. Let me know "
                      "if you'd like a different time.",
                      "booking", "info", "flow_aborted")
    state.clear()
    state["stage"] = "idle"
    return _reply(conn, tenant, session_id, channel, text,
                  "Let me help with that — would you like to book an "
                  "appointment, or ask a question?",
                  "unknown", "info", "reset")


def handle_message(conn, tenant, session_id: str, text: str,
                   channel: str = "chat", today: date | None = None
                   ) -> dict:
    """One caller turn. Logs both sides to the audit trail."""
    today = today or date.today()
    text = (text or "").strip()
    audit.log_interaction(conn, tenant["id"], session_id, channel,
                          "caller", text)
    state = _session(tenant["id"], session_id)
    low = text.lower()

    # an in-progress booking flow owns the turn
    if state.get("stage") not in (None, "idle"):
        return _booking_flow(conn, tenant, session_id, channel, text,
                             today, state)

    # cancellation by confirmation code
    code_m = re.search(r"\bRD-[0-9A-F]{6}\b", text.upper())
    if "cancel" in low and code_m:
        result = engine.cancel(conn, tenant["id"], code_m.group(0),
                               session_id=session_id, channel=channel)
        if result["ok"]:
            return _reply(conn, tenant, session_id, channel, text,
                          f"Done — appointment {code_m.group(0)} is "
                          "cancelled. We hope to see you soon.",
                          "cancellation", "cancelled", code_m.group(0))
        return _reply(conn, tenant, session_id, channel, text,
                      "I couldn't find a booked appointment with that "
                      "code. Could you check it and try again?",
                      "cancellation", "info", "unknown_code")

    # human / callback escalation -> structured lead
    if any(w in low for w in HUMAN_WORDS):
        phone = _phone_in(text) or ""
        name = _name_in(text) or ""
        lead_id = audit.create_lead(
            conn, tenant["id"], name, phone, "callback_request",
            f"Caller asked for a human callback: '{text}'")
        audit.log_interaction(conn, tenant["id"], session_id, channel,
                              "system", f"Lead {lead_id} captured "
                              "(callback request)", intent="escalation",
                              outcome="lead", detail=str(lead_id))
        return _reply(conn, tenant, session_id, channel, text,
                      "Of course — I've passed your request to the "
                      f"team at {tenant['name']} and they'll call you "
                      f"back. If it's urgent, you can also reach them "
                      f"now at {tenant['phone']}.",
                      "escalation", "escalated", f"lead:{lead_id}")

    # booking intent
    if any(w in low for w in BOOK_WORDS) or "book" in low:
        return _start_booking(conn, tenant, session_id, channel, text,
                              today, state)

    # everything else: grounded Q&A (which refuses what it can't cover)
    result = qa.answer(conn, tenant, text, session_id=session_id)
    return _reply(conn, tenant, session_id, channel, text,
                  result["answer"], "question", result["outcome"],
                  result.get("reason") or f"kb:{result['kb_entry_id']}")
