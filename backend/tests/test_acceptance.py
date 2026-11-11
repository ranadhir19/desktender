"""Domain-authored acceptance tests for ReceptDesk.

The questions are authored from the clinic's own topic catalogue —
hours, prices, insurance, policies, emergencies — and from the
requests a dental front desk must NOT answer (services it does not
offer, diagnosis). Booking tests use the clinic's real rules: staff
calendars, lunch break, per-service buffers, Sunday closure.
Dates: 2026-11-16 is a Monday; 2026-11-15 a Sunday; 2026-11-14 a
Saturday (open 09:00-13:00).
"""

from __future__ import annotations

from datetime import date

import pytest

from app import audit, engine, frontdoor, qa

MON = date(2026, 11, 16)
SAT = date(2026, 11, 14)
SUN = date(2026, 11, 15)


# ------------------------------------------------ booking rules

def test_slots_respect_hours_break_and_close(conn, tenant, services):
    svc = services["Routine Cleaning"]
    slots = engine.slots_for_date(conn, tenant["id"], svc, MON)
    assert slots, "expected Monday cleaning slots"
    for s in slots:
        start, end = s["start"][11:16], s["end"][11:16]
        assert "08:00" <= start and end <= "17:00"
        # no appointment may span the 12:00-13:00 lunch break
        assert end <= "12:00" or start >= "13:00"


def test_no_slots_on_sunday(conn, tenant, services):
    svc = services["Routine Cleaning"]
    assert engine.slots_for_date(conn, tenant["id"], svc, SUN) == []


def test_saturday_shorter_day(conn, tenant, services):
    svc = services["Routine Cleaning"]
    slots = engine.slots_for_date(conn, tenant["id"], svc, SAT)
    assert slots and all("09:00" <= s["start"][11:16]
                         and s["end"][11:16] <= "13:00" for s in slots)


def test_booking_lands_in_store_with_confirmation(conn, tenant, services):
    svc = services["Routine Cleaning"]
    slot = engine.slots_for_date(conn, tenant["id"], svc, MON)[0]
    r = engine.book(conn, tenant["id"], svc["id"], slot["start"],
                    "Jamie Lee", "9045550100",
                    staff_id=slot["staff_id"])
    assert r["ok"] and r["confirmation_code"].startswith("RD-")
    row = conn.execute(
        "SELECT * FROM appointments WHERE confirmation_code = ?",
        (r["confirmation_code"],)).fetchone()
    assert row is not None and row["status"] == "booked"
    assert row["customer_name"] == "Jamie Lee"


def test_conflicting_booking_fails_and_offers_alternatives(
        conn, tenant, services):
    svc = services["Routine Cleaning"]
    slot = engine.slots_for_date(conn, tenant["id"], svc, MON)[0]
    first = engine.book(conn, tenant["id"], svc["id"], slot["start"],
                        "Jamie Lee", "9045550100",
                        staff_id=slot["staff_id"])
    assert first["ok"]
    # same staff, same time -> must not double-book
    again = engine.book(conn, tenant["id"], svc["id"], slot["start"],
                        "Other Person", "9045550101",
                        staff_id=slot["staff_id"])
    assert not again["ok"] and again["alternatives"]
    # same staff, overlapping start (15 min into the appointment)
    overlap = engine.book(conn, tenant["id"], svc["id"],
                          slot["start"][:14] + "15", "Third Person",
                          "9045550102", staff_id=slot["staff_id"])
    assert not overlap["ok"]
    # the other hygienist at the same time is fine
    other = engine.book(conn, tenant["id"], svc["id"], slot["start"],
                        "Fourth Person", "9045550103")
    assert other["ok"] and other["staff_name"] != first["staff_name"]


def test_buffer_blocks_slot_too_close_before_next(conn, tenant, services):
    svc = services["Routine Cleaning"]  # 45 min + 10 min buffer
    slots = engine.slots_for_date(conn, tenant["id"], svc, MON)
    target = next(s for s in slots if s["start"].endswith("10:00"))
    r = engine.book(conn, tenant["id"], svc["id"], target["start"],
                    "Anchor", "9045550110", staff_id=target["staff_id"])
    assert r["ok"]
    after = engine.slots_for_date(conn, tenant["id"], svc, MON)
    starts = {(s["staff_id"], s["start"]) for s in after}
    # 09:15 start would end 10:00 but its buffer runs to 10:10 -> gone
    assert (target["staff_id"], "2026-11-16 09:15") not in starts
    # 09:05 is not on the 15-minute grid; 09:00 ends 09:45 + buffer
    # 09:55 <= 10:00 -> still available
    assert (target["staff_id"], "2026-11-16 09:00") in starts


