import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";
import { createServer, preview } from "vite";
import { startStaticServer } from "./static-server.mjs";

const profile = await mkdtemp(join(tmpdir(), "workspace-turso-browser-"));
const staticHosting = process.argv.includes("--static");
const built = staticHosting || process.argv.includes("--built");
const url = `http://127.0.0.1:4387/${staticHosting ? "proof/" : ""}`;
const server = staticHosting ? await startStaticServer() : built ? await preview() : await createServer();
let context;
const errors = [];
const externalRequests = [];

async function openPage() {
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("request", (request) => {
    if (/^https?:/.test(request.url()) && !request.url().startsWith("http://127.0.0.1:4387/")) {
      externalRequests.push(request.url());
    }
  });
  await page.goto(url);
  await ready(page);
  return page;
}

async function ready(page) {
  await page.waitForFunction(() => window.storageProbe || window.storageProbeError, undefined, { timeout: 60_000 });
  const error = await page.evaluate(() => window.storageProbeError);
  assert.equal(error, undefined);
}

try {
  if (!built) await server.listen();
  const rawResponse = await fetch(url);
  const serverHeaders = {
    coop: rawResponse.headers.get("Cross-Origin-Opener-Policy"),
    coep: rawResponse.headers.get("Cross-Origin-Embedder-Policy"),
  };
  if (staticHosting) assert.deepEqual(serverHeaders, { coop: null, coep: null });
  context = await chromium.launchPersistentContext(profile, { headless: true });
  let page = await openPage();
  const initial = await page.evaluate(() => window.storageProbe.read());
  assert.equal(initial.created_identity, true);
  assert.equal(initial.identity_verified, true);
  if (staticHosting) assert.ok(initial.isolation_service_worker?.endsWith("/proof/coi-serviceworker.js"));
  const body = "Durable browser note: survives tab reload and browser-process restart.";
  const saved = await page.evaluate((body) => window.storageProbe.save(body), body);
  assert.equal(saved.note.body, body);
  const strict = await page.evaluate(() => window.storageProbe.verifyStrict());
  await page.reload();
  await ready(page);
  const reloaded = await page.evaluate(() => window.storageProbe.read());
  assert.equal(reloaded.created_identity, false);
  assert.equal(reloaded.device_id, initial.device_id);
  assert.deepEqual(reloaded.note, saved.note);
  assert.equal(reloaded.identity_verified, true);
  await context.close();
  context = await chromium.launchPersistentContext(profile, { headless: true });
  page = await openPage();
  const restarted = await page.evaluate(() => window.storageProbe.read());
  assert.equal(restarted.created_identity, false);
  assert.equal(restarted.device_id, initial.device_id);
  assert.deepEqual(restarted.note, saved.note);
  assert.equal(restarted.identity_verified, true);
  assert.ok(restarted.files.some((file) => file.name.endsWith(".db") && file.bytes > 0));
  const isolatedContext = await context.browser().newContext();
  try {
    const isolatedPage = await isolatedContext.newPage();
    await isolatedPage.goto(url);
    await ready(isolatedPage);
    const isolated = await isolatedPage.evaluate(() => window.storageProbe.read());
    assert.equal(isolated.created_identity, true);
    assert.notEqual(isolated.device_id, initial.device_id);
  } finally {
    await isolatedContext.close();
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(externalRequests, []);
  const report = {
    verified_at: new Date().toISOString(),
    build: staticHosting ? "static-production-service-worker" : built ? "production" : "development",
    server_headers: serverHeaders,
    browser: context.browser().version(),
    reload_passed: true,
    browser_restart_passed: true,
    isolated_context_gets_distinct_identity: true,
    external_requests: externalRequests,
    strict,
    restarted,
  };
  await mkdir("artifacts", { recursive: true });
  const evidenceName = staticHosting ? "chromium-static-storage" : "chromium-storage";
  await writeFile(`artifacts/${evidenceName}-evidence.json`, JSON.stringify(report, null, 2) + "\n");
  await page.screenshot({ path: `artifacts/${evidenceName}-proof.png`, fullPage: true });
  console.log(JSON.stringify(report, null, 2));
} finally {
  await context?.close();
  if (built) {
    await new Promise((resolve, reject) => server.httpServer.close((error) => error ? reject(error) : resolve()));
  } else {
    await server.close();
  }
  await rm(profile, { recursive: true, force: true });
}
