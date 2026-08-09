// Load test for the artifact-generation path: creating, listing,
// refreshing, and downloading pinned artifacts. None of these touch the
// LLM -- refresh re-executes the plugin chain directly (query/chart),
// download renders a file from cached data -- so unlike chat.js, this one
// ramps toward a real breaking point instead of being cost-bounded.
//
// setup() makes exactly two real /chat calls (one query, one chart), no
// matter how high the ramp goes below -- that's the only LLM cost this
// script ever incurs, fixed and independent of VU count or duration.
import http from "k6/http";
import { check, sleep } from "k6";

const API_BASE_URL = __ENV.API_BASE_URL || "http://api:8000";

const JSON_HEADERS = { headers: { "Content-Type": "application/json" } };

export const options = {
  scenarios: {
    artifact_load: {
      executor: "ramping-vus",
      startVUs: 1,
      stages: [
        { duration: "20s", target: 10 },
        { duration: "20s", target: 30 },
        { duration: "20s", target: 60 },
        { duration: "15s", target: 0 },
      ],
    },
  },
};

export function setup() {
  const queryChat = http.post(
    `${API_BASE_URL}/chat`,
    JSON.stringify({ message: "How many members does server_001 have?", session_id: null }),
    { ...JSON_HEADERS, timeout: "60s" },
  );
  const queryBody = JSON.parse(queryChat.body);
  const queryCall = queryBody.tool_calls.find((tc) => tc.name === "query");

  const chartChat = http.post(
    `${API_BASE_URL}/chat`,
    JSON.stringify({
      message: "Chart the total messages per day for server_001 from daily_stats.",
      session_id: null,
    }),
    { ...JSON_HEADERS, timeout: "60s" },
  );
  const chartBody = JSON.parse(chartChat.body);
  const chartCall = chartBody.tool_calls.find((tc) => tc.name === "chart");

  const queryPin = http.post(
    `${API_BASE_URL}/pins`,
    JSON.stringify({ session_id: queryBody.session_id, tool_call_id: queryCall.tool_call_id }),
    JSON_HEADERS,
  );
  const chartPin = http.post(
    `${API_BASE_URL}/pins`,
    JSON.stringify({ session_id: chartBody.session_id, tool_call_id: chartCall.tool_call_id }),
    JSON_HEADERS,
  );

  return {
    queryPinId: JSON.parse(queryPin.body).pin_id,
    chartPinId: JSON.parse(chartPin.body).pin_id,
  };
}

export default function (data) {
  const list = http.get(`${API_BASE_URL}/pins`);
  check(list, { "list: status 200": (r) => r.status === 200 });

  const download = http.get(`${API_BASE_URL}/pins/${data.queryPinId}/download`);
  check(download, { "download: status 200": (r) => r.status === 200 });

  const refreshQuery = http.post(`${API_BASE_URL}/pins/${data.queryPinId}/refresh`, null, JSON_HEADERS);
  check(refreshQuery, { "refresh query: status 200": (r) => r.status === 200 });

  const refreshChart = http.post(`${API_BASE_URL}/pins/${data.chartPinId}/refresh`, null, JSON_HEADERS);
  check(refreshChart, { "refresh chart: status 200": (r) => r.status === 200 });

  sleep(0.5);
}
