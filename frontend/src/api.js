// Thin API client for the ReceptDesk demo UI.
export const SLUG = "brightpath-dental";

async function json(res) {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export function getProfile() {
  return fetch(`/tenants/${SLUG}`).then(json);
}

export function getAppointments(day) {
  return fetch(`/tenants/${SLUG}/appointments?day=${day}`).then(json);
}

export function getAudit(limit = 25) {
  return fetch(`/tenants/${SLUG}/audit?limit=${limit}`).then(json);
}

export function sendMessage(sessionId, text) {
  return fetch(`/tenants/${SLUG}/frontdoor/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, text }),
  }).then(json);
}

const DASHBOARD_QUERY = `{
  dashboard(tenantSlug: "${SLUG}") {
    tenantName
    stats { todayBookings leadsOpen gapsOpen interactionsToday }
    leads { id name phone intent summary createdAt }
    kbGaps { id question reason status createdAt }
  }
}`;

export function getDashboard() {
  return fetch("/graphql", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: DASHBOARD_QUERY }),
  })
    .then(json)
    .then((r) => r.data.dashboard);
}
