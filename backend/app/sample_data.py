"""Sample tenant configuration: Brightpath Dental Studio.

This is the business's own supplied data — services, staff, hours and
the knowledge base the front desk is allowed to answer from. The Q&A
engine may only speak from these KB answers; anything else is refused
and logged as a knowledge gap for the owner.
"""

from __future__ import annotations

import sqlite3

TENANT = {
    "slug": "brightpath-dental",
    "name": "Brightpath Dental Studio",
    "timezone": "America/New_York",
    "phone": "(904) 555-0147",
    "address": "12804 Village Parkway, Jacksonville, FL 32256",
}

STAFF = [
    ("Dr. Maya Chen", "dentist"),
    ("Dr. Luis Ortega", "dentist"),
    ("Priya Nair", "hygienist"),
    ("Sam Whitfield", "hygienist"),
]

# name, duration_min, buffer_min, price_text, staff_role, keywords
SERVICES = [
    ("New Patient Exam & Cleaning", 60, 10,
     "$89 for new patients (exam, digital x-rays and cleaning)",
     "dentist", "new patient exam cleaning first visit checkup"),
    ("Routine Cleaning", 45, 10, "$120", "hygienist",
     "cleaning hygiene polish routine"),
    ("Filling", 60, 10, "from $180 per tooth", "dentist",
     "filling cavity fill"),
    ("Crown Consultation", 30, 5, "free consultation", "dentist",
     "crown consultation consult"),
    ("Teeth Whitening", 45, 10, "$299", "hygienist",
     "whitening bleach brighter white teeth"),
    ("Emergency Tooth Pain Visit", 30, 5,
     "$65 emergency exam (treatment quoted before any work)",
     "dentist", "emergency toothache pain urgent hurt"),
]

# weekday 0=Mon..6=Sun -> (open, close, break_start, break_end); None = closed
HOURS = {
    0: ("08:00", "17:00", "12:00", "13:00"),
    1: ("08:00", "17:00", "12:00", "13:00"),
    2: ("08:00", "17:00", "12:00", "13:00"),
    3: ("08:00", "17:00", "12:00", "13:00"),
    4: ("08:00", "17:00", "12:00", "13:00"),
    5: ("09:00", "13:00", None, None),
    6: (None, None, None, None),
}

# topic, trigger tokens, answer (verbatim owner-supplied text)
KB = [
    ("hours", "hours open close opening when saturday sunday weekend",
     "We are open Monday to Friday, 8:00 AM to 5:00 PM, and Saturday "
     "9:00 AM to 1:00 PM. We are closed on Sundays."),
    ("location", "where located address parking directions drive",
     "You will find us at 12804 Village Parkway, Jacksonville, FL 32256. "
     "Free parking is available in the lot behind the building."),
    ("prices", "cost price prices much fee charge expensive",
     "Our current prices: new patient exam and cleaning $89; routine "
     "cleaning $120; fillings from $180 per tooth; crown consultation is "
     "free; teeth whitening $299; emergency tooth pain visit $65 for the "
     "exam. We always confirm the full cost with you before treatment."),
    ("insurance", "insurance delta cigna aetna metlife cover accepted plan",
     "We accept Delta Dental, Cigna and Aetna PPO plans. Bring your "
     "insurance card to your visit and we will verify your coverage "
     "before treatment. We do not accept Medicaid."),
    ("cancellation", "cancel cancellation reschedule move policy late fee",
     "You can cancel or reschedule free of charge up to 24 hours before "
     "your appointment. Cancellations inside 24 hours carry a $40 fee."),
    ("new patient", "new patient first visit paperwork bring forms register",
     "Welcome! A first visit includes an exam, digital x-rays and a "
     "cleaning, and takes about an hour. Please arrive 15 minutes early "
     "with your insurance card and a photo ID."),
    ("emergency", "emergency urgent toothache pain swelling bleeding night",
     "We hold same-day emergency slots for tooth pain — call us or book "
     "an Emergency Tooth Pain Visit here. If you have severe swelling, "
     "uncontrolled bleeding or trouble breathing, call (904) 555-0147 "
     "right away or go to your nearest emergency room."),
    ("payment", "pay payment card credit hsa fsa carecredit financing cash",
     "We accept all major credit and debit cards, HSA and FSA cards, "
     "and CareCredit financing. Payment is due at the time of treatment."),
    ("languages", "spanish language speak espanol interpreter bilingual",
     "Our team speaks English and Spanish."),
    ("children", "kids children child pediatric family young",
     "We welcome children aged 5 and up for cleanings and check-ups."),
    ("reminders", "reminder remind text message confirm confirmation",
     "We send a text reminder 24 hours before your appointment and a "
     "confirmation text as soon as you book."),
    ("xrays", "xray x-ray xrays radiation safe digital",
     "We use digital x-rays, which use a fraction of the radiation of "
     "film x-rays. X-rays are included in the new patient exam."),
]

# terms that must never be answered as if offered / advised on
NEVER_OFFER = [
    "implant", "implants", "braces", "orthodontics", "orthodontist",
    "dentures", "veneer", "veneers", "oral surgery", "wisdom tooth",
    "wisdom teeth", "root canal",
]
MEDICAL_ADVICE = [
    "diagnose", "diagnosis", "why does my", "what is wrong with",
    "should i take", "antibiotics", "prescribe", "prescription for",
]


def load_sample_tenant(conn: sqlite3.Connection) -> int:
    """Insert the sample tenant; returns tenant id. Idempotent by slug."""
    existing = conn.execute(
        "SELECT id FROM tenants WHERE slug = ?",
        (TENANT["slug"],)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO tenants (slug, name, timezone, phone, address)"
        " VALUES (?, ?, ?, ?, ?)",
        (TENANT["slug"], TENANT["name"], TENANT["timezone"],
         TENANT["phone"], TENANT["address"]))
    tid = cur.lastrowid
    for name, role in STAFF:
        conn.execute(
            "INSERT INTO staff (tenant_id, name, role) VALUES (?, ?, ?)",
            (tid, name, role))
    for name, dur, buf, price, role, kw in SERVICES:
        conn.execute(
            "INSERT INTO services (tenant_id, name, duration_min,"
            " buffer_min, price_text, staff_role, keywords)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tid, name, dur, buf, price, role, kw))
    for weekday, (o, c, bs, be) in HOURS.items():
        conn.execute(
            "INSERT INTO business_hours (tenant_id, weekday, open, close,"
            " break_start, break_end) VALUES (?, ?, ?, ?, ?, ?)",
            (tid, weekday, o, c, bs, be))
    for topic, triggers, answer in KB:
        conn.execute(
            "INSERT INTO kb_entries (tenant_id, topic, triggers, answer)"
            " VALUES (?, ?, ?, ?)", (tid, topic, triggers, answer))
    for term in NEVER_OFFER:
        conn.execute(
            "INSERT INTO scope_terms (tenant_id, term, kind)"
            " VALUES (?, ?, 'never_offer')", (tid, term))
    for term in MEDICAL_ADVICE:
        conn.execute(
            "INSERT INTO scope_terms (tenant_id, term, kind)"
            " VALUES (?, ?, 'medical_advice')", (tid, term))
    conn.commit()
    return tid
