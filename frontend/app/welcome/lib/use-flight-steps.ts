"use client";

import { RefObject, useEffect, useRef, useState } from "react";
import { FLIGHT_DURATION, FLIGHT_MARKS, stopSegment } from "./flight";

type StepOptions = {
  /** The tall section that owns the scroll distance. */
  sectionRef: RefObject<HTMLElement | null>;
  /** The pinned <video> the steps drive. The caller owns the `src`: the hook
   *  reports when the section is near and the element attaches it then. */
  videoRef: RefObject<HTMLVideoElement | null>;
  /** Skip everything and leave the poster up. */
  disabled?: boolean;
};

type StepState = {
  /** Index of the test the copy should be showing. */
  index: number;
  /** True once the clip is decodable and has painted a frame. */
  ready: boolean;
  /** True while the section is on screen; off screen the clip is paused. */
  visible: boolean;
  /** True once the section is close enough that the clip should be fetched. */
  near: boolean;
};

/** One viewport of scroll per test. The section is a band taller than the stop
 *  count so the last test still has somewhere to sit before the stage unpins. */
export function bandOffset(index: number): number {
  return index * window.innerHeight;
}

/**
 * Steps a pre-rendered flight one test per scroll.
 *
 * The clip used to be scrubbed: scroll drove currentTime frame by frame, which
 * meant the whole 13 MB file had to be in memory as a Blob before anything
 * moved, and every wheel notch turned into a seek the decoder had to service.
 * That is what made the page slow to reach and the motion feel like dragging.
 *
 * Now scroll only picks a test. Each stop owns a leg of the timeline
 * (`enter` -> `leave`), and arriving at a stop plays that leg once and parks on
 * its settled frame. Two things fall out of that:
 *
 * 1. Moving forward needs no seek at all. A leg is parked exactly on the next
 *    leg's `enter`, so the next test just resumes playback — the camera flies
 *    on, continuously, the way the film was rendered.
 * 2. Nothing is prefetched. The clip is faststart H.264 with an 8-frame GOP and
 *    the host answers byte ranges, so the element streams it and the page is
 *    interactive long before the film is.
 *
 * A backward step or a jump from the rail is the only case that seeks, and it
 * replays that test's fly-in rather than cutting to it.
 */
export function useFlightSteps({ sectionRef, videoRef, disabled }: StepOptions): StepState {
  // `near` lives in the same object so the caller can attach the source
  // declaratively: a visitor who never scrolls this far never pays for the film.
  const [state, setState] = useState<StepState>({
    index: 0,
    ready: false,
    visible: false,
    near: false,
  });

  const last = useRef({ index: 0, visible: false, near: false });

  // --- scroll: which test are we on, and are we looking at it? --------------
  useEffect(() => {
    if (disabled) return;

    let frame = 0;

    function read() {
      const el = sectionRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const band = window.innerHeight;

      // Rounding, not flooring: the test hands over at the midpoint of a band,
      // so dragging the scrollbar tracks the copy instead of lagging a whole
      // viewport behind it. Scroll snapping lands on the boundaries anyway.
      const index = Math.min(
        FLIGHT_MARKS.length - 1,
        Math.max(0, Math.round(-rect.top / band)),
      );
      const visible = rect.top < band && rect.bottom > 0;
      // A screen of runway before the section arrives: enough for the opening
      // seconds to be buffered by the time the stage pins, late enough that
      // nothing above the fold waits on a 13 MB file. Once true it stays true --
      // the clip is not thrown away for scrolling past it.
      const near = last.current.near || (rect.top < band * 2 && rect.bottom > -band);

      const previous = last.current;
      if (index === previous.index && visible === previous.visible && near === previous.near) return;
      last.current = { index, visible, near };
      setState((prev) => ({ ...prev, index, visible, near }));
    }

    function onScroll() {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        read();
      });
    }

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    read();

    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [disabled, sectionRef]);

  // --- play the leg that belongs to the current test -----------------------
  useEffect(() => {
    if (disabled) return;
    const video = videoRef.current;
    if (!video || !state.ready) return;

    if (!state.visible) {
      video.pause();
      return;
    }

    const duration = Number.isFinite(video.duration) && video.duration > 0
      ? video.duration
      : FLIGHT_DURATION;
    const segment = stopSegment(state.index, duration);
    const from = segment.from;
    // Never run to the very last frame: some decoders trip the 'ended' state
    // and blank the element.
    const to = Math.min(segment.to, duration - 0.04);

    // Already somewhere inside this leg -- which is the forward case, since the
    // previous leg parked on this one's first frame. Let it run on rather than
    // seeking, so the camera move stays continuous.
    if (video.currentTime < from - 0.3 || video.currentTime >= to) {
      video.currentTime = from;
    }

    let raf = 0;

    /** Park on the last frame of this test rather than running on into the next
     *  one's fly-in. */
    function park(): boolean {
      const el = videoRef.current;
      if (!el || el.currentTime < to) return false;
      el.pause();
      // A backgrounded tab only gets `timeupdate`, which is coarse, so it can
      // overshoot the mark by a fraction of a second. Snap back to it.
      if (el.currentTime > to + 0.05) el.currentTime = to;
      return true;
    }

    /** rAF lands within a frame of the mark. */
    function watch() {
      raf = 0;
      if (park()) return;
      raf = requestAnimationFrame(watch);
    }

    function run() {
      const el = videoRef.current;
      if (!el || el.currentTime >= to) return;
      void el.play().catch(() => {
        // Autoplay refused. The frame we seeked to is still the right one to
        // sit on, so the stage reads as a still rather than as a broken player.
      });
      if (!raf) raf = requestAnimationFrame(watch);
    }

    function onVisibility() {
      if (document.hidden) videoRef.current?.pause();
      else run();
    }

    // Backstop for a backgrounded tab, where rAF is suspended but the decoder
    // keeps running.
    video.addEventListener("timeupdate", park);
    document.addEventListener("visibilitychange", onVisibility);
    run();

    return () => {
      if (raf) cancelAnimationFrame(raf);
      video.removeEventListener("timeupdate", park);
      document.removeEventListener("visibilitychange", onVisibility);
      video.pause();
    };
  }, [disabled, state.index, state.ready, state.visible, videoRef]);

  // --- readiness -----------------------------------------------------------
  useEffect(() => {
    if (disabled) return;
    const video = videoRef.current;
    if (!video) return;
    const onLoaded = () => setState((prev) => (prev.ready ? prev : { ...prev, ready: true }));
    if (video.readyState >= 2) onLoaded();
    video.addEventListener("loadeddata", onLoaded);
    return () => video.removeEventListener("loadeddata", onLoaded);
  }, [disabled, state.near, videoRef]);

  return state;
}

/** Matches a media query, SSR-safe. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);
  useEffect(() => {
    const list = window.matchMedia(query);
    const sync = () => setMatches(list.matches);
    sync();
    list.addEventListener("change", sync);
    return () => list.removeEventListener("change", sync);
  }, [query]);
  return matches;
}

