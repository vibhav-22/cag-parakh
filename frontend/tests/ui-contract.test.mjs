import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const pagePath = new URL("../app/page.tsx", import.meta.url);
const cssPath = new URL("../app/globals.css", import.meta.url);

test("keeps explicit loading, empty, error, and long-content states", async () => {
  const [page, css] = await Promise.all([
    readFile(pagePath, "utf8"),
    readFile(cssPath, "utf8"),
  ]);

  assert.match(page, /analysis-loading/);
  assert.match(page, /No review is open/);
  assert.match(page, /feedback-toast error/);
  assert.match(page, /analyzer-skeleton/);
  assert.match(css, /max-height: 340px/);
  assert.match(css, /overflow-wrap: anywhere/);
});

test("defines responsive and reduced-motion safeguards", async () => {
  const css = await readFile(cssPath, "utf8");

  assert.match(css, /@media \(max-width: 1120px\)/);
  assert.match(css, /@media \(max-width: 780px\)/);
  assert.match(css, /@media \(max-width: 430px\)/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.doesNotMatch(css, /transition:\s*all/);
});
