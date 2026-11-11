# DeskTender — AI front desk for small businesses

A B2B front-desk product: it answers a business's calls and chats
from **that business's own knowledge base**, books **real
appointments** against a real calendar store, captures structured
leads, and keeps an **auditable record of every interaction** for the
owner. Clinics, salons, trades and professional offices are the
target buyers.

Published name: DeskTender (developed under the codename ReceptDesk,
which survives in local paths and code identifiers). v0.1 — not
deployed anywhere.

## What is real in v1

- **Booking engine** — per-business hours, breaks, services, staff
  and per-service buffers; availability is computed from the store,
  conflicts (including buffer collisions) are detected inside a
  transaction, and a booking either lands as a row with a
  confirmation code or it does not happen. No simulated success.
- **Grounded answers only** — questions are answered verbatim from
  the tenant's KB entries, with the entry cited in the audit trail.
  Scope guards run *before* matching, so near-miss questions (a
  service the business does not offer, diagnosis-seeking) refuse
  instead of silently substituting a nearby answer. Every refusal
  gives the caller the business's phone number and is logged as a
  **KB-gap report** for the owner.
- **Leads + audit** — callback requests become structured leads;
  every caller/assistant/system turn is an audit row with intent and
  outcome (answered / refused / booked / conflict / lead /
  escalated), visible on the owner dashboard.
- **Multi-tenant shape** — every table is tenant-scoped; one tenant
  (Brightpath Dental Studio, a fictional dental clinic) is fully
  configured with services, staff, hours and a 12-entry KB; tenant
  isolation is covered by tests.
- **Simulated front door** — a chat "call" that exercises the same
  engine and Q&A a phone call would: book (guided flow), ask, cancel
  by confirmation code, request a human.
- **Owner dashboard (React)** — today's bookings, leads, the
  refusal/KB-gap report, and the audit trail; dashboard aggregates
  are served by GraphQL, writes stay on REST.

## What is stubbed — stated plainly

- **Telephony is a stub.** `/telephony/voice` and
  `/tenants/{slug}/telephony/sms` accept carrier-shaped webhooks
  (Twilio-style form fields) and return TwiML-shaped XML, and the SMS
  stub routes its text through the real front-door engine. **No
  carrier account is connected; no real call or text can occur.**
  `GET /telephony/status` says exactly this. Production needs a
  telephony account (paid; owner's step) wired to these endpoints.
- No speech (no STT/TTS): the "call" is text. A voice production
  version adds carrier media streams + STT/TTS behind the same
  front-door interface.
- Auth, billing and hosted deployment are not in v1: subscription
  packaging is a proposal in the design record, not enacted code.

## Run it

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.seed            # demo data (fictional clinic)
.venv/bin/uvicorn app.main:app --port 8000
# UI:  http://localhost:8000/app   (built frontend, if present)
# API: http://localhost:8000/health , GraphQL at /graphql

cd ../frontend
npm install && npm run dev              # dev UI on :5174, proxies API
```

## Validate

```bash
python3 validate.py
```

Stages: backend import/compile, the domain-authored acceptance
suite (booking rules, conflicts and buffers, KB-grounded answers,
near-miss refusals, scripted-call E2E, audit integrity, tenant
isolation, API + GraphQL + telephony-stub honesty), an end-to-end
smoke on a throwaway store, the frontend build, the reference-
integrity scan, and the banned-name check.

## Layout

- `backend/app/engine.py` — availability + booking + conflicts
- `backend/app/qa.py` — KB-grounded Q&A with scope guards
- `backend/app/frontdoor.py` — caller conversation state machine
- `backend/app/main.py` — REST API + telephony stubs
- `backend/app/graphql.py` — owner-dashboard GraphQL seam
- `backend/app/sample_data.py` — the sample tenant's own data
- `frontend/` — React demo UI (owner dashboard + simulated call)
