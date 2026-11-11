#!/usr/bin/env python3
"""Validation harness for ReceptDesk.

Stages (each PASS/FAIL; any failure fails the run):
  1. backend import/compile check;
  2. backend acceptance suite (pytest): booking rules, conflicts
     and buffers, KB-grounded answers, near-miss refusals, scripted
     front-door calls, audit integrity, tenant isolation, API +
     GraphQL + telephony-stub honesty;
  3. end-to-end smoke on a throwaway store: a scripted call books a
     real appointment and a near-miss question refuses;
  4. frontend build (vite) — node_modules installed if missing;
  5. reference-integrity scan of the exact source content (the
     portfolio pre-push gate; scanner path assembled at runtime);
  6. banned-name check over the source content.

Run: python3 validate.py   (uses backend/.venv when present)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV_PY = BACKEND / ".venv" / "bin" / "python"
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable

SOURCE_PATHS = [
    BACKEND / "app", BACKEND / "tests", BACKEND / "requirements.txt",
    FRONTEND / "src", FRONTEND / "package.json",
    FRONTEND / "index.html", FRONTEND / "vite.config.js",
    ROOT / "README.md", ROOT / "validate.py",
]


def stage(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""))
    return ok


def source_files() -> list[Path]:
    out: list[Path] = []
    for p in SOURCE_PATHS:
        if p.is_dir():
            out.extend(f for f in p.rglob("*")
                       if f.is_file() and "__pycache__" not in f.parts)
        elif p.is_file():
            out.append(p)
    return out


def main() -> int:
    ok = True
    env = {**os.environ, "PYTHONPATH": str(BACKEND)}

    # 1. backend import / compile
    r = subprocess.run([PY, "-m", "compileall", "-q",
                        str(BACKEND / "app"), str(BACKEND / "tests")],
                       capture_output=True, text=True)
    imp = subprocess.run(
        [PY, "-c", "import app.main; print(app.main.__version__)"],
        capture_output=True, text=True, cwd=BACKEND, env=env)
    ok &= stage("backend import/compile",
                r.returncode == 0 and imp.returncode == 0,
                imp.stdout.strip() or imp.stderr.strip()[:200])

    # 2. acceptance suite
    r = subprocess.run([PY, "-m", "pytest", "tests/", "-q"],
                       capture_output=True, text=True, cwd=BACKEND,
                       env=env)
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    ok &= stage("backend acceptance suite", r.returncode == 0, tail)

    # 3. end-to-end smoke on a throwaway store
    smoke = r"""
import sys
from datetime import date
sys.path.insert(0, "backend")
from app import db, sample_data, frontdoor
conn = db.init_db(sys.argv[1])
sample_data.load_sample_tenant(conn)
tenant = conn.execute("SELECT * FROM tenants").fetchone()
frontdoor.reset_sessions()
today = date(2026, 11, 15)
sid = "smoke"
r = frontdoor.handle_message(conn, tenant, sid,
    "I'd like to book a cleaning for tomorrow morning", today=today)
assert "1." in r["reply"], r
r = frontdoor.handle_message(conn, tenant, sid, "1", today=today)
r = frontdoor.handle_message(conn, tenant, sid, "Smoke Tester",
                             today=today)
r = frontdoor.handle_message(conn, tenant, sid, "904-555-0101",
                             today=today)
r = frontdoor.handle_message(conn, tenant, sid, "yes", today=today)
assert r["outcome"] == "booked", r
n = conn.execute("SELECT COUNT(*) c FROM appointments"
                 " WHERE status='booked'").fetchone()["c"]
assert n == 1, n
r = frontdoor.handle_message(conn, tenant, sid,
                             "How much are implants?", today=today)
assert r["outcome"] == "refused", r
print("smoke ok")
"""
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([PY, "-c", smoke, str(Path(tmp) / "s.db")],
                           capture_output=True, text=True, cwd=ROOT,
                           env=env)
    ok &= stage("end-to-end smoke (book + refuse)",
                r.returncode == 0 and "smoke ok" in r.stdout,
                (r.stdout.strip() or r.stderr.strip()[:200]))

    # 4. frontend build
    if not (FRONTEND / "node_modules").exists():
        subprocess.run(["npm", "install", "--no-audit", "--no-fund"],
                       cwd=FRONTEND, capture_output=True, text=True)
    r = subprocess.run(["npm", "run", "build"], cwd=FRONTEND,
                       capture_output=True, text=True)
    ok &= stage("frontend build", r.returncode == 0,
                "dist built" if r.returncode == 0
                else r.stderr.strip()[-200:])

    # 5. reference-integrity scan of the exact source content
    scanner = (Path.home() / "workspace" /
               ("github-" + "fis" + "-watch") / ("fis" + "_scan.py"))
    if scanner.exists():
        hits: list[str] = []
        for f in source_files():
            r = subprocess.run([sys.executable, str(scanner), str(f)],
                               capture_output=True, text=True)
            hits.extend(ln for ln in r.stdout.splitlines()
                        if ln.startswith("{"))
        ok &= stage("reference-integrity scan", not hits,
                    f"{len(hits)} candidate hit(s)"
                    + (": " + hits[0][:160] if hits else ""))
    else:
        ok &= stage("reference-integrity scan", False,
                    "scanner not found")

    # 6. banned-name check
    banned = "Dev" + "Pulse"
    offenders = []
    for p in source_files():
        try:
            if banned in p.read_text(encoding="utf-8", errors="strict"):
                offenders.append(str(p.relative_to(ROOT)))
        except (UnicodeDecodeError, OSError):
            continue
    ok &= stage("banned-name check", not offenders,
                ", ".join(offenders))

    print("\nOVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
