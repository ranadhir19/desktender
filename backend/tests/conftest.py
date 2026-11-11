"""Acceptance-test fixtures: a fresh store per test, loaded with the
real sample tenant configuration (Brightpath Dental Studio)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, sample_data  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    c = db.init_db(tmp_path / "test.db")
    sample_data.load_sample_tenant(c)
    yield c
    c.close()


@pytest.fixture()
def tenant(conn):
    return conn.execute("SELECT * FROM tenants WHERE slug = ?",
                        ("brightpath-dental",)).fetchone()


@pytest.fixture()
def services(conn, tenant):
    return {r["name"]: r for r in conn.execute(
        "SELECT * FROM services WHERE tenant_id = ?",
        (tenant["id"],)).fetchall()}
