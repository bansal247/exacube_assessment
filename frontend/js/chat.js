// Non-streaming by design (see README "Design decisions" -- streaming was
// cut in Part 3). A turn is one request/response: the input disables and a
// "thinking" indicator shows while it's in flight, then the full reply and
// tool-call trace render at once when the response arrives. A dead
// connection or a slow response both look the same here -- a pending
// state that either resolves or errors, not a partial one.
//
// Two ways messages end up on screen: a live turn (appendAssistantTurn,
// working off a single ChatResponse) and a loaded past session
// (renderHistory, working off the flat SessionMessage list GET
// /chat/sessions/{id}/messages returns) -- both funnel through the same
// renderToolCall()/renderArtifact() so a past conversation looks exactly
// like a live one, not a degraded replay.

const CHAT_SESSION_KEY = "discord_analytics_chat_session_id";

function initChat(root) {
  let sessionId = localStorage.getItem(CHAT_SESSION_KEY) || null;
  let pending = false;

  const messagesEl = el("div", { class: "chat-messages" });
  const emptyState = el("div", { class: "state state-empty" }, "Ask something about the dataset to get started.");
  messagesEl.appendChild(emptyState);

  const sessionListEl = el("div", { class: "session-list" });

  const input = el("textarea", {
    class: "chat-input",
    placeholder: "Ask a question, e.g. \"which channels are dying?\"",
    rows: "2",
  });
  const sendBtn = el("button", { class: "btn btn-primary", onclick: () => send() }, "Send");
  const newChatBtn = el("button", { class: "btn btn-ghost", onclick: () => startNewChat() }, "+ New chat");

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  root.appendChild(
    el("div", { class: "chat-layout" }, [
      el("div", { class: "chat-sidebar" }, [
        el("div", { class: "chat-sidebar-header" }, [newChatBtn]),
        sessionListEl,
      ]),
      el("div", { class: "chat-panel" }, [
        messagesEl,
        el("div", { class: "chat-composer" }, [input, sendBtn]),
      ]),
    ]),
  );

  loadSessionList();
  if (sessionId) openSession(sessionId, { skipIfAlreadyShown: false });

  function startNewChat() {
    sessionId = null;
    localStorage.removeItem(CHAT_SESSION_KEY);
    messagesEl.replaceChildren(emptyState);
    highlightActiveSession();
  }

  async function loadSessionList() {
    try {
      const body = await api.listSessions(50, 0);
      renderSessionList(body.items);
    } catch (err) {
      sessionListEl.replaceChildren(el("div", { class: "state-detail" }, "Couldn't load past conversations."));
    }
  }

  function renderSessionList(sessions) {
    if (sessions.length === 0) {
      sessionListEl.replaceChildren(el("div", { class: "state-detail" }, "No conversations yet."));
      return;
    }
    sessionListEl.replaceChildren(
      ...sessions.map((s) =>
        el(
          "button",
          {
            class: "session-item",
            "data-session-id": s.session_id,
            onclick: () => openSession(s.session_id, { skipIfAlreadyShown: true }),
          },
          [
            el("div", { class: "session-preview" }, s.preview || "(empty conversation)"),
            el("div", { class: "session-date" }, formatDateTime(s.updated_at)),
          ],
        ),
      ),
    );
    highlightActiveSession();
  }

  function highlightActiveSession() {
    for (const node of sessionListEl.querySelectorAll(".session-item")) {
      node.classList.toggle("active", node.dataset.sessionId === sessionId);
    }
  }

  async function openSession(id, { skipIfAlreadyShown }) {
    if (skipIfAlreadyShown && id === sessionId && messagesEl.dataset.loadedSession === id) return;
    sessionId = id;
    localStorage.setItem(CHAT_SESSION_KEY, sessionId);
    highlightActiveSession();
    renderLoading(messagesEl, "Loading conversation…");
    try {
      const body = await api.getSessionMessages(sessionId);
      messagesEl.dataset.loadedSession = sessionId;
      renderHistory(body.messages);
    } catch (err) {
      renderError(messagesEl, err, () => openSession(id, { skipIfAlreadyShown: false }));
    }
  }

  function renderHistory(messages) {
    const turns = groupIntoTurns(messages);
    if (turns.length === 0) {
      messagesEl.replaceChildren(emptyState);
      return;
    }
    messagesEl.replaceChildren(
      ...turns.flatMap((turn) => {
        const nodes = [];
        if (turn.userText) nodes.push(el("div", { class: "chat-message chat-message-user" }, turn.userText));
        if (turn.replyText || turn.toolCalls.length > 0) {
          nodes.push(renderAssistantTurnNode(turn.replyText, turn.toolCalls, sessionId));
        }
        return nodes;
      }),
    );
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function groupIntoTurns(messages) {
    const turns = [];
    let current = null;
    const argsByCallId = {};
    for (const m of messages) {
      if (m.role === "assistant") {
        for (const tc of m.tool_calls || []) argsByCallId[tc.id] = tc.arguments;
      }
    }
    for (const m of messages) {
      if (m.role === "user") {
        current = { userText: m.content, replyText: null, toolCalls: [] };
        turns.push(current);
      } else if (m.role === "assistant") {
        if (!current) {
          current = { userText: null, replyText: null, toolCalls: [] };
          turns.push(current);
        }
        if (m.content) current.replyText = m.content;
      } else if (m.role === "tool") {
        if (!current) {
          current = { userText: null, replyText: null, toolCalls: [] };
          turns.push(current);
        }
        current.toolCalls.push({
          tool_call_id: m.tool_call_id,
          name: m.tool_name,
          arguments: argsByCallId[m.tool_call_id] || {},
          is_error: m.is_error,
          result: m.result,
          display_kind: m.display_kind,
        });
      }
    }
    return turns;
  }

  async function send() {
    const message = input.value.trim();
    if (!message || pending) return;

    if (messagesEl.contains(emptyState)) messagesEl.removeChild(emptyState);
    appendMessage("user", message);
    input.value = "";
    setPending(true);
    const thinking = el("div", { class: "chat-message chat-message-assistant chat-thinking" }, "Thinking…");
    messagesEl.appendChild(thinking);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    try {
      const body = await api.sendChatMessage(message, sessionId);
      const isNewSession = sessionId !== body.session_id;
      sessionId = body.session_id;
      messagesEl.dataset.loadedSession = sessionId;
      localStorage.setItem(CHAT_SESSION_KEY, sessionId);
      thinking.remove();
      messagesEl.appendChild(renderAssistantTurnNode(body.reply, body.tool_calls, sessionId, body));
      messagesEl.scrollTop = messagesEl.scrollHeight;
      loadSessionList();
      if (isNewSession) highlightActiveSession();
    } catch (err) {
      thinking.remove();
      appendMessage("error", err.message || "The request failed.", () => {
        input.value = message;
        send();
      });
    } finally {
      setPending(false);
    }
  }

  function setPending(value) {
    pending = value;
    input.disabled = value;
    sendBtn.disabled = value;
  }

  function appendMessage(role, text, onRetry) {
    const bubble = el("div", { class: `chat-message chat-message-${role}` }, text);
    if (onRetry) bubble.appendChild(el("button", { class: "btn btn-small", onclick: onRetry }, "Retry"));
    messagesEl.appendChild(bubble);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

// toolCalls: [{tool_call_id, name, arguments, is_error, result, display_kind}]
// meta (optional): the raw ChatResponse, for the latency/token footer on a
// live turn -- absent when rendering loaded history, which never carried
// per-turn latency/token figures in the first place.
function renderAssistantTurnNode(replyText, toolCalls, sessionIdForPin, meta) {
  const wrap = el("div", { class: "chat-message chat-message-assistant" });
  if (replyText) wrap.appendChild(el("div", { class: "chat-reply" }, replyText));

  for (const call of toolCalls) {
    wrap.appendChild(renderToolCall(call, sessionIdForPin));
  }

  if (meta) {
    wrap.appendChild(
      el(
        "div",
        { class: "chat-meta" },
        `${meta.latency_ms.toFixed(0)}ms · ${meta.input_tokens}in/${meta.output_tokens}out tokens`,
      ),
    );
  }
  return wrap;
}

function renderToolCall(call, sessionIdForPin) {
  const header = el("div", { class: "tool-call-header" }, [
    el("span", { class: "tool-call-name" }, call.name),
    call.is_error ? el("span", { class: "badge badge-error" }, "error") : null,
  ]);

  const body = el("div", { class: "tool-call-body" });
  if (call.is_error) {
    body.appendChild(el("div", { class: "tool-call-error" }, String(call.result)));
  } else {
    body.appendChild(renderArtifact(call.display_kind, call.result));
  }

  const card = el("div", { class: "tool-call" }, [header, body]);

  if (!call.is_error && call.display_kind) {
    const pinBtn = el(
      "button",
      {
        class: "btn btn-small",
        onclick: async (e) => {
          e.target.disabled = true;
          e.target.textContent = "Pinning…";
          try {
            await api.createPin(sessionIdForPin, call.tool_call_id);
            e.target.textContent = "Pinned ✓";
          } catch (err) {
            e.target.textContent = "Pin failed";
            e.target.title = err.message;
            e.target.disabled = false;
          }
        },
      },
      "Pin to dashboard",
    );
    card.appendChild(pinBtn);
  }

  return card;
}
