"""Seed the runtime database with the sample tenant.

Run: python -m app.seed [db_path]
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import db, sample_data
from .main import DEFAULT_DB


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    conn = db.init_db(path)
    tid = sample_data.load_sample_tenant(conn)
    name = conn.execute("SELECT name FROM tenants WHERE id = ?",
                        (tid,)).fetchone()["name"]
    _demo_activity(conn, tid)
    print(f"seeded tenant {name} (id {tid}) into {path}")
    return 0


def _demo_activity(conn, tenant_id: int) -> None:
    """Two bookings for today plus a callback lead, so the owner
    dashboard shows its real shape on first run. Demo data only —
    the acceptance tests build their own stores and never see this.
    """
    from datetime import date, timedelta

    from . import audit, engine

    if conn.execute("SELECT COUNT(*) c FROM appointments"
                    " WHERE tenant_id = ?",
                    (tenant_id,)).fetchone()["c"]:
        return
    today = date.today()
    day = today + timedelta(days=(7 - today.weekday()) % 7 or 7) \
        if today.weekday() == 6 else today
    svc = conn.execute(
        "SELECT * FROM services WHERE tenant_id = ? AND name ="
        " 'Routine Cleaning'", (tenant_id,)).fetchone()
    slots = engine.slots_for_date(conn, tenant_id, svc, day)
    first = slots[0]
    second = next(s for s in slots
                  if s["start"][11:16] >= "11:00"
                  and s["start"] != first["start"])
    for slot, who, phone in (
            (first, "Demo Patient One", "9045550201"),
            (second, "Demo Patient Two", "9045550202")):
        engine.book(conn, tenant_id, svc["id"], slot["start"], who,
                    phone, staff_id=slot["staff_id"], channel="seed")
    audit.create_lead(conn, tenant_id, "Demo Caller", "9045550203",
                      "callback_request",
                      "Seeded demo lead: asked for a callback about "
                      "Saturday hours.")


if __name__ == "__main__":
    raise SystemExit(main())
