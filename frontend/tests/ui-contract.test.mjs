import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (relative) => readFile(new URL(relative, import.meta.url), "utf8");

const paths = {
  home: "../app/page.tsx",
  css: "../app/globals.css",
  shellCss: "../app/styles/shell.css",
  caseCss: "../app/styles/case.css",
  advancedSettings: "../app/advanced-settings.tsx",
  controlPanel: "../app/components/control-panel.tsx",
  shell: "../app/components/app-shell.tsx",
  rail: "../app/components/nav-rail.tsx",
  session: "../app/lib/session.tsx",
  format: "../app/lib/format.ts",
  useBatch: "../app/lib/use-batch.ts",
  newRoute: "../app/new/page.tsx",
  ask: "../app/ask/page.tsx",
  history: "../app/history/page.tsx",
  settings: "../app/settings/page.tsx",
  batch: "../app/batches/[batchId]/page.tsx",
  document: "../app/batches/[batchId]/documents/[jobId]/page.tsx",
};

const ROUTES = [
  ["home", paths.home],
  ["new", paths.newRoute],
  ["ask", paths.ask],
  ["history", paths.history],
  ["settings", paths.settings],
  ["batches", paths.batch],
  ["document", paths.document],
];

test("keeps explicit loading, empty, error, and long-content states", async () => {
  const [home, css, controlPanel, session, document, history] = await Promise.all([
    read(paths.home),
    read(paths.css),
    read(paths.controlPanel),
    read(paths.session),
    read(paths.document),
    read(paths.history),
  ]);

  assert.match(document, /analysis-loading/);
  assert.match(controlPanel, /Investigation preset|preset-options/);
  assert.match(home, /poster-title/, "the landing route must present the poster");
  assert.match(session, /Checking access/, "the access gate must state what it is doing");
  assert.match(controlPanel, /feedback-toast error/);
  assert.match(controlPanel, /analyzer-skeleton/);
  assert.match(document, /finding-tests/, "per-test result table must render structured analyzer tests");
  assert.match(css, /max-height: 340px/);
  assert.match(css, /overflow-wrap: anywhere/);
  assert.match(history, /No cases yet/i, "an empty case list must say so rather than vanish");
});

test("labels the whitener detector and keeps raw output behind a disclosure", async () => {
  const [format, document] = await Promise.all([read(paths.format), read(paths.document)]);

  assert.match(format, /Whitener Detection/, "tamper scan must be presented as the whitener detector");
  assert.match(format, /Open annotated PDF/, "whitener artifacts must have clear labels");
  assert.match(document, /raw-output/, "raw analyzer JSON must stay behind a disclosure");
});

test("shows an extracted document photo inside its result", async () => {
  const [format, document, caseCss] = await Promise.all([
    read(paths.format),
    read(paths.document),
    read(paths.caseCss),
  ]);

  assert.match(format, /Document Photo/);
  assert.match(await read(paths.advancedSettings), /photo_detection/);
  assert.match(document, /detectedPhoto/);
  assert.match(document, /Photo detected in/);
  assert.match(caseCss, /\.app\[data-route="document"\] \.detected-photo/);
});

