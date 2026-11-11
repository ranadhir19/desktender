"""SQLite store for ReceptDesk.

Multi-tenant from day one: every business table carries tenant_id and
every query is tenant-scoped. SQLite is the v1 store so the whole
product runs locally with zero accounts; the schema is deliberately
plain SQL so a hosted Postgres swap is mechanical.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'America/New_York',
    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL,
    role TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL,
    duration_min INTEGER NOT NULL,
    buffer_min INTEGER NOT NULL DEFAULT 0,
    price_text TEXT NOT NULL DEFAULT '',
    staff_role TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS business_hours (
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    weekday INTEGER NOT NULL,           -- 0 = Monday .. 6 = Sunday
    open TEXT,                          -- 'HH:MM' or NULL when closed
    close TEXT,
    break_start TEXT,
    break_end TEXT,
    PRIMARY KEY (tenant_id, weekday)
);
CREATE TABLE IF NOT EXISTS kb_entries (
    id INTEGER PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    topic TEXT NOT NULL,
    triggers TEXT NOT NULL DEFAULT '',  -- space-separated trigger tokens
    answer TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scope_terms (
    id INTEGER PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    term TEXT NOT NULL,
    kind TEXT NOT NULL                  -- 'never_offer' | 'medical_advice'
);
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    service_id INTEGER NOT NULL REFERENCES services(id),
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    customer_name TEXT NOT NULL,
    customer_phone TEXT NOT NULL,
    start_ts TEXT NOT NULL,             -- 'YYYY-MM-DD HH:MM' local
    end_ts TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'booked',
    confirmation_code TEXT UNIQUE NOT NULL,
    channel TEXT NOT NULL DEFAULT 'web',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    session_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    speaker TEXT NOT NULL,              -- 'caller' | 'assistant' | 'system'
    text TEXT NOT NULL,
    intent TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',   -- answered|refused|booked|conflict|lead|escalated|info
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_gaps (
    id INTEGER PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    question TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',    -- out_of_scope|never_offer|medical_advice|no_match
    session_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def get_tenant(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM tenants WHERE slug = ?", (slug,)).fetchone()
