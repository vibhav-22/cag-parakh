import assert from "node:assert/strict";
import test from "node:test";

async function render() {
  // The build targets plain Node (`vinext start`), so dist/server/index.js
  // default-exports the fetch handler itself. It took a Worker's
  // (request, env, ctx) shape back when the Cloudflare plugin wrapped it.
  const entryUrl = new URL("../dist/server/index.js", import.meta.url);
  entryUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: handler } = await import(entryUrl.href);
  return handler(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
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
