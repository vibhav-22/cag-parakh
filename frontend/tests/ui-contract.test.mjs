import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (relative) => readFile(new URL(relative, import.meta.url), "utf8");

const paths = {
  home: "../app/page.tsx",
  css: "../app/globals.css",
  shellCss: "../app/styles/shell.css",
  caseCss: "../app/styles/case.css",
  penCss: "../app/styles/pen-design.css",
  advancedSettings: "../app/advanced-settings.tsx",
  controlPanel: "../app/components/control-panel.tsx",
  shell: "../app/components/app-shell.tsx",
  rail: "../app/components/nav-rail.tsx",
  session: "../app/lib/session.tsx",
  format: "../app/lib/format.ts",
  profiles: "../app/lib/profiles.ts",
  useBatch: "../app/lib/use-batch.ts",
  newRoute: "../app/new/page.tsx",
  ask: "../app/ask/page.tsx",
  history: "../app/history/page.tsx",
  reports: "../app/reports/page.tsx",
  settings: "../app/settings/page.tsx",
  batch: "../app/batches/[batchId]/page.tsx",
  document: "../app/batches/[batchId]/documents/[jobId]/page.tsx",
};

const ROUTES = [
  ["home", paths.home],
  ["new", paths.newRoute],
  ["ask", paths.ask],
  ["history", paths.history],
  ["reports", paths.reports],
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
  assert.match(home, /dashboard-state/, "the dashboard must name its loading, empty, and error states");
  assert.match(home, /Recent screenings/, "the landing route must present the operational dashboard");
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

test("shows every extracted document photo inside its result", async () => {
  const [format, document, caseCss] = await Promise.all([
    read(paths.format),
    read(paths.document),
    read(paths.caseCss),
  ]);

  assert.match(format, /Document Photo/);
  assert.match(await read(paths.advancedSettings), /photo_detection/);
  assert.match(document, /detectedPhotos/);
  assert.match(document, /detected-photos/);
  assert.match(document, /Photo \$\{photo\.index\} detected in/);
  assert.match(format, /detected_photo/);
  assert.match(format, /Open extracted photo/);
  assert.match(caseCss, /\.app\[data-route="document"\] \.detected-photos/);
  assert.match(caseCss, /\.app\[data-route="document"\] \.detected-photo/);
});

test("keeps pass explanations and visual presence evidence auditable", async () => {
  const document = await read(paths.document);

  assert.match(
    document,
    /const evidenceAnalyzers[\s\S]*return job\.analyzers/,
    "passing checks must stay in the evidence rail with their reason",
  );
  assert.match(document, /check\?\.reason \|\| result\.summary/);
  assert.match(document, /locatedEvidenceImages/);
  assert.match(document, /QR Presence image evidence|analyzerLabel\(name\).*image evidence/s);
  assert.match(document, /name === "qr_presence" \|\| name === "signature"/);
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
  assert.match(newRoute, /Upload.*Configure.*Process.*Review/s, "the intake route must expose the complete screening journey");
});

test("each check owns its pass criterion; nothing in the UI configures ticks", async () => {
  const [profiles, controlPanel, batch, document, format] = await Promise.all([
    read(paths.profiles),
    read(paths.controlPanel),
    read(paths.batch),
    read(paths.document),
    read(paths.format),
  ]);

  // Whether a missing signature is a pass belongs to the signature detector, not
  // to a batch, so no screen may offer a tick/cross mapping control.
  for (const source of [profiles, controlPanel, batch, document, format]) {
    assert.doesNotMatch(source, /result_rules/);
  }
  assert.doesNotMatch(controlPanel, /Tick\/cross mapping/);
  assert.doesNotMatch(format, /configuredTestVerdict/);

  // Five result states, so "could not run" and "reports facts" never collapse
  // into a cross.
  assert.match(format, /CHECK_STATES/);
  for (const state of ["pass", "fail", "inconclusive", "info", "error"]) {
    assert.match(format, new RegExp(`\\b${state}: \\{ glyph`));
  }
  assert.match(controlPanel, /analyzer\.criterion/, "the setup screen must state each check's rule");
  assert.match(batch, /checkCriteria\[analyzer\]/);
  assert.match(batch, /Criterion not met/);
  assert.match(document, /document-compact-header/);
  assert.match(document, /document-workspace-toolbar/);
  assert.match(document, /evidenceAnalyzers\.map/);
  assert.match(document, /Document checks/);
  assert.match(document, /Check criteria/);
  assert.match(document, /criteria-reason/, "an inconclusive check must be able to say why it could not run");
  assert.match(document, /criteria-facts/, "detector values such as font names must be shown");
  assert.match(document, /item\.dpi/);
});

test("the document viewer keeps one consistent inset spacing system", async () => {
  const [document, penCss] = await Promise.all([
    read(paths.document),
    read(paths.penCss),
  ]);

  assert.match(document, /<details className="prior-screening">/, "duplicate history should stay compact until requested");
  assert.match(penCss, /\.case-grid\s*\{[^}]*gap: 16px;[^}]*padding: 16px;/s);
  assert.match(penCss, /\.evidence-column\s*\{[^}]*gap: 16px;/s);
  assert.match(penCss, /\.document-viewer\s*\{[^}]*border: 1px solid[^}]*border-radius:/s);
  assert.match(penCss, /\.document-compact-header\s*\{[^}]*position: sticky;/s);
  assert.match(penCss, /\.document-workspace-toolbar\s*\{[^}]*position: sticky;/s);
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
  assert.match(document, /document-back/, "a document two levels deep needs a route back to its batch");
  assert.match(document, /previousDocument.*nextDocument/s, "the viewer needs previous/next across every document");
  assert.match(document, /aria-label="Move between PDFs"/, "the PDF sequence controls must be discoverable");
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
  assert.match(home, /chrome="rail"/, "the dashboard must retain the primary navigation rail");

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

// The journey has to end somewhere. Before these, review stopped at "look at the
// evidence": there was no way to get a decision and its evidence out of the app,
// which made every step before it unusable as a case record.
test("a reviewed case can leave the app", async () => {
  const [document, batch] = await Promise.all([read(paths.document), read(paths.batch)]);

  assert.match(document, /report\.html/, "a document must export a printable case report");
  assert.match(document, /report\.json/, "a document must export a machine-readable record");
  assert.match(batch, /batches\/\$\{batch\.id\}\/report\.csv/, "a batch must export one row per document");
  assert.match(batch, /batches\/\$\{batch\.id\}\/report\.html/, "a batch must export a printable summary");
  assert.match(document, /No decision is recorded yet/, "an export without a decision must say so");
});

test("the decision records a handoff as well as a finding", async () => {
  const [format, document] = await Promise.all([read(paths.format), read(paths.document)]);

  assert.match(format, /escalated/, "escalation must be a decision, not a note");
  assert.match(document, /assigned_to/, "an escalation must name the second reviewer");
  assert.match(
    document,
    /does not notify\s+anyone/,
    "with no accounts in this build, the UI must not imply an escalation reaches anyone",
  );
});

// Efficacy is unmeasured against known-forged documents. A reviewer who
// over-trusts a flag and rejects a genuine certificate is the worst outcome this
// product can produce, so the limits are stated where the decision is made.
test("states what the screening does not claim, next to the decision", async () => {
  const [document, calibration, reporting] = await Promise.all([
    read(paths.document),
    read("../app/lib/calibration.ts"),
    read("../../backend/reporting.py"),
  ]);

  assert.match(document, /calibration-note/, "the disclosure must sit in the decision card");
  assert.match(calibration, /true-positive rate of this system is unknown/);
  assert.match(reporting, /true-positive rate of this system is unknown/,
    "the exported report must carry the same notice as the screen");
});

test("intake checks for duplicates and unreadable files before screening", async () => {
  const [controlPanel, document] = await Promise.all([read(paths.controlPanel), read(paths.document)]);

  assert.match(controlPanel, /documents\/preflight/, "intake must pre-flight the selected files");
  assert.match(controlPanel, /screened \{item\.prior_screenings\.length\}/, "a resubmitted file must be named at intake");
  assert.match(controlPanel, /left out of this batch/, "a file that cannot be screened must not be queued");
  assert.match(
    document,
    /not mean anyone else screened this document/,
    "the digest identifies a file, never a document — the copy must not overclaim",
  );
});

test("a check that crashed has a way out", async () => {
  const [document, format] = await Promise.all([read(paths.document), read(paths.format)]);

  assert.match(format, /failedAnalyzers/, "failed runs must be distinguishable from findings");
  assert.match(document, /partial-failure/, "a partly-screened document must say so");
  assert.match(document, /rerun/, "a crashed check must be retryable");
  assert.match(document, /unanalyzable/, "a file that will never screen must be able to leave the queue");
});

test("the batch hands over a queue, not just a matrix", async () => {
  const batch = await read(paths.batch);

  assert.match(batch, /Review flagged queue/, "a reviewer must be handed the next document to work");
  assert.match(batch, /Notification/, "a long batch must be able to announce that it finished");
  assert.match(batch, /saveRunAsDefaults/, "a run that worked must be promotable to the saved defaults");
});

test("reports turn stored batches into filterable operational analytics", async () => {
  const [reports, rail] = await Promise.all([read(paths.reports), read(paths.rail)]);

  assert.match(rail, /href: "\/reports"/, "analytics must be reachable from primary navigation");
  assert.match(reports, /api\/v1\/batches/, "reports must use stored screening data");
  assert.match(reports, /Reporting period/, "the date range must be adjustable");
  assert.match(reports, /Screening Volume/);
  assert.match(reports, /Flag Reasons/);
  assert.match(reports, /Performance by File Type/);
  assert.match(reports, /Export report/, "the operational summary must be exportable");
});

test("visual Q&A is scoped to the document in front of the reviewer", async () => {
  const [askPanel, document] = await Promise.all([
    read("../app/components/ask-document-panel.tsx"),
    read(paths.document),
  ]);

  assert.match(document, /AskDocumentPanel/, "the workspace must be able to question its own document");
  assert.match(askPanel, /jobs\/\$\{jobId\}\/questions/, "questions must go to this job's document");
  assert.match(askPanel, /carries no weight in the\s+verdict/,
    "a model reading must never be presented as a screening result");
});

test("triage works from the keyboard", async () => {
  const document = await read(paths.document);

  assert.match(document, /case "j"/, "j must step through the marked evidence");
  assert.match(document, /case "n"/, "n must move to the next PDF");
  assert.match(document, /Next \/ previous PDF in this batch/);
  assert.match(document, /INPUT\|TEXTAREA\|SELECT/, "shortcuts must not fire while typing in a field");
  assert.match(document, /key-hints/, "the shortcuts must be discoverable");
});

test("an empty dashboard offers a real verdict to look at", async () => {
  const [home, sample] = await Promise.all([read(paths.home), read("../../backend/sample_case.py")]);

  assert.match(home, /first-run/, "a dashboard with no cases must not be a dead end");
  assert.match(home, /samples\/case/, "the sample must be screened, not mocked up");
  assert.match(sample, /SAMPLE/, "a generated document must never pass for a genuine one");
});

// The flowchart's "Create screening profile → name test + define goal +
// decision rules" branch. A profile is a named, reusable test that carries its
// goal and decision rule to the run and its exports.
test("a screening test can be created, used, and reused", async () => {
  const [profiles, controlPanel, settings] = await Promise.all([
    read("../app/lib/profiles.ts"),
    read(paths.controlPanel),
    read(paths.settings),
  ]);

  assert.match(profiles, /PROFILES_STORAGE_KEY = "parakh-screening-profiles"/, "profiles need their own storage key");
  assert.doesNotMatch(profiles, /setItem\(\s*"parakh-analysis-settings"|getItem\(\s*"parakh-analysis-settings"/, "profiles must not read or write the saved-defaults key");
  assert.match(controlPanel, /applyProfile/, "/new must be able to use a saved test");
  assert.match(controlPanel, /Create a test from this setup|Save current setup as a new test/, "/new must be able to create a test");
  assert.match(controlPanel, /Name the test/, "creating a test must name it");
  assert.match(controlPanel, /Goal/, "a test must define a goal");
  assert.match(controlPanel, /Decision rule/, "a test must define a decision rule");
  assert.match(settings, /Screening tests/, "/settings must manage the test library");
});

test("the screening test rides the run into the workspace and the report", async () => {
  const [controlPanel, document, reporting] = await Promise.all([
    read(paths.controlPanel),
    read(paths.document),
    read("../../backend/reporting.py"),
  ]);

  assert.match(controlPanel, /body\.append\("profile"/, "the run must be stamped with the test it was started under");
  assert.match(document, /decision-rule/, "the workspace must show the test's decision rule at the point of decision");
  assert.match(document, /job\.profile/, "the decision rule comes from the run's stamped profile");
  assert.match(reporting, /Screening test/, "the exported report must name the test");
  assert.match(reporting, /Decision rule/, "the exported report must carry the decision rule");
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
