"""GraphQL seam for the owner dashboard: bookings, leads, KB gaps and
the audit trail. Dashboard queries are exactly where GraphQL earns
its place here — one query, nested fields, no N+1 endpoint round
trips. Read-only by design: the front desk's writes stay on REST.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import strawberry
from strawberry.types import Info


@strawberry.type
class Booking:
    id: int
    service: str
    staff_name: str
    customer_name: str
    customer_phone: str
    start: str
    end: str
    status: str
    confirmation_code: str


@strawberry.type
class Lead:
    id: int
    name: str
    phone: str
    intent: str
    summary: str
    created_at: str


@strawberry.type
class KbGap:
    id: int
    question: str
    reason: str
    status: str
    created_at: str


@strawberry.type
class AuditRow:
    id: int
    session_id: str
    speaker: str
    text: str
    intent: str
    outcome: str
    created_at: str


@strawberry.type
class DashboardStats:
    today_bookings: int
    leads_open: int
    gaps_open: int
    interactions_today: int


@strawberry.type
class Dashboard:
    tenant_name: str
    today_bookings: list[Booking]
    leads: list[Lead]
    kb_gaps: list[KbGap]
    stats: DashboardStats


def _conn(info) -> sqlite3.Connection:
    return info.context["conn"]


@strawberry.type
class Query:
    @strawberry.field
    def dashboard(self, info: Info, tenant_slug: str) -> Dashboard:
        conn = _conn(info)
        tenant = conn.execute(
            "SELECT * FROM tenants WHERE slug = ?",
            (tenant_slug,)).fetchone()
        if tenant is None:
            raise ValueError(f"unknown tenant {tenant_slug}")
        today = date.today().isoformat()
        rows = conn.execute(
            "SELECT a.*, s.name AS service_name, st.name AS staff_name"
            " FROM appointments a JOIN services s ON s.id = a.service_id"
            " JOIN staff st ON st.id = a.staff_id"
            " WHERE a.tenant_id = ? AND substr(a.start_ts, 1, 10) = ?"
            " AND a.status = 'booked' ORDER BY a.start_ts",
            (tenant["id"], today)).fetchall()
        bookings = [Booking(id=r["id"], service=r["service_name"],
                            staff_name=r["staff_name"],
                            customer_name=r["customer_name"],
                            customer_phone=r["customer_phone"],
                            start=r["start_ts"], end=r["end_ts"],
                            status=r["status"],
                            confirmation_code=r["confirmation_code"])
                    for r in rows]
        leads = [Lead(id=r["id"], name=r["name"], phone=r["phone"],
                      intent=r["intent"], summary=r["summary"],
                      created_at=r["created_at"]) for r in conn.execute(
            "SELECT * FROM leads WHERE tenant_id = ? ORDER BY id DESC"
            " LIMIT 25", (tenant["id"],)).fetchall()]
        gaps = [KbGap(id=r["id"], question=r["question"],
                      reason=r["reason"], status=r["status"],
                      created_at=r["created_at"]) for r in conn.execute(
            "SELECT * FROM kb_gaps WHERE tenant_id = ? AND status='open'"
            " ORDER BY id DESC LIMIT 25", (tenant["id"],)).fetchall()]
        stats = DashboardStats(
            today_bookings=len(bookings), leads_open=len(leads),
            gaps_open=len(gaps),
            interactions_today=conn.execute(
                "SELECT COUNT(*) c FROM interactions WHERE tenant_id = ?"
                " AND substr(created_at, 1, 10) = ?",
                (tenant["id"], today)).fetchone()["c"])
        return Dashboard(tenant_name=tenant["name"],
                         today_bookings=bookings, leads=leads,
                         kb_gaps=gaps, stats=stats)

    @strawberry.field
    def audit_trail(self, info: Info, tenant_slug: str, limit: int = 50
                    ) -> list[AuditRow]:
        conn = _conn(info)
        tenant = conn.execute(
            "SELECT * FROM tenants WHERE slug = ?",
            (tenant_slug,)).fetchone()
        if tenant is None:
            raise ValueError(f"unknown tenant {tenant_slug}")
        rows = conn.execute(
            "SELECT * FROM interactions WHERE tenant_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (tenant["id"], min(limit, 200))).fetchall()
        return [AuditRow(id=r["id"], session_id=r["session_id"],
                         speaker=r["speaker"], text=r["text"],
                         intent=r["intent"], outcome=r["outcome"],
                         created_at=r["created_at"]) for r in rows]


schema = strawberry.Schema(query=Query)
