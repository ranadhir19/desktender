"""API-level acceptance tests: REST booking flow, GraphQL dashboard
seam, and the telephony stub's honesty (shaped like a carrier webhook,
labelled as a stub, connected to nothing).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

SLUG = "brightpath-dental"


@pytest.fixture()
def client(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as c:
        yield c


def test_profile_and_availability(client):
    prof = client.get(f"/tenants/{SLUG}").json()
    assert prof["name"] == "Brightpath Dental Studio"
    cleaning = next(s for s in prof["services"]
                    if s["name"] == "Routine Cleaning")
    av = client.get(f"/tenants/{SLUG}/availability",
                    params={"service_id": cleaning["id"],
                            "day": "2026-11-16"}).json()
    assert av["slots"] and av["slots"][0]["start"] == "2026-11-16 08:00"
    sun = client.get(f"/tenants/{SLUG}/availability",
                     params={"service_id": cleaning["id"],
                             "day": "2026-11-15"}).json()
    assert sun["slots"] == []


def test_rest_booking_conflict_and_cancel(client):
    prof = client.get(f"/tenants/{SLUG}").json()
    cleaning = next(s for s in prof["services"]
                    if s["name"] == "Routine Cleaning")
    body = {"service_id": cleaning["id"], "start": "2026-11-16 09:00",
            "customer_name": "Api Person", "customer_phone": "9045550140",
            "staff_id": 3}
    r1 = client.post(f"/tenants/{SLUG}/appointments", json=body)
    assert r1.status_code == 200 and r1.json()["ok"]
    r2 = client.post(f"/tenants/{SLUG}/appointments", json=body)
    assert r2.status_code == 409  # same staff, same time
    code = r1.json()["confirmation_code"]
    rc = client.post(f"/tenants/{SLUG}/appointments/{code}/cancel")
    assert rc.status_code == 200 and rc.json()["ok"]
    r3 = client.post(f"/tenants/{SLUG}/appointments", json=body)
    assert r3.status_code == 200  # slot freed by the cancellation


def test_frontdoor_endpoint_books_and_audits(client):
    sid = "web-1"
    def say(text):
        return client.post(f"/tenants/{SLUG}/frontdoor/message",
                           json={"session_id": sid, "text": text}).json()
    # use explicit ISO date so the test is calendar-independent
    r = say("I would like to book a routine cleaning on 2026-11-16")
    assert "1." in r["reply"]
    r = say("1")
    r = say("Jordan Ellis")
    r = say("904-555-0151")
    r = say("yes")
    assert r["outcome"] == "booked"
    audit_rows = client.get(f"/tenants/{SLUG}/audit").json()
    outcomes = [i["outcome"] for i in audit_rows["interactions"]
                if i["outcome"]]
    assert "booked" in outcomes


def test_graphql_dashboard(client):
    q = {"query": "{ dashboard(tenantSlug: \"%s\") { tenantName"
                  " stats { todayBookings leadsOpen gapsOpen }"
                  " todayBookings { service confirmationCode } } }"
           % SLUG}
    r = client.post("/graphql", json=q)
    assert r.status_code == 200
    data = r.json()["data"]["dashboard"]
    assert data["tenantName"] == "Brightpath Dental Studio"
    assert data["stats"]["todayBookings"] == len(data["todayBookings"])


def test_telephony_stub_is_honest_and_routes_sms(client):
    status = client.get("/telephony/status").json()
    assert status["connected"] is False
    assert status["status"] == "stub-not-connected"
    voice = client.post("/telephony/voice",
                        data={"From": "+19045550160", "CallSid": "CAx"})
    assert voice.status_code == 200
    assert "stub" in voice.text and "<Response>" in voice.text
    sms = client.post(f"/tenants/{SLUG}/telephony/sms",
                      data={"From": "+19045550160",
                            "Body": "What are your opening hours?"})
    assert sms.status_code == 200
    assert "Monday to Friday" in sms.text  # answered from the real KB
    refused = client.post(f"/tenants/{SLUG}/telephony/sms",
                          data={"From": "+19045550160",
                                "Body": "How much are implants?"})
    assert "not a service we offer" in refused.text
