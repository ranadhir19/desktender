"""Knowledge-base-grounded Q&A with refusal discipline.

The front desk may only answer from the business's own KB entries.
Answers are returned verbatim from the KB with the entry id cited.
Anything else is a refusal: scope guards (services the business does
not offer, medical advice) run BEFORE matching, so a near-miss
question that shares vocabulary with a KB entry still refuses rather
than silently substituting a nearby answer. Every refusal is logged
as a knowledge gap for the owner and hands the caller a human path.
"""

from __future__ import annotations

import re
import sqlite3

from . import audit

STOPWORDS = {
    "the", "a", "an", "is", "are", "do", "does", "did", "you", "your",
    "i", "me", "my", "we", "it", "to", "of", "for", "and", "or", "can",
    "could", "please", "what", "whats", "if", "on", "in", "at", "be",
    "have", "has", "with", "there", "this", "that",
}

THRESHOLD = 3  # one trigger hit (weight 3) is the minimum evidence


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ",
                  re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _normalize(text).split() if t not in STOPWORDS}


def _contains_term(norm_text: str, term: str) -> bool:
    return f" {term} " in f" {norm_text} "


def _scope_guard(conn, tenant_id: int, norm_text: str) -> str | None:
    rows = conn.execute(
        "SELECT term, kind FROM scope_terms WHERE tenant_id = ?",
        (tenant_id,)).fetchall()
    for kind in ("never_offer", "medical_advice"):
        for r in rows:
            if r["kind"] == kind and _contains_term(norm_text, r["term"]):
                return kind
    return None


def refusal_text(tenant: sqlite3.Row, reason: str) -> str:
    if reason == "never_offer":
        lead = "That is not a service we offer here, and I don't want to" \
               " steer you wrong."
    elif reason == "medical_advice":
        lead = "I can't help with diagnosis or treatment advice — that" \
               " needs the clinical team."
    else:
        lead = "I don't have that in our own records, and I don't want" \
               " to guess."
    return (f"{lead} You can reach {tenant['name']} directly at "
            f"{tenant['phone']}. I've also noted your question so the "
            f"team can follow up.")


def answer(conn, tenant: sqlite3.Row, question: str,
           session_id: str = "qa") -> dict:
    """Answer strictly from the KB, or refuse + log a gap."""
    tenant_id = tenant["id"]
    norm = _normalize(question)
    guard = _scope_guard(conn, tenant_id, norm)
    if guard:
        audit.log_gap(conn, tenant_id, question, guard, session_id)
        return {"outcome": "refused", "reason": guard,
                "answer": refusal_text(tenant, guard),
                "kb_entry_id": None}

    q_tokens = _tokens(question)
    entries = conn.execute(
        "SELECT * FROM kb_entries WHERE tenant_id = ? ORDER BY id",
        (tenant_id,)).fetchall()
    best, best_score = None, 0
    for entry in entries:
        triggers = set(entry["triggers"].split())
        topic_tokens = set(entry["topic"].replace("-", " ").split())
        score = 3 * len(q_tokens & triggers) + len(q_tokens & topic_tokens)
        if score > best_score:
            best, best_score = entry, score
    if best is None or best_score < THRESHOLD:
        audit.log_gap(conn, tenant_id, question, "no_match", session_id)
        return {"outcome": "refused", "reason": "no_match",
                "answer": refusal_text(tenant, "no_match"),
                "kb_entry_id": None}
    return {"outcome": "answered", "reason": "",
            "answer": best["answer"], "kb_entry_id": best["id"],
            "topic": best["topic"], "score": best_score}
