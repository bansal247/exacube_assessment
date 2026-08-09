// Renders a plugin result by display_kind ("table" | "chart" | "image" |
// "file"), not by plugin name -- the one place in the frontend that
// dispatches generically, mirroring how the backend's pinning model never
// hardcodes a per-plugin chain either. A plugin whose shape doesn't match
// what's expected here still renders as raw JSON instead of crashing --
// the fallback a fifth plugin gets before anyone updates this file.

function renderArtifact(displayKind, data) {
  try {
    if (displayKind === "table") return renderTable(data);
    if (displayKind === "chart") return renderChart(data);
    if (displayKind === "image") return renderImage(data);
    if (displayKind === "file") return renderFileNote(data);
  } catch (err) {
    // A plugin's actual shape not matching what this renderer expects is
    // a frontend-lag issue, not a reason to break the whole chat turn.
  }
  return renderRawFallback(data);
}

function renderTable(data) {
  const rows = data?.rows ?? [];
  if (rows.length === 0) return el("div", { class: "state state-empty" }, "Query returned 0 rows.");

  const columns = Object.keys(rows[0]);
  const displayRows = rows.slice(0, 100);

  const thead = el("thead", {}, el("tr", {}, columns.map((c) => el("th", {}, c))));
  const tbody = el(
    "tbody",
    {},
    displayRows.map((row) => el("tr", {}, columns.map((c) => el("td", {}, formatCell(row[c]))))),
  );

  const wrap = el("div", { class: "artifact-table" }, [
    data?.sql ? el("div", { class: "artifact-sql" }, data.sql) : null,
    el("div", { class: "artifact-caption" }, `${data?.row_count ?? rows.length} row(s)`),
    el("div", { class: "table-scroll" }, el("table", {}, [thead, tbody])),
    rows.length > displayRows.length
      ? el("div", { class: "artifact-caption" }, `Showing first ${displayRows.length} of ${rows.length} rows.`)
      : null,
  ]);
  return wrap;
}

function formatCell(v) {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.join(", ") || "—";
  return String(v);
}

let chartCounter = 0;

function renderChart(spec) {
  const canvasId = `chart-${++chartCounter}`;
  const canvas = el("canvas", { id: canvasId });
  const downloadBtn = el("button", { class: "btn btn-small", disabled: true }, "Download PNG");
  const wrap = el("div", { class: "artifact-chart" }, [
    el("div", { class: "artifact-caption" }, spec?.title || ""),
    el("div", { class: "chart-canvas-wrap" }, canvas),
    downloadBtn,
  ]);

  // Chart.js needs the canvas in the DOM before it can size itself --
  // deferred one tick past insertion via requestAnimationFrame. The
  // download button stays disabled until there's an actual chart instance
  // to export -- chart_type charts don't get a server-side to_file() (see
  // README "chart returns a spec, not a rendered image"), so this button,
  // not a backend download, is the whole story for "save this chart."
  requestAnimationFrame(() => {
    const ctx = canvas.getContext("2d");
    const config = buildChartConfig(spec);
    if (!config) return;
    const chart = new Chart(ctx, config);
    downloadBtn.removeAttribute("disabled");
    downloadBtn.addEventListener("click", () => {
      const a = document.createElement("a");
      a.href = chart.toBase64Image("image/png", 1);
      a.download = `${(spec?.title || "chart").replace(/[^a-z0-9]+/gi, "_")}.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    });
  });

  return wrap;
}

function buildChartConfig(spec) {
  const rows = spec?.data ?? [];
  if (spec.chart_type === "histogram") {
    return buildHistogramConfig(rows, spec.value_field, spec.title);
  }
  if (spec.chart_type === "line" || spec.chart_type === "bar") {
    const labels = rows.map((r) => String(r[spec.x_field]));
    const values = rows.map((r) => Number(r[spec.y_field]));
    return {
      type: spec.chart_type,
      data: { labels, datasets: [{ label: spec.y_field, data: values, borderWidth: 1 }] },
      options: { responsive: true, plugins: { legend: { display: false } } },
    };
  }
  return null;
}

function buildHistogramConfig(rows, field, title) {
  const values = rows.map((r) => Number(r[field])).filter((v) => !Number.isNaN(v));
  if (values.length === 0) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const bucketCount = 10;
  const width = (max - min) / bucketCount || 1;
  const counts = new Array(bucketCount).fill(0);
  for (const v of values) {
    const idx = Math.min(bucketCount - 1, Math.floor((v - min) / width));
    counts[idx]++;
  }
  const labels = counts.map((_, i) => `${(min + i * width).toFixed(1)}–${(min + (i + 1) * width).toFixed(1)}`);
  return {
    type: "bar",
    data: { labels, datasets: [{ label: field, data: counts, borderWidth: 1 }] },
    options: { responsive: true, plugins: { legend: { display: false }, title: { display: !!title, text: title } } },
  };
}

function renderImage(data) {
  const src = data?.url || (data?.base64 ? `data:image/png;base64,${data.base64}` : null);
  if (!src) return renderRawFallback(data);
  return el("img", { class: "artifact-image", src, alt: "artifact" });
}

function renderFileNote(data) {
  return el("div", { class: "state state-empty" }, "This result is downloadable. Pin it to download it.");
}

function renderRawFallback(data) {
  return el("pre", { class: "artifact-raw" }, JSON.stringify(data, null, 2));
}
