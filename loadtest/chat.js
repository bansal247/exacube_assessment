// Load test for POST /chat. Deliberately NOT a breaking-point search --
// every request here is a real LLM call, so scaling this up to find where
// it falls over would cost real, unbounded money. `shared-iterations`
// caps the TOTAL number of requests regardless of duration or VU count,
// so this run's cost is fixed and known in advance, not a function of how
// long it happens to run. Real concurrency (3 VUs) is still exercised --
// just not ramped toward a breaking point. See README "Load test results"
// for the honest scope note this implies.
import http from "k6/http";
import { check, sleep } from "k6";

const API_BASE_URL = __ENV.API_BASE_URL || "http://api:8000";

// Short, cheap questions -- no chart/chain questions here, which would
// cost more tokens per turn and make the total cost less predictable.
const QUESTIONS = [
  "How many servers are in this dataset in total?",
  "How many channels are there across all servers combined?",
  "What region is the server with id 'server_001' in?",
  "What's the weather like in Tokyo today?",
];

export const options = {
  scenarios: {
    chat_load: {
      executor: "shared-iterations",
      vus: 3,
      iterations: 24,
      maxDuration: "5m",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.05"],
  },
};

export default function () {
  const question = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
  const res = http.post(
    `${API_BASE_URL}/chat`,
    JSON.stringify({ message: question, session_id: null }),
    { headers: { "Content-Type": "application/json" }, timeout: "60s" },
  );
  check(res, {
    "status is 200": (r) => r.status === 200,
    "has a reply": (r) => {
      try {
        return JSON.parse(r.body).reply.length > 0;
      } catch {
        return false;
      }
    },
  });
  sleep(1);
}
