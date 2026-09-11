import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import test from "node:test";

const PORT = 4174;
const ORIGIN = `http://localhost:${PORT}`;
const EMAIL = `onboarding-${process.pid}@example.com`;
const AUTH = {
  "oai-authenticated-user-email": EMAIL,
  "oai-authenticated-user-full-name": "Joy%20Tan",
  "oai-authenticated-user-full-name-encoding": "percent-encoded-utf-8",
};

async function waitForServer() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(ORIGIN, { headers: AUTH });
      if (response.ok) return;
    } catch {
      // Development worker is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("JoyMesh development worker did not start");
}

test("renders and resumes authenticated onboarding progress", async (context) => {
  const server = spawn("npm", ["run", "dev", "--", "--port", String(PORT)], {
    cwd: new URL("..", import.meta.url),
    env: { ...process.env, WRANGLER_LOG_PATH: ".wrangler/test.log" },
    stdio: "ignore",
  });
  context.after(() => server.kill("SIGTERM"));
  await waitForServer();

  const page = await fetch(ORIGIN, { headers: AUTH });
  assert.equal(page.status, 200);
  const html = await page.text();
  assert.match(html, /Set up your mesh/);
  assert.match(html, /No node connected/);
  assert.doesNotMatch(html, /localStorage/);

  const initial = await fetch(`${ORIGIN}/api/onboarding`, { headers: AUTH });
  assert.equal(initial.status, 200);
  assert.equal((await initial.json()).state, "ACCOUNT_READY");

  const saved = {
    state: "HARNESS_SELECTION",
    selectedHarnesses: ["codex", "opencode"],
    nodeName: "Test node",
    nodeOnline: false,
    environmentChecked: true,
    paidPolicy: "ask",
    fireconnect: false,
  };
  const update = await fetch(`${ORIGIN}/api/onboarding`, {
    method: "PUT",
    headers: { ...AUTH, "Content-Type": "application/json", Origin: ORIGIN },
    body: JSON.stringify(saved),
  });
  assert.equal(update.status, 200);
  assert.equal((await update.json()).state, "HARNESS_SELECTION");

  const resumed = await fetch(`${ORIGIN}/api/onboarding`, { headers: AUTH });
  const resumedBody = await resumed.json();
  assert.match(resumedBody.updatedAt, /^\d{4}-\d{2}-\d{2}T/);
  assert.equal(resumedBody.state, "HARNESS_SELECTION");
  assert.deepEqual(resumedBody.selectedHarnesses, ["codex", "opencode"]);
  // Without JOYMESH_CONTROL_PLANE_URL the Sites worker keeps a preview store only.
  assert.equal(resumedBody.synchronised, false);
  assert.equal(resumedBody.authority, "preview-unsynchronised");

  const crossOrigin = await fetch(`${ORIGIN}/api/onboarding`, {
    method: "PUT",
    headers: { ...AUTH, "Content-Type": "application/json", Origin: "https://attacker.test" },
    body: JSON.stringify(saved),
  });
  assert.equal(crossOrigin.status, 403);
});

test("pairing start requires control plane configuration", async (context) => {
  const server = spawn("npm", ["run", "dev", "--", "--port", String(PORT + 1)], {
    cwd: new URL("..", import.meta.url),
    env: { ...process.env, WRANGLER_LOG_PATH: ".wrangler/test-pairing.log" },
    stdio: "ignore",
  });
  context.after(() => server.kill("SIGTERM"));
  const origin = `http://localhost:${PORT + 1}`;
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(origin, { headers: AUTH });
      if (response.ok) break;
    } catch {
      // still starting
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  const pairing = await fetch(`${origin}/api/pairing/start`, {
    method: "POST",
    headers: { ...AUTH, "Content-Type": "application/json", Origin: origin },
    body: "{}",
  });
  assert.equal(pairing.status, 503);
  const body = await pairing.json();
  assert.equal(body.code, "control_plane_unavailable");
});