def test_booking_on_sunday_fails(conn, tenant, services):
    svc = services["Routine Cleaning"]
    r = engine.book(conn, tenant["id"], svc["id"], "2026-11-15 10:00",
                    "Sunday Caller", "9045550120")
    assert not r["ok"]


def test_cancel_frees_the_slot(conn, tenant, services):
    svc = services["Routine Cleaning"]
    slot = engine.slots_for_date(conn, tenant["id"], svc, MON)[0]
    r = engine.book(conn, tenant["id"], svc["id"], slot["start"],
                    "Jamie Lee", "9045550100",
                    staff_id=slot["staff_id"])
    assert engine.cancel(conn, tenant["id"], r["confirmation_code"])["ok"]
    freed = engine.slots_for_date(conn, tenant["id"], svc, MON)
    assert any(s["start"] == slot["start"]
               and s["staff_id"] == slot["staff_id"] for s in freed)


# ------------------------------------------------ grounded Q&A

KB_QUESTIONS = [
    ("What are your opening hours on Saturday?", "hours"),
    ("Where are you located, and is there parking?", "location"),
    ("How much does a routine cleaning cost?", "prices"),
    ("Do you accept Delta Dental insurance?", "insurance"),
    ("What is your cancellation policy?", "cancellation"),
    ("I am a new patient — what should I bring?", "new patient"),
    ("Do you hold emergency slots for tooth pain?", "emergency"),
    ("Can I pay with CareCredit?", "payment"),
    ("Do you speak Spanish?", "languages"),
    ("Do you see children?", "children"),
    ("Will I get a text reminder before my appointment?", "reminders"),
    ("Are digital x-rays safe?", "xrays"),
]


@pytest.mark.parametrize("question,topic", KB_QUESTIONS)
def test_kb_questions_answered_from_kb_text(conn, tenant, question,
                                            topic):
    res = qa.answer(conn, tenant, question, session_id="acc")
    assert res["outcome"] == "answered", res
    entry = conn.execute("SELECT * FROM kb_entries WHERE id = ?",
                         (res["kb_entry_id"],)).fetchone()
    assert entry["topic"] == topic
    # the spoken answer IS the owner's KB text, verbatim
    assert res["answer"] == entry["answer"]


NEAR_MISS = [
    "How much are dental implants?",          # price vocab, not offered
    "Do you offer root canal treatment?",     # treatment vocab, not offered
    "Can I get braces for my teenager?",      # children vocab, not offered
    "Do you do wisdom tooth removal?",        # emergency vocab, not offered
]


@pytest.mark.parametrize("question", NEAR_MISS)
def test_near_miss_questions_refuse(conn, tenant, question):
    res = qa.answer(conn, tenant, question, session_id="acc")
    assert res["outcome"] == "refused" and res["reason"] == "never_offer"
    assert tenant["phone"] in res["answer"]  # human path is given
    gap = conn.execute(
        "SELECT * FROM kb_gaps WHERE question = ?",
        (question,)).fetchone()
    assert gap is not None and gap["reason"] == "never_offer"


def test_medical_advice_refused(conn, tenant):
    res = qa.answer(conn, tenant, "Why does my tooth hurt?",
                    session_id="acc")
    assert res["outcome"] == "refused"
    assert res["reason"] == "medical_advice"


def test_unrelated_question_refused_not_improvised(conn, tenant):
    res = qa.answer(conn, tenant, "Do you sell cars?", session_id="acc")
    assert res["outcome"] == "refused" and res["reason"] == "no_match"
    gaps = conn.execute("SELECT COUNT(*) c FROM kb_gaps").fetchone()["c"]
    assert gaps >= 1  # owner gets the KB-missing report


# ------------------------------------------------ front door E2E

def test_scripted_call_books_cleaning(conn, tenant):
    frontdoor.reset_sessions()
    today = date(2026, 11, 15)  # Sunday; 'tomorrow' is the open Monday
    sid = "call-1"
    r1 = frontdoor.handle_message(
        conn, tenant, sid, "Hi, I'd like to book a cleaning for "
        "tomorrow morning", today=today)
    assert r1["intent"] == "booking" and "1." in r1["reply"]
    r2 = frontdoor.handle_message(conn, tenant, sid, "1", today=today)
    assert "name" in r2["reply"].lower()
    r3 = frontdoor.handle_message(conn, tenant, sid, "Maria Gomez",
                                  today=today)
    assert "phone" in r3["reply"].lower()
    r4 = frontdoor.handle_message(conn, tenant, sid, "904-555-0177",
                                  today=today)
    assert "confirm" in r4["reply"].lower()
    r5 = frontdoor.handle_message(conn, tenant, sid, "yes", today=today)
    assert r5["outcome"] == "booked"
    code = r5["detail"]
    row = conn.execute(
        "SELECT * FROM appointments WHERE confirmation_code = ?",
        (code,)).fetchone()
    assert row is not None and row["customer_name"] == "Maria Gomez"
    assert row["customer_phone"] == "9045550177"
    assert row["start_ts"].startswith("2026-11-16")


