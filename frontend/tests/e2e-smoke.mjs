import assert from "node:assert/strict";

const base = process.env.E2E_BASE_URL || "http://127.0.0.1:5175";
const page = await fetch(base);
assert.equal(page.status, 200, "frontend root must be reachable");
assert.match(await page.text(), /<div id="root"><\/div>/, "SPA root element must exist");

if (process.env.E2E_SKIP_API !== "1") {
  const registration = await fetch(`${base}/api/auth/registration`);
  assert.equal(registration.status, 200, "login page must load registration settings through the proxy");
  assert.equal(typeof (await registration.json()).enabled, "boolean");
  const protectedApi = await fetch(`${base}/api/auth/me`);
  assert.equal(protectedApi.status, 401, "frontend proxy must reach the protected backend API");
}
