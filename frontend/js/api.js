// Every fetch in this app goes through here -- one place that knows the
// API's base URL and its {error: {code, message, details}} envelope, so
// every view can `catch` a single, useful Error instead of re-parsing a
// Response by hand.

const API_BASE_URL = window.API_BASE_URL || "http://localhost:8000";

class ApiError extends Error {
  constructor(code, message, status, details) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

async function apiFetch(path, options = {}) {
  let resp;
  try {
    resp = await fetch(`${API_BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (networkErr) {
    // fetch() itself throws on DNS/connection failure -- the API being
    // down entirely, not just returning an error status.
    throw new ApiError("network_error", "Could not reach the API. Is it running?", 0, null);
  }

  if (resp.status === 204) return null;

  let body = null;
  try {
    body = await resp.json();
  } catch {
    // A non-JSON body (e.g. a proxy's own error page) shouldn't crash the
    // caller -- fall through to the generic error below with body=null.
  }

  if (!resp.ok) {
    const envelope = body && body.error;
    throw new ApiError(
      envelope?.code || "unknown_error",
      envelope?.message || `Request failed with status ${resp.status}`,
      resp.status,
      envelope?.details ?? null,
    );
  }

  return body;
}

const api = {
  listServers: (limit = 50, offset = 0) => apiFetch(`/servers?limit=${limit}&offset=${offset}`),
  getServer: (serverId) => apiFetch(`/servers/${encodeURIComponent(serverId)}`),
  listChannels: (serverId, limit = 50, offset = 0) =>
    apiFetch(`/servers/${encodeURIComponent(serverId)}/channels?limit=${limit}&offset=${offset}`),
  listMembers: (serverId, { limit = 50, offset = 0, sortBy = "messages_sent", order = "desc" } = {}) =>
    apiFetch(
      `/servers/${encodeURIComponent(serverId)}/members?limit=${limit}&offset=${offset}&sort_by=${sortBy}&order=${order}`,
    ),
  getActivity: (serverId, { channelId = null, granularity = "day", from = null, to = null } = {}) => {
    const params = new URLSearchParams({ granularity });
    if (channelId) params.set("channel_id", channelId);
    if (from) params.set("from", from);
    if (to) params.set("to", to);
    return apiFetch(`/servers/${encodeURIComponent(serverId)}/activity?${params}`);
  },

  sendChatMessage: (message, sessionId) =>
    apiFetch("/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id: sessionId ?? null }),
    }),
  listSessions: (limit = 50, offset = 0) => apiFetch(`/chat/sessions?limit=${limit}&offset=${offset}`),
  getSessionMessages: (sessionId) => apiFetch(`/chat/sessions/${sessionId}/messages`),

  listPins: () => apiFetch("/pins"),
  createPin: (sessionId, toolCallId) =>
    apiFetch("/pins", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, tool_call_id: toolCallId }),
    }),
  deletePin: (pinId) => apiFetch(`/pins/${pinId}`, { method: "DELETE" }),
  reorderPins: (order) => apiFetch("/pins/order", { method: "PUT", body: JSON.stringify({ order }) }),
  refreshPin: (pinId) => apiFetch(`/pins/${pinId}/refresh`, { method: "POST" }),

  // Not routed through apiFetch: a successful download's body is a raw
  // file (CSV, workbook, ...), not JSON -- apiFetch's `resp.json()` would
  // fail on exactly the success case. Errors still come back as the same
  // {error: {...}} envelope, so those are parsed by hand instead.
  downloadPin: async (pinId, filenameHint) => {
    let resp;
    try {
      resp = await fetch(`${API_BASE_URL}/pins/${pinId}/download`);
    } catch {
      throw new ApiError("network_error", "Could not reach the API. Is it running?", 0, null);
    }
    if (!resp.ok) {
      const body = await resp.json().catch(() => null);
      const envelope = body && body.error;
      throw new ApiError(
        envelope?.code || "unknown_error",
        envelope?.message || `Download failed with status ${resp.status}`,
        resp.status,
        envelope?.details ?? null,
      );
    }
    const blob = await resp.blob();
    const disposition = resp.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : filenameHint || "download";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};
