import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

// Isolated containers only: never recreate the application's live backend.
const image = process.env.PROXY_TEST_IMAGE || "nginx:1.27-alpine";
const prefix = `ops-proxy-test-${process.pid}-${Date.now()}`;
const network = `${prefix}-net`;
const directory = mkdtempSync(join(tmpdir(), "ops-proxy-test-"));
const containers = [];
let networkCreated = false;
const docker = (...args) => execFileSync("docker", args, { encoding: "utf8", timeout: 60000, stdio: ["ignore", "pipe", "pipe"] }).trim();

function start(name, config, ...args) {
  docker("run", "-d", "--name", name, "--network", network,
    "-v", `${config}:/etc/nginx/conf.d/default.conf:ro`, ...args, image);
  containers.push(name);
}

async function awaitResponse(url, expected, options) {
  const deadline = performance.now() + 25000;
  let last = "no response";
  while (performance.now() < deadline) {
    try {
      const response = await fetch(url, { ...options, signal: AbortSignal.timeout(2000) });
      const body = await response.text();
      last = `${response.status} ${body}`;
      if (response.status === 200 && body === expected) return;
    } catch (error) { last = error.message; }
    await delay(250);
  }
  assert.fail(`Expected ${expected}; received ${last}`);
}

try {
  docker("image", "inspect", image);
  docker("network", "create", network);
  networkCreated = true;
  for (const label of ["first", "second"]) {
    writeFileSync(join(directory, `${label}.conf`),
      `server { listen 8000; location / { default_type text/plain; return 200 '${label}:$request_method:$request_uri'; } }\n`);
  }
  const first = `${prefix}-first`, second = `${prefix}-second`, proxy = `${prefix}-proxy`;
  const config = resolve(fileURLToPath(new URL("../nginx.conf", import.meta.url)));
  start(first, join(directory, "first.conf"), "--network-alias", "backend");
  start(proxy, config, "-p", "127.0.0.1::80");
  const binding = docker("port", proxy, "80/tcp");
  const base = `http://${binding}`;
  await awaitResponse(`${base}/api/auth/registration?probe=1`, "first:GET:/api/auth/registration?probe=1");
  const proxyId = docker("inspect", "--format", "{{.Id}}", proxy);
  const firstIp = docker("inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", first);

  // Start replacement before removing the first container to guarantee a different IP.
  start(second, join(directory, "second.conf"), "--network-alias", "backend");
  const secondIp = docker("inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", second);
  assert.notEqual(firstIp, secondIp);
  docker("rm", "-f", first);
  containers.splice(containers.indexOf(first), 1);

  await awaitResponse(`${base}/api/auth/registration?probe=2`, "second:GET:/api/auth/registration?probe=2");
  await awaitResponse(`${base}/api/auth/login`, "second:POST:/api/auth/login", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: "probe", password: "probe" }),
  });
  await awaitResponse(`${base}/health`, "second:GET:/health");
  assert.equal(docker("inspect", "--format", "{{.Id}}", proxy), proxyId);
  console.log("Proxy DNS replacement passed: API paths, query strings, POST and health recover without restarting frontend.");
} finally {
  for (const name of containers.reverse()) {
    try { docker("rm", "-f", name); } catch (error) { console.error(`Cleanup failed for ${name}: ${error.message}`); }
  }
  if (networkCreated) {
    try { docker("network", "rm", network); } catch (error) { console.error(`Network cleanup failed: ${error.message}`); }
  }
  rmSync(directory, { recursive: true, force: true });
}