test("defines responsive and reduced-motion safeguards", async () => {
  const [css, shellCss] = await Promise.all([read(paths.css), read(paths.shellCss)]);

  assert.match(css, /@media \(max-width: 1120px\)/);
  assert.match(css, /@media \(max-width: 780px\)/);
  assert.match(css, /@media \(max-width: 430px\)/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.doesNotMatch(css, /transition:\s*all/);
  assert.match(shellCss, /@media \(max-width: 900px\)/, "the rail must collapse on narrow viewports");
});

test("provides advanced detector controls with safe defaults", async () => {
  const [controlPanel, settingsComponent, css] = await Promise.all([
    read(paths.controlPanel),
    read(paths.advancedSettings),
    read(paths.css),
  ]);

  assert.match(controlPanel, /body\.append\("settings"/);
  assert.match(settingsComponent, /Minimum QR codes/);
  assert.match(settingsComponent, /Maximum image noise/);
  assert.match(settingsComponent, /Minimum sharpness/);
  assert.match(settingsComponent, /Reset defaults/);
  assert.match(css, /\.setting-group\.disabled/);
  assert.match(css, /\.analyzer-switch/);
});

test("accepts common image documents as well as PDFs", async () => {
  const [controlPanel, newRoute] = await Promise.all([
    read(paths.controlPanel),
    read(paths.newRoute),
  ]);

  assert.match(controlPanel, /image\/jpeg/);
  assert.match(controlPanel, /image\/png/);
  assert.match(controlPanel, /image\/webp/);
  assert.match(controlPanel, /image\/tiff/);
  assert.match(controlPanel, /Choose PDFs or images/);
  assert.match(newRoute, /Pick PDFs or images/);
});

// Pass 1: a case is a URL, not React state. These assertions are what stop the
// app sliding back to one route.
test("gives every case an addressable route", async () => {
  const [controlPanel, batch, document, useBatch, history] = await Promise.all([
    read(paths.controlPanel),
    read(paths.batch),
    read(paths.document),
    read(paths.useBatch),
    read(paths.history),
  ]);

  assert.match(controlPanel, /router\.push\(`\/batches\/\$\{payload\.id\}`\)/, "submitting must navigate to the batch route");
  assert.match(history, /\/batches\/\$\{/, "history must link to real batch URLs");
  assert.match(useBatch, /api\/v1\/batches\/\$\{batchId\}/, "the batch route must hydrate from its id");
  assert.match(batch, /useParams/, "the batch route must read its id from the URL");
  assert.match(document, /useParams/, "the document route must read its ids from the URL");
  assert.match(batch, /router\.replace/, "a single-document batch must forward to its document");
  assert.match(document, /case-breadcrumb/, "a document two levels deep needs a breadcrumb");
  assert.match(document, /flag-walk/, "batch review needs prev/next across flagged documents");
});

test("states what happens when a case id does not resolve", async () => {
  const [batch, document, css] = await Promise.all([
    read(paths.batch),
    read(paths.document),
    read(paths.css),
  ]);

  assert.match(batch, /not on this device/, "an unknown batch must explain why, not 404 blankly");
  assert.match(document, /not on this device/, "an unknown document must explain why");
  assert.match(batch, /stall-notice|pollStalled/, "polling must have a stall escape hatch");
  assert.match(document, /stall-notice|pollStalled/, "polling must have a stall escape hatch");
  assert.match(css, /\.route-message/);
});

// Pass 2: each route is its own room. Every route declares a distinct surface,
// and no route may reuse another's identity.
test("every route declares its own surface and chrome", async () => {
  const files = await Promise.all(ROUTES.map(([, path]) => read(path)));

  for (const [index, [name]] of ROUTES.entries()) {
    assert.match(
      files[index],
      new RegExp(`route="${name}"`),
      `the ${name} route must declare route="${name}" so it gets its own surface`,
    );
  }

  const [home] = files;
  assert.match(home, /chrome="bare"/, "home must have no navigation rail");

  const documentSource = files[ROUTES.findIndex(([name]) => name === "document")];
  assert.match(documentSource, /chrome="icons"/, "the workspace must collapse the rail so evidence dominates");
});

test("the surface ladder gives each route a different depth", async () => {
  const shellCss = await read(paths.shellCss);

  const depths = new Map();
  for (const [name] of ROUTES) {
    const block = shellCss.match(new RegExp(`\\.app\\[data-route="${name}"\\]\\s*\\{[^}]*\\}`));
    assert.ok(block, `shell.css must define a surface block for the ${name} route`);
    const canvas = block[0].match(/--route-canvas:\s*(#[0-9a-f]{3,8})/i);
    assert.ok(canvas, `the ${name} route must set its own --route-canvas`);
    depths.set(name, canvas[1].toLowerCase());
  }

  assert.equal(
    new Set(depths.values()).size,
    depths.size,
    `no two routes may share a background: ${JSON.stringify(Object.fromEntries(depths))}`,
  );
});

test("route stylesheets stay scoped so they cannot collide", async () => {
  const sheets = ["home", "new", "ask", "history", "settings", "case"];
  for (const sheet of sheets) {
    const source = await read(`../app/styles/${sheet}.css`);
    const rules = source
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split("}")
      .map((chunk) => chunk.split("{")[0].trim())
      .filter((selector) => selector && !selector.startsWith("@") && !selector.startsWith("/*"));

    for (const selector of rules) {
      assert.ok(
        selector.split(",").every((part) => /\.app\[data-route=|^\.mono-ref$|^\d|^from$|^to$/.test(part.trim())),
        `${sheet}.css must scope every selector under .app[data-route="..."], found: ${selector}`,
      );
    }
  }
});

test("the semantic palette is never used for route identity", async () => {
  const shellCss = await read(paths.shellCss);
  const surfaceBlocks = shellCss.match(/\.app\[data-route="[a-z]+"\]\s*\{[^}]*\}/g) || [];

  for (const block of surfaceBlocks) {
    assert.doesNotMatch(
      block,
      /#33c3bb|#f2607e/i,
      "teal means clear and rose means critical; neither may be a route background",
    );
  }
});

test("only one h1 per route, and it names the page not the product", async () => {
  const [shell, routeHeader] = await Promise.all([
    read(paths.shell),
    read("../app/components/route-header.tsx"),
  ]);

  assert.doesNotMatch(shell, /<h1>/, "the wordmark must not be an h1 on every route");
  assert.match(shell, /brand-name/, "the wordmark is a span, so the route owns the only h1");
  assert.match(routeHeader, /<h1>\{title\}<\/h1>/, "the route header supplies the page's h1");
});

// The saved-defaults key is shared between /settings (which owns it) and /new
// (which only seeds from it). /new writing it back silently dropped
// `default_analyzers`, because initialAnalysisSettings() allow-lists detector
// keys — so merely visiting /new erased the user's saved default checks.
test("per-run overrides never write back to saved defaults", async () => {
  const [controlPanel, settings, format] = await Promise.all([
    read(paths.controlPanel),
    read(paths.settings),
    read(paths.format),
  ]);

  assert.doesNotMatch(
    controlPanel,
    /localStorage\.setItem/,
    "/new holds per-run overrides and must never persist them over the saved defaults",
  );
  assert.match(settings, /localStorage\.setItem/, "/settings owns writing the saved defaults");
  assert.match(
    controlPanel,
    /storedDefaultAnalyzers\(\)/,
    "a saved default check selection must actually seed a new run, or the setting does nothing",
  );
  assert.match(format, /default_analyzers/, "the shared reader must know the key holds more than AnalysisSettings");
});

test("navigation never imports next/link", async () => {
  const sources = await Promise.all([
    ...ROUTES.map(([, path]) => read(path)),
    read(paths.shell),
    read(paths.rail),
    read(paths.controlPanel),
  ]);

  for (const source of sources) {
    assert.doesNotMatch(
      source,
      /from "next\/link"/,
      "next/link resolves a second React copy under vinext and throws on render; use components/nav-link",
    );
  }
});
