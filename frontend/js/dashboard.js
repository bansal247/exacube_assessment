// The pinned dashboard. Every pin renders via renderArtifact(display_kind,
// cached_data) -- the same generic dispatch chat.js uses -- so this view
// needs no per-plugin knowledge either.

function initDashboard(root) {
  const listEl = el("div", { class: "dashboard-list" });
  root.appendChild(
    el("div", { class: "dashboard-layout" }, [
      el("div", { class: "dashboard-toolbar" }, [el("button", { class: "btn", onclick: load }, "Refresh list")]),
      listEl,
    ]),
  );

  load();

  async function load() {
    renderLoading(listEl, "Loading pins…");
    try {
      const body = await api.listPins();
      renderPins(body.items);
    } catch (err) {
      renderError(listEl, err, load);
    }
  }

  function renderPins(pins) {
    if (pins.length === 0) {
      renderEmpty(listEl, "No pins yet. Pin a result from the chat to see it here.");
      return;
    }
    const sorted = [...pins].sort((a, b) => a.position - b.position);
    listEl.replaceChildren(...sorted.map((pin, i) => renderPinCard(pin, i, sorted)));
  }

  function renderPinCard(pin, index, allPins) {
    const header = el("div", { class: "pin-header" }, [
      el("span", { class: "pin-title" }, pin.title),
      el("span", { class: "badge" }, pin.plugin_name),
    ]);

    const body = el("div", { class: "pin-body" }, renderArtifact(pin.display_kind, pin.cached_data));

    const meta = el("div", { class: "chat-meta" }, `Cached ${formatDateTime(pin.cached_at)}`);

    const actions = el("div", { class: "pin-actions" }, [
      el(
        "button",
        { class: "btn btn-small", disabled: index === 0, onclick: () => move(pin, index, allPins, -1) },
        "↑",
      ),
      el(
        "button",
        {
          class: "btn btn-small",
          disabled: index === allPins.length - 1,
          onclick: () => move(pin, index, allPins, 1),
        },
        "↓",
      ),
      el("button", { class: "btn btn-small", onclick: () => refresh(pin.pin_id) }, "Refresh"),
      // Only "table"/"file" kinds have a real to_file() on the backend
      // (query -> CSV; a future excel/powerpoint -> its own output).
      // "chart" gets its own in-card PNG export (see artifact.js) instead
      // of this button, which would otherwise just 400.
      pin.display_kind === "table" || pin.display_kind === "file"
        ? el("button", { class: "btn btn-small", onclick: () => download(pin) }, "Download")
        : null,
      el("button", { class: "btn btn-small btn-danger", onclick: () => unpin(pin.pin_id) }, "Unpin"),
    ]);

    return el("div", { class: "pin-card" }, [header, body, meta, actions]);
  }

  async function move(pin, index, allPins, direction) {
    const newOrder = [...allPins];
    const target = index + direction;
    if (target < 0 || target >= newOrder.length) return;
    [newOrder[index], newOrder[target]] = [newOrder[target], newOrder[index]];
    try {
      await api.reorderPins(newOrder.map((p) => p.pin_id));
      await load();
    } catch (err) {
      renderError(listEl, err, load);
    }
  }

  async function refresh(pinId) {
    try {
      await api.refreshPin(pinId);
      await load();
    } catch (err) {
      renderError(listEl, err, load);
    }
  }

  async function unpin(pinId) {
    try {
      await api.deletePin(pinId);
      await load();
    } catch (err) {
      renderError(listEl, err, load);
    }
  }

  async function download(pin) {
    try {
      await api.downloadPin(pin.pin_id, pin.title);
    } catch (err) {
      alert(err.message);
    }
  }
}
