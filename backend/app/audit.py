"""Audit + lead recording.

Every interaction a caller has with the front desk lands in the
interactions table: what was said, the intent we detected, and the
outcome (answered / refused / booked / conflict / lead / escalated).
The owner dashboard and the acceptance tests read this trail — it is
the product's evidence surface, so nothing writes appointments or
leads without a matching audit row.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_interaction(conn: sqlite3.Connection, tenant_id: int,
                    session_id: str, channel: str, speaker: str,
                    text: str, intent: str = "", outcome: str = "",
                    detail: str = "") -> None:
    conn.execute(
        "INSERT INTO interactions (tenant_id, session_id, channel,"
        " speaker, text, intent, outcome, detail, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tenant_id, session_id, channel, speaker, text, intent, outcome,
         detail, now_iso()))
    conn.commit()


def log_gap(conn: sqlite3.Connection, tenant_id: int, question: str,
            reason: str, session_id: str = "") -> None:
    conn.execute(
        "INSERT INTO kb_gaps (tenant_id, question, reason, session_id,"
        " created_at) VALUES (?, ?, ?, ?, ?)",
        (tenant_id, question, reason, session_id, now_iso()))
    conn.commit()


def create_lead(conn: sqlite3.Connection, tenant_id: int, name: str,
                phone: str, intent: str, summary: str) -> int:
    cur = conn.execute(
        "INSERT INTO leads (tenant_id, name, phone, intent, summary,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (tenant_id, name, phone, intent, summary, now_iso()))
    conn.commit()
    return cur.lastrowid
