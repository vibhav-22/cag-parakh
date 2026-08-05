import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

/**
 * The landing page advertises the detectors by name, which makes it a claim
 * about the product rather than decoration. These tests hold that claim to the
 * backend: every registered evaluator has to appear in the flight, and the
 * flight is not allowed to invent one that does not exist.
 *
 * Without this, adding a detector to checks.py silently leaves the front page
 * understating the product, and removing one silently leaves it lying.
 */

const read = (relative) => readFile(new URL(relative, import.meta.url), "utf8");

const stops = async () => JSON.parse(await read("../app/welcome/flight-stops.json"));

async function backendEvaluators() {
  const source = await read("../../backend/checks.py");
  const block = source.match(/EVALUATORS[^{]*\{([\s\S]*?)\n\}/);
  assert.ok(block, "could not find the EVALUATORS registry in backend/checks.py");
  return new Set([...block[1].matchAll(/"([a-z_]+)":/g)].map((m) => m[1]));
}

test("the flight shows every detector the backend actually registers", async () => {
  const evaluators = await backendEvaluators();
  const config = await stops();
  const shown = new Set(config.stops.map((stop) => stop.module).filter(Boolean));

  const missing = [...evaluators].filter((id) => !shown.has(id)).sort();
  assert.deepEqual(missing, [], `detectors that ship but are missing from the flight: ${missing}`);
});

test("the flight never advertises a detector that does not exist", async () => {
  const evaluators = await backendEvaluators();
  const config = await stops();
  const shown = [...new Set(config.stops.map((stop) => stop.module).filter(Boolean))];

  const invented = shown.filter((id) => !evaluators.has(id)).sort();
  assert.deepEqual(invented, [], `detectors on the landing page with no evaluator: ${invented}`);
});

test("metadata is shown as reported, never as a pass or a fail", async () => {
  const config = await stops();
  const metadata = config.stops.find((stop) => stop.module === "metadata");
  assert.ok(metadata, "the metadata detector should appear in the flight");

  // checks.py returns INFO for metadata unconditionally and excludes it from
  // every pass/fail total; a tick or a cross here would misstate the product.
  assert.equal(metadata.tone, "info");
  for (const check of metadata.checks) {
    assert.equal(
      check.status,
      "info",
      "a metadata fact must not be presented with a pass or fail marker",
    );
  }
});

test("a test that could not run is never dressed up as a pass", async () => {
  const config = await stops();
  const samePhone = config.stops.find((stop) => stop.module === "same_phone");
  assert.ok(samePhone, "the same-phone detector should appear in the flight");

  // The specimen is a single page and same_phone needs at least two, so the
  // honest state is inconclusive with the reason attached.
  assert.equal(samePhone.tone, "inconclusive");
  assert.match(samePhone.body, /single page|one page|nothing to compare/i);
});

test("every stop carries a tone the renderer and the stylesheet both know", async () => {
  const config = await stops();
  const known = new Set(["neutral", "clear", "flag", "info", "inconclusive"]);
  const statuses = new Set(["pass", "warn", "fail", "info", "inconclusive"]);

  for (const stop of config.stops) {
    assert.ok(known.has(stop.tone), `stop ${stop.id} has an unknown tone: ${stop.tone}`);
    for (const check of stop.checks) {
      assert.ok(
        statuses.has(check.status),
        `stop ${stop.id} has an unknown check status: ${check.status}`,
      );
    }
  }
});

test("the baked timeline still covers every stop in the flight", async () => {
  const config = await stops();
  const timeline = JSON.parse(await read("../app/welcome/flight-timeline.json"));

  // The video is rendered from this config; if someone edits the stops without
  // re-rendering, the copy would land on the wrong frames.
  assert.equal(
    timeline.marks.length,
    config.stops.length,
    "flight-timeline.json is stale — re-run tools/flight/render_flight.py",
  );
  assert.deepEqual(
    timeline.marks.map((mark) => mark.id),
    config.stops.map((stop) => stop.id),
    "flight-timeline.json is stale — re-run tools/flight/render_flight.py",
  );
  assert.equal(timeline.duration, config.video.duration);
});

test("every stop is reachable by scroll, in order", async () => {
  const timeline = JSON.parse(await read("../app/welcome/flight-timeline.json"));
  const { marks } = timeline;

  // app/welcome/lib/flight.ts picks the active stop with "the last mark whose
  // handover point is behind us", where handover is the midpoint of the travel
  // into that stop -- the same instant the renderer switches its baked caption.
  // That only works if the marks march forward and each stop's settled moment
  // falls inside its own window; otherwise a stop exists in the film but no
  // scroll position ever selects its copy.
  const handover = (mark) => (mark.enter + mark.settle) / 2;
  let previous = -1;
  marks.forEach((mark, i) => {
    assert.ok(
      handover(mark) > previous,
      `stop ${mark.id} does not take over after the one before it`,
    );
    assert.ok(
      mark.enter <= mark.settle && mark.settle < mark.leave,
      `stop ${mark.id} has an incoherent window`,
    );
    const settled = (mark.settle + mark.leave) / 2;
    const next = marks[i + 1];
    assert.ok(
      settled >= handover(mark) && (!next || settled < handover(next)),
      `stop ${mark.id} settles outside its own ownership window, so its copy would never show`,
    );
    previous = handover(mark);
  });

  assert.equal(handover(marks[0]), 0, "the first stop must own the top of the scroll");
  assert.ok(
    marks[marks.length - 1].leave > 0.97,
    "the last stop must hold to the bottom of the scroll",
  );
});
