// Small shared helpers so every view renders loading/error/empty states
// the same way instead of each reinventing it slightly differently.

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (typeof value === "boolean") {
      // HTML boolean attributes (disabled, checked, ...) are presence-based,
      // not value-based -- setAttribute("disabled", false) still disables
      // the element, since the *attribute itself* being present is what
      // counts. false has to remove the attribute, not set it to "false".
      if (value) node.setAttribute(key, "");
      else node.removeAttribute(key);
    } else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function renderLoading(container, label = "Loading…") {
  container.replaceChildren(el("div", { class: "state state-loading" }, label));
}

function renderError(container, err, onRetry) {
  const children = [el("div", { class: "state-title" }, "Something went wrong")];
  children.push(el("div", { class: "state-detail" }, err?.message || String(err)));
  if (onRetry) children.push(el("button", { class: "btn", onclick: onRetry }, "Retry"));
  container.replaceChildren(el("div", { class: "state state-error" }, children));
}

function renderEmpty(container, message) {
  container.replaceChildren(el("div", { class: "state state-empty" }, message));
}

function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
}