def test_scripted_call_question_then_refusal(conn, tenant):
    frontdoor.reset_sessions()
    sid = "call-2"
    r = frontdoor.handle_message(conn, tenant, sid,
                                 "What are your opening hours?")
    assert r["outcome"] == "answered"
    r = frontdoor.handle_message(conn, tenant, sid,
                                 "How much are implants?")
    assert r["outcome"] == "refused"


def test_callback_request_captures_lead(conn, tenant):
    frontdoor.reset_sessions()
    r = frontdoor.handle_message(
        conn, tenant, "call-3",
        "Can I talk to a real person? Please call me back at "
        "904-555-0199, my name is Tom Baker")
    assert r["outcome"] == "escalated"
    lead = conn.execute("SELECT * FROM leads").fetchone()
    assert lead is not None and lead["phone"] == "9045550199"
    assert lead["intent"] == "callback_request"


def test_frontdoor_cancel_by_code(conn, tenant, services):
    svc = services["Routine Cleaning"]
    slot = engine.slots_for_date(conn, tenant["id"], svc, MON)[0]
    r = engine.book(conn, tenant["id"], svc["id"], slot["start"],
                    "Jamie Lee", "9045550100",
                    staff_id=slot["staff_id"])
    frontdoor.reset_sessions()
    resp = frontdoor.handle_message(
        conn, tenant, "call-4",
        f"Please cancel my appointment {r['confirmation_code']}")
    assert resp["outcome"] == "cancelled"
    row = conn.execute(
        "SELECT status FROM appointments WHERE confirmation_code = ?",
        (r["confirmation_code"],)).fetchone()
    assert row["status"] == "cancelled"


# ------------------------------------------------ audit integrity

def test_audit_trail_matches_what_happened(conn, tenant):
    frontdoor.reset_sessions()
    today = date(2026, 11, 15)
    frontdoor.handle_message(conn, tenant, "audit-1",
                             "What are your opening hours?", today=today)
    frontdoor.handle_message(conn, tenant, "audit-1",
                             "Do you sell cars?", today=today)
    outcomes = [r["outcome"] for r in conn.execute(
        "SELECT outcome FROM interactions WHERE speaker = 'assistant'"
        " ORDER BY id").fetchall()]
    assert outcomes == ["answered", "refused"]
    booked = engine.book(
        conn, tenant["id"],
        conn.execute("SELECT id FROM services LIMIT 1").fetchone()["id"],
        "2026-11-16 08:00", "Audit Person", "9045550130",
        session_id="audit-1")
    assert booked["ok"]
    sysrows = conn.execute(
        "SELECT outcome, detail FROM interactions WHERE speaker ="
        " 'system' AND outcome = 'booked'").fetchall()
    assert len(sysrows) == 1
    assert sysrows[0]["detail"] == booked["confirmation_code"]


# ------------------------------------------------ tenant boundary

def test_tenant_isolation(conn, tenant, services):
    cur = conn.execute(
        "INSERT INTO tenants (slug, name) VALUES ('other-clinic',"
        " 'Other Clinic')")
    other_id = cur.lastrowid
    conn.commit()
    svc = services["Routine Cleaning"]
    slot = engine.slots_for_date(conn, tenant["id"], svc, MON)[0]
    r = engine.book(conn, tenant["id"], svc["id"], slot["start"],
                    "Jamie Lee", "9045550100",
                    staff_id=slot["staff_id"])
    assert r["ok"]
    # the other tenant sees none of Brightpath's appointments or gaps
    n = conn.execute(
        "SELECT COUNT(*) c FROM appointments WHERE tenant_id = ?",
        (other_id,)).fetchone()["c"]
    assert n == 0
    # and Brightpath's confirmation code means nothing to the other
    res = engine.cancel(conn, other_id, r["confirmation_code"])
    assert not res["ok"]
    row = conn.execute(
        "SELECT status FROM appointments WHERE confirmation_code = ?",
        (r["confirmation_code"],)).fetchone()
    assert row["status"] == "booked"
