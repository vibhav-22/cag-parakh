import stopsConfig from "../flight-stops.json";
import timelineConfig from "../flight-timeline.json";

/**
 * Outcome of a single detector check, mirroring the statuses backend/checks.py
 * can return. `info` is metadata's own category — reported, never scored — and
 * `inconclusive` is a test that could not run, which the product is careful
 * never to present as a pass.
 */
export type CheckStatus = "pass" | "warn" | "fail" | "info" | "inconclusive";

/** How a stop is coloured, following the workspace's semantic palette: green
 *  cleared, red finding, amber could-not-run, grey reported-only, blue for
 *  narration. Green is deliberately split from the blue accent so a passed
 *  check never wears the action colour and read as a flag. */
export type StopTone = "neutral" | "clear" | "flag" | "info" | "inconclusive";

export type StopCheck = {
  label: string;
  value: string;
  status: CheckStatus;
};

/** A page-space rectangle, in the same 794x1123 coordinate system the renderer
 *  draws in. Present for informational parity with the video; the page itself
 *  never has to do the arithmetic. */
export type PageRect = { x: number; y: number; w: number; h: number };

export type FlightStop = {
  id: string;
  weight: number;
  tone: StopTone;
  module: string | null;
  rect: PageRect | null;
  frame?: PageRect;
  /** Short name for the rail. Falls back to `eyebrow`, which is already the
   *  detector's name for every module stop; only the narration stops need it. */
  label?: string;
  eyebrow: string;
  title: string;
  body: string;
  checks: StopCheck[];
};

/** Normalised (0..1) schedule for one stop, emitted by the renderer.
 *  `settle` is when the camera arrives, `leave` when it starts moving on. */
export type FlightMark = {
  id: string;
  enter: number;
  settle: number;
  leave: number;
};

export const FLIGHT_STOPS = stopsConfig.stops as FlightStop[];
export const FLIGHT_MARKS = timelineConfig.marks as FlightMark[];
export const FLIGHT_DURATION = timelineConfig.duration as number;

export const FLIGHT_VIDEO = "/flight/verification-flight.mp4";
export const FLIGHT_POSTER = "/flight/poster.jpg";

/** Still frame for a stop, used as the mobile carousel slide. */
export function stopStill(id: string): string {
  return `/flight/stops/${id}.jpg`;
}

/**
 * The leg of the film a stop owns, in seconds.
 *
 * `enter` -> `leave` is the whole move for one test: the camera travelling to
 * the region, settling on it, and holding there. Playing exactly this window
 * and stopping is what makes one scroll equal one test, and it is why the film
 * and the HTML copy never disagree — the caption burned into the frame belongs
 * to the same stop the panel is showing, because they are one window.
 */
export function stopSegment(index: number, duration: number): { from: number; to: number } {
  const mark = FLIGHT_MARKS[index] ?? FLIGHT_MARKS[0];
  return { from: mark.enter * duration, to: mark.leave * duration };
}
