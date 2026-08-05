"use client";

import { useCallback, useRef } from "react";
import { FLIGHT_POSTER, FLIGHT_STOPS, FLIGHT_VIDEO, stopStill, type FlightStop } from "../lib/flight";
import { bandOffset, useFlightSteps, useMediaQuery } from "../lib/use-flight-steps";

/**
 * The pinned fly-through.
 *
 * One scroll, one test. Each stop owns a viewport-tall band of the section and
 * a leg of the pre-rendered camera move (tools/flight/render_flight.py);
 * arriving at a band plays that leg once and parks on its settled frame, so the
 * film runs at the speed it was shot rather than at the speed of the wheel.
 *
 * The overlays that are locked to a place on the sheet are baked into the clip
 * because they have to move with the camera; the prose lives here as HTML so it
 * stays selectable and reaches a screen reader.
 */

function CheckRow({ label, value, status }: FlightStop["checks"][number]) {
  return (
    <li className={`flight-check is-${status}`}>
      <span className="flight-check-dot" aria-hidden="true" />
      <span className="flight-check-label">{label}</span>
      <span className="flight-check-value">{value}</span>
    </li>
  );
}

function StopCopy({ stop, active }: { stop: FlightStop; active: boolean }) {
  return (
    <article className="flight-copy" data-active={active} aria-hidden={!active} data-tone={stop.tone}>
      <p className="flight-eyebrow">{stop.eyebrow}</p>
      <h3>{stop.title}</h3>
      <p className="flight-body">{stop.body}</p>
      {stop.checks.length > 0 && (
        <ul className="flight-checks">
          {stop.checks.map((check) => (
            <CheckRow key={check.label} {...check} />
          ))}
        </ul>
      )}
    </article>
  );
}

/** Mobile and reduced-motion both get the same thing: the stops as discrete
 *  cards. Scrubbing a 1080p clip is the wrong ask for a phone decoder, and
 *  swiping through the modules communicates the identical idea. */
function FlightCarousel() {
  return (
    <div className="flight-carousel">
      <ol>
        {FLIGHT_STOPS.map((stop) => (
          <li key={stop.id} data-tone={stop.tone}>
            <figure>
              <img src={stopStill(stop.id)} alt="" loading="lazy" decoding="async" />
            </figure>
            <div className="flight-carousel-copy">
              <p className="flight-eyebrow">{stop.eyebrow}</p>
              <h3>{stop.title}</h3>
              <p className="flight-body">{stop.body}</p>
              {stop.checks.length > 0 && (
                <ul className="flight-checks">
                  {stop.checks.map((check) => (
                    <CheckRow key={check.label} {...check} />
                  ))}
                </ul>
              )}
            </div>
          </li>
        ))}
      </ol>
      <p className="flight-carousel-hint">Swipe to move through the modules →</p>
    </div>
  );
}

export default function FlightShowcase() {
  const sectionRef = useRef<HTMLElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  // A coarse pointer is the real signal, not width alone: a narrow desktop
  // window still scrubs fine, a large phone does not.
  const compact = useMediaQuery("(max-width: 900px), (pointer: coarse)");
  const still = useMediaQuery("(prefers-reduced-motion: reduce)");
  const degraded = compact || still;

  const { index, ready, near } = useFlightSteps({ sectionRef, videoRef, disabled: degraded });

  // The rail jumps to the top of a stop's band -- the same place a scroll step
  // lands -- so picking a module by name runs that test from its fly-in.
  const jumpTo = useCallback((stopIndex: number) => {
    const section = sectionRef.current;
    if (!section) return;
    window.scrollTo({ top: section.offsetTop + bandOffset(stopIndex), behavior: "smooth" });
  }, []);

  if (degraded) {
    return (
      <section className="flight flight-degraded" id="flight" aria-label="Document verification flight">
        <header className="flight-intro">
          <p className="pen-eyebrow">The flight</p>
          <h2 className="display">Watch it read a sheet.</h2>
        </header>
        <FlightCarousel />
      </section>
    );
  }

  return (
    <section
      className="flight"
      id="flight"
      ref={sectionRef}
      aria-label="Document verification flight"
      style={{ height: `${(FLIGHT_STOPS.length + 1) * 100}vh` }}
    >
      {/* One snap target per test. They carry no content -- they exist so a
          wheel gesture settles on a test instead of between two of them. */}
      {FLIGHT_STOPS.map((stop, i) => (
        <span key={stop.id} className="flight-band" style={{ top: `${i * 100}vh` }} aria-hidden="true" />
      ))}

      <div className="flight-stage">
        <div className="flight-frame" data-ready={ready}>
          <img className="flight-poster" src={FLIGHT_POSTER} alt="" aria-hidden="true" />
          <video
            ref={videoRef}
            className="flight-video"
            // No source until the section is within a screen of the viewport.
            // The clip is 13 MB; nothing above it should wait on that.
            src={near ? FLIGHT_VIDEO : undefined}
            poster={FLIGHT_POSTER}
            muted
            playsInline
            preload="auto"
            aria-label="A camera moves across a candidate verification sheet, stopping on each region a detector inspects."
          />
        </div>

        <nav className="flight-rail" aria-label="Verification modules">
          <ol>
            {FLIGHT_STOPS.map((stop, i) => (
              <li key={stop.id}>
                <button
                  type="button"
                  data-active={i === index}
                  data-tone={stop.tone}
                  aria-current={i === index ? "step" : undefined}
                  onClick={() => jumpTo(i)}
                >
                  <span className="flight-rail-tick" aria-hidden="true" />
                  <span className="flight-rail-label">{stop.label ?? stop.eyebrow}</span>
                </button>
              </li>
            ))}
          </ol>
        </nav>

        <div className="flight-panel">
          {FLIGHT_STOPS.map((stop, i) => (
            <StopCopy key={stop.id} stop={stop} active={i === index} />
          ))}
        </div>

        <p className="flight-scroll-hint" data-hidden={index > 0}>
          Scroll — one test at a time
        </p>
      </div>
    </section>
  );
}

