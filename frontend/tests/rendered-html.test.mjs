import assert from "node:assert/strict";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("renders the access shell on the server", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Parakh/);
  // The workspace is gated behind a client-side session check, so the server
  // paints the access shell first to avoid flashing the tool before auth
  // resolves. Full workspace markup is asserted from source in ui-contract.
  assert.match(html, /Checking access/);
  assert.doesNotMatch(html, /codex-preview/);
});
