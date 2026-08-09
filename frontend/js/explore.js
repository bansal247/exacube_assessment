// Part 2's own endpoints (servers/members/activity), independent of the
// agent -- the plain data table + chart the brief asks for, not the
// agent-produced ones that show up in chat/dashboard.

function initExplore(root) {
  let selectedServerId = null;
  let activityChart = null;

  const serverSelect = el("select", { class: "select", onchange: (e) => selectServer(e.target.value) }, [
    el("option", { value: "" }, "Select a server…"),
  ]);

  const membersTableWrap = el("div", { class: "explore-panel" });
  const activityChartWrap = el("div", { class: "explore-panel" });

  const granularitySelect = el(
    "select",
    { class: "select", onchange: () => selectedServerId && loadActivity(selectedServerId) },
    [el("option", { value: "day" }, "Daily"), el("option", { value: "hour" }, "Hourly")],
  );

  root.appendChild(
    el("div", { class: "explore-layout" }, [
      el("div", { class: "explore-toolbar" }, [
        el("label", {}, "Server:"),
        serverSelect,
        el("label", {}, "Granularity:"),
        granularitySelect,
      ]),
      el("div", { class: "explore-grid" }, [
        el("div", {}, [el("h3", {}, "Top members"), membersTableWrap]),
        el("div", {}, [el("h3", {}, "Activity over time"), activityChartWrap]),
      ]),
    ]),
  );

  loadServers();

  async function loadServers() {
    try {
      const body = await api.listServers(200, 0);
      for (const server of body.items) {
        serverSelect.appendChild(el("option", { value: server.server_id }, `${server.server_name} (${server.server_id})`));
      }
    } catch (err) {
      renderError(membersTableWrap, err, loadServers);
    }
  }

  function selectServer(serverId) {
    selectedServerId = serverId || null;
    if (!selectedServerId) {
      renderEmpty(membersTableWrap, "Select a server to see its members.");
      renderEmpty(activityChartWrap, "Select a server to see its activity.");
      return;
    }
    loadMembers(selectedServerId);
    loadActivity(selectedServerId);
  }

  async function loadMembers(serverId) {
    renderLoading(membersTableWrap, "Loading members…");
    try {
      const body = await api.listMembers(serverId, { limit: 25, sortBy: "messages_sent", order: "desc" });
      renderMembersTable(body);
    } catch (err) {
      renderError(membersTableWrap, err, () => loadMembers(serverId));
    }
  }

  function renderMembersTable(body) {
    if (body.items.length === 0) {
      renderEmpty(membersTableWrap, "No members found for this server.");
      return;
    }
    const columns = ["display_name", "username", "messages_sent", "voice_minutes", "is_owner"];
    const thead = el("thead", {}, el("tr", {}, columns.map((c) => el("th", {}, c))));
    const tbody = el(
      "tbody",
      {},
      body.items.map((m) => el("tr", {}, columns.map((c) => el("td", {}, formatCell(m[c]))))),
    );
    membersTableWrap.replaceChildren(
      el("div", { class: "table-scroll" }, el("table", {}, [thead, tbody])),
      el("div", { class: "artifact-caption" }, `${body.page.total} member(s) total, showing top ${body.items.length}`),
    );
  }

  async function loadActivity(serverId) {
    renderLoading(activityChartWrap, "Loading activity…");
    try {
      const body = await api.getActivity(serverId, { granularity: granularitySelect.value });
      renderActivityChart(body);
    } catch (err) {
      renderError(activityChartWrap, err, () => loadActivity(serverId));
    }
  }

  function renderActivityChart(body) {
    if (body.items.length === 0) {
      renderEmpty(activityChartWrap, "No activity recorded for this server.");
      return;
    }
    const canvas = el("canvas");
    activityChartWrap.replaceChildren(el("div", { class: "chart-canvas-wrap" }, canvas));

    if (activityChart) {
      activityChart.destroy();
      activityChart = null;
    }
    requestAnimationFrame(() => {
      activityChart = new Chart(canvas.getContext("2d"), {
        type: "line",
        data: {
          labels: body.items.map((b) => formatDateTime(b.bucket)),
          datasets: [{ label: "Messages", data: body.items.map((b) => b.message_count), borderWidth: 1 }],
        },
        options: { responsive: true, plugins: { legend: { display: false } } },
      });
    });
  }

  renderEmpty(membersTableWrap, "Select a server to see its members.");
  renderEmpty(activityChartWrap, "Select a server to see its activity.");
}
