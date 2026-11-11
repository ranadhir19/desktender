"""ReceptDesk API: REST front door + owner endpoints, GraphQL
dashboard seam, and telephony webhook stubs.

Telephony status, stated plainly: the /telephony/* endpoints are
STUBS. They accept carrier-shaped webhooks (Twilio-style form fields)
and answer with TwiML-shaped XML so the integration shape can be
exercised end to end, and the SMS stub routes its text through the
same front-door engine a real call would use. No carrier account is
connected, no real call or text can occur, and nothing in this file
pretends otherwise. A production deployment needs a telephony
account (a paid, owner-approved step) wired to these endpoints.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from strawberry.fastapi.router import GraphQLRouter

from . import audit, db, engine, frontdoor, sample_data
from .graphql import schema

__version__ = "0.1.0"

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "receptdesk.db"


class FrontdoorMessage(BaseModel):
    session_id: str
    text: str
    channel: str = "chat"


class BookingRequest(BaseModel):
    service_id: int
    start: str  # 'YYYY-MM-DD HH:MM'
    customer_name: str
    customer_phone: str
    staff_id: int | None = None


def _tenant_or_404(conn, slug: str):
    tenant = db.get_tenant(conn, slug)
    if tenant is None:
        raise HTTPException(404, f"unknown tenant {slug}")
    return tenant


def create_app(db_path: str | Path | None = None) -> FastAPI:
    db_path = Path(db_path or os.environ.get("RECEPTDESK_DB",
                                             DEFAULT_DB))
    conn = db.init_db(db_path)
    if conn.execute("SELECT COUNT(*) c FROM tenants").fetchone()["c"] == 0:
        sample_data.load_sample_tenant(conn)
    conn.close()

    app = FastAPI(title="ReceptDesk", version=__version__)
    app.state.db_path = db_path

    def connect():
        return db.connect(db_path)

    async def gql_context(request: Request):
        return {"request": request, "conn": connect()}

    app.include_router(
        GraphQLRouter(schema, context_getter=gql_context), prefix="/graphql")

    @app.get("/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.get("/tenants/{slug}")
    def tenant_profile(slug: str):
        conn = connect()
        tenant = _tenant_or_404(conn, slug)
        services = [dict(r) for r in conn.execute(
            "SELECT id, name, duration_min, price_text FROM services"
            " WHERE tenant_id = ? ORDER BY id", (tenant["id"],))]
        hours = [dict(r) for r in conn.execute(
            "SELECT weekday, open, close FROM business_hours"
            " WHERE tenant_id = ? ORDER BY weekday", (tenant["id"],))]
        conn.close()
        return {"slug": tenant["slug"], "name": tenant["name"],
                "phone": tenant["phone"], "address": tenant["address"],
                "services": services, "hours": hours}

    @app.get("/tenants/{slug}/availability")
    def availability(slug: str, service_id: int, day: str):
        conn = connect()
        tenant = _tenant_or_404(conn, slug)
        service = engine.get_service(conn, tenant["id"], service_id)
        if service is None:
            raise HTTPException(404, "unknown service")
        slots = engine.slots_for_date(
            conn, tenant["id"], service,
            date.fromisoformat(day))
        conn.close()
        return {"service": service["name"], "date": day, "slots": slots}

    @app.get("/tenants/{slug}/appointments")
    def list_appointments(slug: str, day: str):
        conn = connect()
        tenant = _tenant_or_404(conn, slug)
        rows = [dict(r) for r in conn.execute(
            "SELECT a.*, s.name AS service_name, st.name AS staff_name"
            " FROM appointments a JOIN services s ON s.id = a.service_id"
            " JOIN staff st ON st.id = a.staff_id"
            " WHERE a.tenant_id = ? AND substr(a.start_ts, 1, 10) = ?"
            " ORDER BY a.start_ts", (tenant["id"], day))]
        conn.close()
        return {"date": day, "appointments": rows}

    @app.post("/tenants/{slug}/appointments")
    def book_appointment(slug: str, body: BookingRequest):
        conn = connect()
        tenant = _tenant_or_404(conn, slug)
        result = engine.book(conn, tenant["id"], body.service_id,
                             body.start, body.customer_name,
                             body.customer_phone, staff_id=body.staff_id)
        conn.close()
        if not result["ok"]:
            raise HTTPException(409, result)
        return result

    @app.post("/tenants/{slug}/appointments/{code}/cancel")
    def cancel_appointment(slug: str, code: str):
        conn = connect()
        tenant = _tenant_or_404(conn, slug)
        result = engine.cancel(conn, tenant["id"], code)
        conn.close()
        if not result["ok"]:
            raise HTTPException(404, result)
        return result

    @app.post("/tenants/{slug}/frontdoor/message")
    def frontdoor_message(slug: str, body: FrontdoorMessage):
        conn = connect()
        tenant = _tenant_or_404(conn, slug)
        result = frontdoor.handle_message(
            conn, tenant, body.session_id, body.text,
            channel=body.channel)
        conn.close()
        return result

    @app.get("/tenants/{slug}/audit")
    def audit_trail(slug: str, limit: int = 50):
        conn = connect()
        tenant = _tenant_or_404(conn, slug)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM interactions WHERE tenant_id = ?"
            " ORDER BY id DESC LIMIT ?", (tenant["id"], limit))]
        conn.close()
        return {"interactions": rows}

    @app.get("/tenants/{slug}/kb-gaps")
    def kb_gaps(slug: str):
        conn = connect()
        tenant = _tenant_or_404(conn, slug)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM kb_gaps WHERE tenant_id = ? AND status='open'"
            " ORDER BY id DESC", (tenant["id"],))]
        conn.close()
        return {"kb_gaps": rows}

    # --- telephony stubs (NOT connected to any carrier) -------------
    @app.get("/telephony/status")
    def telephony_status():
        return {"connected": False,
                "status": "stub-not-connected",
                "note": "Webhook shapes only. No carrier account is "
                        "wired up; no real call or SMS can occur."}

    @app.post("/telephony/voice")
    def telephony_voice(From: str = Form(default=""),
                        CallSid: str = Form(default="")):
        xml = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
               "<Response><!-- ReceptDesk stub: not a live call -->"
               "<Say>This is a demonstration line for ReceptDesk. "
               "No real call is connected.</Say></Response>")
        return PlainTextResponse(xml, media_type="text/xml")

    @app.post("/tenants/{slug}/telephony/sms")
    def telephony_sms(slug: str, From: str = Form(default=""),
                      Body: str = Form(default="")):
        conn = connect()
        tenant = _tenant_or_404(conn, slug)
        result = frontdoor.handle_message(
            conn, tenant, f"sms:{From or 'unknown'}", Body,
            channel="sms-stub")
        conn.close()
        reply = result["reply"].replace("&", "and")
        xml = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
               "<Response><!-- ReceptDesk stub: no SMS is sent -->"
               f"<Message>{reply}</Message></Response>")
        return PlainTextResponse(xml, media_type="text/xml")

    # serve the built demo UI when present
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/app", StaticFiles(directory=dist, html=True),
                  name="app")

    return app


app = create_app()
