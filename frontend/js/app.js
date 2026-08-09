// Three tabs, each its own module (chat.js, explore.js, dashboard.js) --
// initialized once and left in the DOM (display: none when hidden) rather
// than torn down and rebuilt on every switch, so an in-progress chat isn't
// lost by tabbing away to check the dashboard.

const TABS = [
  { id: "chat", label: "Chat", init: initChat },
  { id: "explore", label: "Explore", init: initExplore },
  { id: "dashboard", label: "Dashboard", init: initDashboard },
];

function initApp() {
  const nav = el("nav", { class: "tabs" });
  const panels = el("div", { class: "panels" });

  for (const tab of TABS) {
    const panel = el("div", { class: "panel", id: `panel-${tab.id}` });
    panel.style.display = "none";
    panels.appendChild(panel);
    tab.init(panel);

    const btn = el("button", { class: "tab-btn", onclick: () => activate(tab.id) }, tab.label);
    btn.dataset.tab = tab.id;
    nav.appendChild(btn);
  }

  document.getElementById("app").append(nav, panels);
  activate("chat");

  function activate(tabId) {
    for (const tab of TABS) {
      document.getElementById(`panel-${tab.id}`).style.display = tab.id === tabId ? "" : "none";
    }
    for (const btn of nav.querySelectorAll(".tab-btn")) {
      btn.classList.toggle("active", btn.dataset.tab === tabId);
    }
  }
}

document.addEventListener("DOMContentLoaded", initApp);
