import { useCallback, useEffect, useRef, useState } from "react";
import {
  getAppointments,
  getAudit,
  getDashboard,
  getProfile,
  sendMessage,
} from "./api";

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function Dashboard() {
  const [day, setDay] = useState(todayISO());
  const [appts, setAppts] = useState([]);
  const [dash, setDash] = useState(null);
  const [audit, setAudit] = useState([]);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    setErr("");
    getAppointments(day).then((r) => setAppts(r.appointments)).catch((e) => setErr(String(e)));
    getDashboard().then(setDash).catch((e) => setErr(String(e)));
    getAudit(25).then((r) => setAudit(r.interactions)).catch(() => {});
  }, [day]);

  useEffect(load, [load]);

  return (
    <div>
      {err && <p className="err">API error: {err} — is the backend running on :8000?</p>}
      {dash && (
        <div className="cards">
          <div className="card"><b>{dash.stats.todayBookings}</b><span>bookings today</span></div>
          <div className="card"><b>{dash.stats.leadsOpen}</b><span>leads captured</span></div>
          <div className="card"><b>{dash.stats.gapsOpen}</b><span>open KB gaps</span></div>
          <div className="card"><b>{dash.stats.interactionsToday}</b><span>interactions today</span></div>
        </div>
      )}
      <section>
        <h2>
          Appointments{" "}
          <input type="date" value={day} onChange={(e) => setDay(e.target.value)} />{" "}
          <button onClick={load}>Refresh</button>
        </h2>
        {appts.length === 0 ? (
          <p className="dim">No appointments on this date yet.</p>
        ) : (
          <table>
            <thead>
              <tr><th>Time</th><th>Service</th><th>With</th><th>Patient</th><th>Phone</th><th>Status</th><th>Code</th></tr>
            </thead>
            <tbody>
              {appts.map((a) => (
                <tr key={a.id}>
                  <td>{a.start_ts.slice(11)}–{a.end_ts.slice(11)}</td>
                  <td>{a.service_name}</td>
                  <td>{a.staff_name}</td>
                  <td>{a.customer_name}</td>
                  <td>{a.customer_phone}</td>
                  <td>{a.status}</td>
                  <td>{a.confirmation_code}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <div className="cols">
        <section>
          <h2>Leads</h2>
          {dash && dash.leads.length === 0 && <p className="dim">No leads yet.</p>}
          {dash && dash.leads.map((l) => (
            <div key={l.id} className="item">
              <b>{l.name || "Caller"}</b> · {l.phone || "no phone"} · {l.intent}
              <div className="dim">{l.summary}</div>
            </div>
          ))}
        </section>
        <section>
          <h2>Refusals — KB gap report</h2>
          <p className="dim">
            Questions the front desk would not improvise an answer to. Each one is a
            knowledge-base entry the owner can add.
          </p>
          {dash && dash.kbGaps.length === 0 && <p className="dim">No open gaps.</p>}
          {dash && dash.kbGaps.map((g) => (
            <div key={g.id} className="item">
              “{g.question}” <span className="tag">{g.reason}</span>
            </div>
          ))}
        </section>
      </div>
      <section>
        <h2>Audit trail (latest first)</h2>
        <div className="audit">
          {audit.map((i) => (
            <div key={i.id} className="arow">
              <span className="tag">{i.speaker}</span> {i.text}
              {i.outcome && <span className="tag out">{i.outcome}</span>}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function CallSim() {
  const [turns, setTurns] = useState([
    { who: "assistant", text: "This is a simulated call. Try: “What are your opening hours?”, “I'd like to book a cleaning for tomorrow”, “How much are implants?” (watch it refuse), or “Please call me back”.", outcome: "" },
  ]);
  const [draft, setDraft] = useState("");
  const sessionRef = useRef(`ui-${Date.now()}`);
  const boxRef = useRef(null);

  useEffect(() => {
    boxRef.current?.scrollTo(0, boxRef.current.scrollHeight);
  }, [turns]);

  async function send() {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    setTurns((t) => [...t, { who: "caller", text, outcome: "" }]);
    try {
      const r = await sendMessage(sessionRef.current, text);
      setTurns((t) => [...t, { who: "assistant", text: r.reply, outcome: r.outcome }]);
    } catch (e) {
      setTurns((t) => [...t, { who: "assistant", text: `Error: ${e}`, outcome: "error" }]);
    }
  }

  return (
    <div>
      <div className="chat" ref={boxRef}>
        {turns.map((t, i) => (
          <div key={i} className={`msg ${t.who}`}>
            <div>{t.text}</div>
            {t.outcome && <span className="tag out">{t.outcome}</span>}
          </div>
        ))}
      </div>
      <div className="composer">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Type as the caller…"
        />
        <button onClick={send}>Send</button>
      </div>
      <p className="dim">
        Simulated front door — it exercises the real booking engine and the real
        knowledge base. No phone call is placed; telephony webhooks are stubs
        (see /telephony/status).
      </p>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState("dashboard");
  const [profile, setProfile] = useState(null);
  useEffect(() => {
    getProfile().then(setProfile).catch(() => {});
  }, []);
  return (
    <div className="wrap">
      <header>
        <h1>ReceptDesk</h1>
        <span className="dim">
          AI front desk{profile ? ` — ${profile.name} · ${profile.phone}` : ""}
        </span>
      </header>
      <nav>
        <button className={tab === "dashboard" ? "on" : ""} onClick={() => setTab("dashboard")}>
          Owner dashboard
        </button>
        <button className={tab === "call" ? "on" : ""} onClick={() => setTab("call")}>
          Simulated call
        </button>
      </nav>
      {tab === "dashboard" ? <Dashboard /> : <CallSim />}
    </div>
  );
}
