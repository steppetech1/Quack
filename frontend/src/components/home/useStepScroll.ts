"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

/** Once the scroll has been still this long, the nearest step is brought fully into place. */
const SNAP_IDLE_MS = 140;
/** A nudge smaller than this share of a step settles back on the same step. */
const SNAP_SLACK = 0.12;
/** The wheel is held for at least this long after it turns a step, and until it has been quiet for WHEEL_QUIET_MS. */
const WHEEL_LOCK_MS = 650;
const WHEEL_QUIET_MS = 200;

/**
 * Turns a tall section with a sticky stage into `count` discrete steps.
 *
 * While the stage is pinned, one flick of the wheel moves exactly one step and
 * the page is carried there by script; touch and scrollbar scrolling settle on
 * the nearest step once they stop.
 *
 * The drawn position does not trace the scroll. It chases the current step on a
 * critically damped spring, in real time, so every move starts and ends softly
 * however unevenly the page itself scrolls, and a new flick mid-move carries on
 * without a jolt. `onFrame` receives that position (0 to count − 1) on every
 * frame it changes; callers write to the DOM directly and scrolling never
 * re-renders React — only the step index is state.
 */
export function useStepScroll(
  sectionRef: RefObject<HTMLElement | null>,
  count: number,
  onFrame: (pos: number) => void,
  /** Stiffness of the spring, in radians per second: higher settles sooner. */
  omega = 4.2
) {
  const [active, setActive] = useState(0);
  const frameCb = useRef(onFrame);
  frameCb.current = onFrame;

  useEffect(() => {
    const section = sectionRef.current;
    if (!section) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const last = count - 1;

    let frame = 0;
    let target = 0;
    let pos = 0;
    let vel = 0;
    let then = 0;
    let shown = -1;
    // Set while a wheel flick is carrying the page, so the half-way scroll does not undo its target.
    let wheelLocked = false;

    const tick = (now: number) => {
      const dt = then ? Math.min(0.05, (now - then) / 1000) : 1 / 60;
      then = now;
      if (reduced) {
        pos = target;
        vel = 0;
      } else {
        vel += (omega * omega * (target - pos) - 2 * omega * vel) * dt;
        pos += vel * dt;
      }
      const done = Math.abs(target - pos) < 0.0005 && Math.abs(vel) < 0.002;
      if (done) {
        pos = target;
        vel = 0;
      }
      frameCb.current(pos);
      const idx = Math.round(pos);
      if (idx !== shown) {
        shown = idx;
        setActive(idx);
      }
      if (done) {
        frame = 0;
        then = 0;
      } else frame = requestAnimationFrame(tick);
    };

    const aim = (i: number) => {
      target = Math.max(0, Math.min(last, i));
      if (!frame) frame = requestAnimationFrame(tick);
    };

    const scrollFor = (i: number) => {
      const run = section.offsetHeight - window.innerHeight;
      return section.getBoundingClientRect().top + window.scrollY + (run * i) / last;
    };

    // After the scroll stops, finish the move onto a step — always the way the
    // reader was going, unless they barely left the step they were on.
    let idle = 0;
    let lastY = window.scrollY;
    let dir = 0;
    const snap = () => {
      const rect = section.getBoundingClientRect();
      const run = rect.height - window.innerHeight;
      const raw = run > 0 ? -rect.top / run : -1;
      if (raw <= 0 || raw >= 1) return;
      const at = raw * last;
      const i = dir > 0 ? Math.ceil(at - SNAP_SLACK) : dir < 0 ? Math.floor(at + SNAP_SLACK) : Math.round(at);
      const top = scrollFor(Math.max(0, Math.min(last, i)));
      if (Math.abs(top - window.scrollY) > 2) window.scrollTo({ top, behavior: reduced ? "auto" : "smooth" });
    };

    const onScroll = () => {
      const y = window.scrollY;
      if (y !== lastY) dir = Math.sign(y - lastY);
      lastY = y;
      const rect = section.getBoundingClientRect();
      const run = rect.height - window.innerHeight;
      const at = (run > 0 ? Math.max(0, Math.min(1, -rect.top / run)) : 0) * last;
      // Aim for the step the reader is heading to, with the same slack the snap uses.
      if (!wheelLocked) {
        aim(dir > 0 ? Math.ceil(at - SNAP_SLACK) : dir < 0 ? Math.floor(at + SNAP_SLACK) : Math.round(at));
      }
      clearTimeout(idle);
      idle = window.setTimeout(snap, SNAP_IDLE_MS);
    };

    onScroll();
    pos = target;
    frameCb.current(pos);
    cancelAnimationFrame(frame);
    frame = 0;

    // One step per flick: the rest of the flick (extra notches, trackpad inertia)
    // is swallowed until the wheel has been quiet for a moment. At the first and
    // last step the wheel is let through, so the section can be left.
    let lockUntil = 0;
    let quiet = 0;
    const unlock = () => {
      const wait = lockUntil - performance.now();
      if (wait > 0) quiet = window.setTimeout(unlock, wait);
      else wheelLocked = false;
    };
    const onWheel = (e: WheelEvent) => {
      const rect = section.getBoundingClientRect();
      const run = rect.height - window.innerHeight;
      if (run <= 0) return;
      const raw = -rect.top / run;
      const d = Math.sign(e.deltaY);
      if (!d || raw < -0.001 || raw > 1.001) return;
      const at = raw * last;
      const next = d > 0 ? Math.floor(at + 0.01) + 1 : Math.ceil(at - 0.01) - 1;
      if (next < 0 || next > last) return;

      e.preventDefault();
      clearTimeout(quiet);
      quiet = window.setTimeout(unlock, WHEEL_QUIET_MS);
      if (wheelLocked) return;
      wheelLocked = true;
      lockUntil = performance.now() + WHEEL_LOCK_MS;
      dir = d;
      aim(next);
      window.scrollTo({ top: scrollFor(next), behavior: reduced ? "auto" : "smooth" });
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("wheel", onWheel, { passive: false });
    // A new viewport size changes the drawing even when the step stays the same.
    const onResize = () => {
      onScroll();
      frameCb.current(pos);
    };
    window.addEventListener("resize", onResize);
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(idle);
      clearTimeout(quiet);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("wheel", onWheel);
      window.removeEventListener("resize", onResize);
    };
  }, [sectionRef, count, omega]);

  /** Scrolls the page so that step `i` is the one on screen. */
  const jumpTo = useCallback(
    (i: number) => {
      const section = sectionRef.current;
      if (!section) return;
      const run = section.offsetHeight - window.innerHeight;
      const top = section.getBoundingClientRect().top + window.scrollY + (run * i) / (count - 1);
      window.scrollTo({ top, behavior: "smooth" });
    },
    [sectionRef, count]
  );

  return { active, jumpTo };
}
