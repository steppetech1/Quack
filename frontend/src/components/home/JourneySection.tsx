"use client";

import { useCallback, useEffect, useMemo, useRef, type CSSProperties, type ReactNode, type Ref } from "react";
import { PixelDuck } from "@/components/duck/PixelDuck";
import { copy } from "./copy";
import {
  BOAT,
  BUSH,
  FOG_PALETTE,
  HUT,
  JOURNEY_PALETTE,
  MAP,
  LAPTOP,
  MAST,
  PINE,
  TOWER,
  U,
  WAKE,
  WRECK,
  ARCH,
  FOG_PROPS,
  GULL,
  LAMP,
  LIGHTHOUSE,
  STACKS,
  fogIsle,
  glintMap,
  islandSlice,
  islandTop,
  signalArc,
} from "./journeyArt";
import styles from "./journey.module.css";
import { PixelSprite } from "./PixelSprite";
import { useStepScroll } from "./useStepScroll";

const STEPS = copy.journey.steps;
const N = STEPS.length;

/** Scroll distance spent on each step, in viewport heights. */
const PER_STEP_VH = 0.55;
/** How far the sea is tipped away from the viewer. */
const TILT = 38;
/** Figures standing on a plane seen this much from above look squat; they are stretched back by this factor. */
const STAND_LIFT = (1 / Math.sin((TILT * Math.PI) / 180)) * 0.9;
/** Camera zoom at each step. The lookout is tall, so the camera backs off for
    it, then closes in a little as the island turns round on the last step. */
const ZOOM = [1, 0.86, 0.64, 0.78];
/** How far above its footing the middle of each step's main figure is, in world
    pixels, so the figure — not its footing — lands in the middle of the screen. */
const LIFT = [36, 34, 80, 52];
/** Half the width of each step's main figure, in world pixels: the boat, then each island. The text stands just outside it. */
const HALF = [70, 125, 184, 184];

/* Heights above the sea plane, in world pixels. */
const WATER_Z = -10;
const THREAD_Z = -8;
const BOAT_Z = -6;
/** An island's bank is a stack of slices this far apart. */
const SLICES = 5;
const SLICE_Z = 3;

type Pt = { x: number; y: number };

/* The world, in unscaled pixels on the sea plane: x to the right, y towards
   the viewer. The route always runs right and down, so the camera following it
   always pulls the scene up and to the left. */
const START: Pt = { x: 0, y: 0 };
const ISLE_1: Pt = { x: 600, y: 520 };
const ISLE_2: Pt = { x: 1400, y: 1080 };
/** Where the boat is pulled up on each island's shore. */
const MOOR_1: Pt = { x: ISLE_1.x - 175, y: ISLE_1.y + 30 };
const MOOR_2: Pt = { x: ISLE_2.x - 240, y: ISLE_2.y + 40 };

/** Two legs of the route, each a quadratic curve; the second swings round the front of the first island. */
const LEGS: [Pt, Pt, Pt][] = [
  [START, { x: 360, y: 60 }, MOOR_1],
  [MOOR_1, { x: ISLE_1.x + 180, y: ISLE_1.y + 320 }, MOOR_2],
];

/** Where the camera looks, relative to the boat, at each step: the boat first, then each island's middle. */
const LOOK: Pt[] = [
  { x: 0, y: 0 },
  { x: ISLE_1.x - MOOR_1.x, y: ISLE_1.y - MOOR_1.y },
  { x: ISLE_2.x - MOOR_2.x, y: ISLE_2.y - MOOR_2.y },
  { x: ISLE_2.x - MOOR_2.x, y: ISLE_2.y - MOOR_2.y },
];

/** Thread: how far ahead of the duck it shows, how far behind a faint trace stays, and its sway in the wind. */
const THREAD_AHEAD = 340;
const THREAD_BEHIND = 360;
const SWAY = 5;
/** Bounds of the thread's drawing surface on the plane. */
const THREAD_BOX = { x: -400, y: -400, w: 2400, h: 2000 };

const ISLE_1_SIZE = { w: 60, h: 40 };
const ISLE_2_SIZE = { w: 92, h: 60 };

const clamp01 = (v: number) => Math.max(0, Math.min(1, v));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const smooth = (t: number) => t * t * (3 - 2 * t);

function bezier([a, c, b]: [Pt, Pt, Pt], t: number): Pt {
  const u = 1 - t;
  return { x: u * u * a.x + 2 * u * t * c.x + t * t * b.x, y: u * u * a.y + 2 * u * t * c.y + t * t * b.y };
}

type Measured = { pts: Pt[]; len: number[]; total: number };

/** A leg sampled finely, with the running length at each sample, so it can be walked at an even pace. */
function measure(leg: [Pt, Pt, Pt]): Measured {
  const pts: Pt[] = [];
  const len: number[] = [];
  let total = 0;
  for (let i = 0; i <= 240; i++) {
    const p = bezier(leg, i / 240);
    if (i > 0) total += Math.hypot(p.x - pts[i - 1].x, p.y - pts[i - 1].y);
    pts.push(p);
    len.push(total);
  }
  return { pts, len, total };
}

function pointAt(m: Measured, d: number): Pt {
  const target = Math.max(0, Math.min(m.total, d));
  let i = 1;
  while (i < m.len.length - 1 && m.len[i] < target) i++;
  const span = m.len[i] - m.len[i - 1] || 1;
  const t = (target - m.len[i - 1]) / span;
  return { x: lerp(m.pts[i - 1].x, m.pts[i].x, t), y: lerp(m.pts[i - 1].y, m.pts[i].y, t) };
}

const LEG_M = LEGS.map(measure);
const ROUTE = LEG_M[0].total + LEG_M[1].total;

/** A point at distance `d` along the whole route. */
function routeAt(d: number): Pt {
  return d <= LEG_M[0].total ? pointAt(LEG_M[0], d) : pointAt(LEG_M[1], d - LEG_M[0].total);
}

/** How far along the route the boat is at scroll position `pos`. */
function boatDistance(pos: number) {
  if (pos < 1) return LEG_M[0].total * clamp01(pos);
  return LEG_M[0].total + LEG_M[1].total * clamp01(pos - 1);
}

/** The thread from distance `a` to `b` as a path swaying about the route; it is held still at the duck's end. */
function threadPath(a: number, b: number, anchor: number, time: number) {
  if (b - a < 2) return "";
  let d = "";
  for (let s = a; ; s = Math.min(b, s + 6)) {
    const p = routeAt(s);
    const q = routeAt(Math.min(ROUTE, s + 1));
    const r = routeAt(Math.max(0, s - 1));
    const tx = q.x - r.x;
    const ty = q.y - r.y;
    const tl = Math.hypot(tx, ty) || 1;
    const hold = clamp01(Math.abs(s - anchor) / 90);
    const off = SWAY * hold * (Math.sin(time * 1.3 + s * 0.028) + 0.4 * Math.sin(time * 2.1 - s * 0.05));
    d += `${d ? "L" : "M"}${(p.x - (ty / tl) * off).toFixed(1)} ${(p.y + (tx / tl) * off).toFixed(1)}`;
    if (s >= b) break;
  }
  return d;
}

/** Copy with its *starred* words picked out in the accent colour. */
function Accented({ text }: { text: string }) {
  return (
    <>
      {/* A dash never starts a line: it stays with the word before it. */}
      {text.replace(/ —/g, " —").split("*").map((part, i) =>
        i % 2 ? (
          <em key={i} className={styles.accent}>
            {part}
          </em>
        ) : (
          part
        )
      )}
    </>
  );
}

/** Bottom-centre placement on the plane (or inside a group, relative to its centre). */
function place(x: number, bottom: number, w: number, h: number): CSSProperties {
  return { left: x - w / 2, top: bottom - h, width: w, height: h };
}

/** Something standing on the sea or an island, facing the viewer. */
function Stand({ map, x, y, unit = U, children }: { map: string[]; x: number; y: number; unit?: number; children?: ReactNode }) {
  const w = map[0].length * unit;
  const h = map.length * unit;
  return (
    <div className={styles.stand} style={place(x, y, w, h)}>
      <PixelSprite map={map} palette={JOURNEY_PALETTE} unit={unit} />
      {children}
    </div>
  );
}

/** A patch of sea: a soft pool of water that fades out on every side, with a few glints. */
function Water({ c, w, h, seed }: { c: Pt; w: number; h: number; seed: number }) {
  const glints = useMemo(() => [glintMap(w / U, h / U, seed), glintMap(w / U, h / U, seed + 1)], [w, h, seed]);
  return (
    <div className={styles.water} style={{ left: c.x - w / 2, top: c.y - h / 2, width: w, height: h, transform: `translateZ(${WATER_Z}px)` }}>
      <PixelSprite map={glints[0]} palette={JOURNEY_PALETTE} unit={U} className={styles.glintA} />
      <PixelSprite map={glints[1]} palette={JOURNEY_PALETTE} unit={U} className={styles.glintB} />
    </div>
  );
}

/** An island: a grassy top lying on the plane over a stack of bank slices. */
function IslandBody({ w, h, grass, seed }: { w: number; h: number; grass: number; seed: number }) {
  const maps = useMemo(
    () => ({
      top: islandTop(w, h, grass, seed),
      bank: islandSlice(w, h, "e"),
      foot: islandSlice(w, h, "E"),
    }),
    [w, h, grass, seed]
  );
  const box: CSSProperties = { left: (-w * U) / 2, top: (-h * U) / 2, width: w * U, height: h * U };
  return (
    <>
      {Array.from({ length: SLICES }, (_, i) => SLICES - i).map((k) => (
        <div key={k} className={styles.flat} style={{ ...box, transform: `translateZ(${-k * SLICE_Z}px)` }}>
          <PixelSprite map={k > 2 ? maps.foot : maps.bank} palette={JOURNEY_PALETTE} unit={U} />
        </div>
      ))}
      <div className={styles.flat} style={box}>
        <PixelSprite map={maps.top} palette={JOURNEY_PALETTE} unit={U} />
      </div>
    </>
  );
}

const DUCK_W = 16 * U;
const DUCK_H = 14 * U;

/** The duck inside a standing card; `innerRef` gets the part that fades and hops. */
function DuckFigure({ innerRef, children, style }: { innerRef?: Ref<HTMLDivElement>; children?: ReactNode; style?: CSSProperties }) {
  return (
    <div ref={innerRef} className={styles.duck} style={style}>
      <PixelDuck tempo="steady" />
      {children}
    </div>
  );
}

/** Laptop on the duck's lap, lid towards us; its screen lights the duck's face. */
function Laptop() {
  return (
    <>
      <span className={styles.screenGlow} />
      <div className={styles.laptop}>
        <PixelSprite map={LAPTOP} palette={JOURNEY_PALETTE} unit={U} />
      </div>
    </>
  );
}

/** A silhouette in the fog, with what lives on it: gulls overhead, or a lighthouse lamp (in map pixels). */
type Ghost = { map: string[]; gulls?: number; lamp?: Pt };

const { HUT_SHAPE, PALM, TALL_PINE } = FOG_PROPS;
const LIGHT_ISLE = fogIsle(30, 6, [[LIGHTHOUSE, 11], [TALL_PINE, 21]]);

/** For each step, the silhouettes in the fog: one to the upper right of the main figure, one to its lower left. */
const FOG: [Ghost, Ghost][] = [
  [{ map: WRECK, gulls: 2 }, { map: fogIsle(26, 5, [[PALM, 9], [TALL_PINE, 18]]).map }],
  [
    { map: LIGHT_ISLE.map, lamp: { x: LIGHT_ISLE.anchors[0].x + LAMP.x, y: LIGHT_ISLE.anchors[0].y + LAMP.y } },
    { map: ARCH, gulls: 1 },
  ],
  [{ map: fogIsle(34, 5, [[TALL_PINE, 7], [HUT_SHAPE, 17], [TALL_PINE, 27]]).map, gulls: 1 }, { map: STACKS, gulls: 2 }],
  [{ map: fogIsle(38, 6, [[TALL_PINE, 6], [TALL_PINE, 12], [PALM, 30]]).map, gulls: 3 }, { map: fogIsle(22, 4, [[HUT_SHAPE, 11]]).map }],
];

/** Pixel size of the fog silhouettes. */
const FOG_U = 8;

/** Pixel sizes of the scenery: pines and buildings are drawn coarser than the duck so they stand taller. */
const PINE_U = 6;
const BIG_U = 5;

/**
 * "Quack! персонализация, которая работает" — the student's path told as a
 * duck's trip across a small sea, one state per flick of the wheel:
 *
 * 1. a duck in a boat, a thread drifting ahead of it: where to even go?
 * 2. a sandy island with pines, the duck studying a map: choosing;
 * 3. a bigger island with a lookout, the duck on its roof with a laptop: preparing;
 * 4. the island turns round and comes closer as a cabin with a radio mast: what
 *    the preparation learns flows back into the choice.
 *
 * The sea is a plane tipped away from the viewer under a perspective, islands
 * lie on it with some thickness, and everything else stands upright on it
 * like cut-out figures — a paper-theatre 2.5D. The camera rides with the boat, so the duck
 * stays near the middle. Positions are written straight to the DOM.
 */
export function JourneySection() {
  const sectionRef = useRef<HTMLElement>(null);
  const sceneRef = useRef<HTMLDivElement>(null);
  const worldRef = useRef<HTMLDivElement>(null);
  const boatRef = useRef<HTMLDivElement>(null);
  const boatDuckRef = useRef<HTMLDivElement>(null);
  const boatWaterRef = useRef<HTMLDivElement>(null);
  const scoutRef = useRef<HTMLDivElement>(null);
  const coderRef = useRef<HTMLDivElement>(null);
  const turnerRef = useRef<HTMLDivElement>(null);
  const aheadRef = useRef<SVGPathElement>(null);
  const behindRef = useRef<SVGPathElement>(null);
  const gradRef = useRef<SVGLinearGradientElement>(null);
  const posRef = useRef(0);
  const t = copy.journey;

  /** The thread is redrawn on its own clock, so it keeps swaying while the scene is still. */
  const drawThread = useCallback((time: number) => {
    const here = boatDistance(posRef.current);
    const end = Math.min(ROUTE, here + THREAD_AHEAD);
    aheadRef.current?.setAttribute("d", threadPath(here, end, here, time));
    behindRef.current?.setAttribute("d", threadPath(Math.max(0, here - THREAD_BEHIND), here, here, time));
    const grad = gradRef.current;
    if (grad) {
      const a = routeAt(here);
      const b = routeAt(end);
      grad.setAttribute("x1", a.x.toFixed(1));
      grad.setAttribute("y1", a.y.toFixed(1));
      grad.setAttribute("x2", b.x.toFixed(1));
      grad.setAttribute("y2", b.y.toFixed(1));
    }
  }, []);

  const onFrame = useCallback(
    (pos: number) => {
      posRef.current = pos;
      const world = worldRef.current;
      const scene = sceneRef.current;
      if (!world || !scene) return;
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      // Boat: along the first leg, then the second; pulled up on shore in between.
      const b = routeAt(boatDistance(pos));
      const moving = (pos > 0.002 && pos < 0.998) || (pos > 1.002 && pos < 1.998);
      const bw = BOAT[0].length * BIG_U;
      const bh = BOAT.length * BIG_U;
      if (boatRef.current) {
        boatRef.current.style.transform = `translate3d(${(b.x - bw / 2).toFixed(1)}px, ${(b.y - bh).toFixed(1)}px, ${BOAT_Z}px) rotateX(-90deg) scaleY(${STAND_LIFT.toFixed(3)})`;
        boatRef.current.dataset.moving = String(moving);
      }
      if (boatWaterRef.current) {
        boatWaterRef.current.style.transform = `translate3d(${b.x.toFixed(1)}px, ${b.y.toFixed(1)}px, 0)`;
      }

      // Camera rides with the boat, drifting over to the middle of each island it reaches.
      const k = Math.min(N - 2, Math.floor(pos));
      const e = smooth(clamp01(pos - k));
      const fx = b.x + lerp(LOOK[k].x, LOOK[k + 1].x, e);
      const fy = b.y + lerp(LOOK[k].y, LOOK[k + 1].y, e);
      const vw = scene.clientWidth;
      const vh = scene.clientHeight;
      const base = Math.max(0.8, Math.min(2.6, Math.min(vw / 330, vh / 400)));
      const zoom = base * lerp(ZOOM[k], ZOOM[k + 1], e);
      const s = zoom.toFixed(4);
      const cy = vh / 2 + lerp(LIFT[k], LIFT[k + 1], e) * zoom;
      scene.parentElement?.style.setProperty("--half", `${(lerp(HALF[k], HALF[k + 1], e) * zoom).toFixed(0)}px`);
      world.style.transform = `translate(${(vw / 2).toFixed(1)}px, ${cy.toFixed(1)}px) scale3d(${s}, ${s}, ${s}) rotateX(${TILT}deg) translate(${(-fx).toFixed(1)}px, ${(-fy).toFixed(1)}px)`;

      // The duck steps ashore as the boat lands, and back in as it leaves.
      const onIsle1 = clamp01((0.16 - Math.abs(pos - 1)) / 0.08);
      const onIsle2 = clamp01((pos - 1.84) / 0.08);
      const inBoat = 1 - Math.max(onIsle1, onIsle2);
      const show = (el: HTMLDivElement | null, v: number) => {
        if (!el) return;
        el.style.opacity = v.toFixed(3);
        el.style.transform = `translateY(${((1 - v) * 10).toFixed(1)}px)`;
      };
      show(boatDuckRef.current, inBoat);
      show(scoutRef.current, onIsle1);
      show(coderRef.current, onIsle2);

      // Last step: the island turns a full circle on the spot and is rebuilt
      // while its figures stand edge-on.
      const turn = clamp01(pos - 2);
      if (turnerRef.current) {
        turnerRef.current.style.transform = `rotateZ(${reduced ? 0 : (360 * smooth(turn)).toFixed(2)}deg)`;
        turnerRef.current.dataset.after = String(reduced ? turn >= 0.5 : smooth(turn) >= 0.25);
      }

      if (reduced) drawThread(0);
    },
    [drawThread]
  );

  const { active, jumpTo } = useStepScroll(sectionRef, N, onFrame);

  // Wind: keep the thread swaying while the section is on screen.
  useEffect(() => {
    const section = sectionRef.current;
    if (!section || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let frame = 0;
    const loop = (now: number) => {
      drawThread(now / 1000);
      frame = requestAnimationFrame(loop);
    };
    const observer = new IntersectionObserver((entries) => {
      cancelAnimationFrame(frame);
      if (entries.some((en) => en.isIntersecting)) frame = requestAnimationFrame(loop);
    });
    observer.observe(section);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [drawThread]);

  // The ducks flying across the page sky would crowd this scene: while the
  // section holds the middle of the screen, the page is marked so the sky
  // leaves them out (see sky.module.css).
  useEffect(() => {
    const section = sectionRef.current;
    if (!section) return;
    const root = document.documentElement;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((en) => en.isIntersecting)) root.dataset.quietSky = "true";
        else delete root.dataset.quietSky;
      },
      { rootMargin: "-20% 0px -20% 0px" }
    );
    observer.observe(section);
    return () => {
      observer.disconnect();
      delete root.dataset.quietSky;
    };
  }, []);

  const bw = BOAT[0].length * BIG_U;
  const towerW = TOWER[0].length * BIG_U;
  const mastW = MAST[0].length * BIG_U;

  return (
    <section
      ref={sectionRef}
      id="journey"
      className={styles.section}
      style={{ height: `${100 + PER_STEP_VH * 100 * (N - 1)}vh` }}
      aria-labelledby="journey-heading"
    >
      <div className={styles.stage}>
        <h2 id="journey-heading" className={styles.heading}>
          {t.brand.slice(0, -1)}
          <span className={styles.bang}>{t.brand.slice(-1)}</span> {t.heading}
        </h2>

        <div ref={sceneRef} className={styles.scene} aria-hidden="true">
          <div ref={worldRef} className={styles.world} style={{ "--stand-lift": STAND_LIFT } as CSSProperties}>
            {/* Sea: only around the islands and around the boat. */}
            <Water c={ISLE_1} w={520} h={360} seed={5} />
            <Water c={ISLE_2} w={720} h={500} seed={9} />
            <div ref={boatWaterRef} className={styles.follower}>
              <Water c={{ x: 0, y: 0 }} w={320} h={220} seed={3} />
            </div>

            <svg
              className={styles.thread}
              style={{ left: THREAD_BOX.x, top: THREAD_BOX.y, width: THREAD_BOX.w, height: THREAD_BOX.h, transform: `translateZ(${THREAD_Z}px)` }}
              viewBox={`${THREAD_BOX.x} ${THREAD_BOX.y} ${THREAD_BOX.w} ${THREAD_BOX.h}`}
            >
              <defs>
                <linearGradient ref={gradRef} id="journey-thread" gradientUnits="userSpaceOnUse">
                  <stop offset="0" stopColor="#fff" stopOpacity="0.75" />
                  <stop offset="1" stopColor="#fff" stopOpacity="0" />
                </linearGradient>
              </defs>
              <path ref={behindRef} className={styles.threadBehind} />
              <path ref={aheadRef} stroke="url(#journey-thread)" />
            </svg>

            {/* First island: sand, a patch of grass, pines, and the scout. */}
            <div className={styles.group} style={{ left: ISLE_1.x, top: ISLE_1.y }}>
              <IslandBody {...ISLE_1_SIZE} grass={0.55} seed={11} />
              <Stand map={PINE} unit={PINE_U} x={-30} y={-30} />
              <Stand map={PINE} unit={PINE_U} x={12} y={-42} />
              <Stand map={PINE} unit={PINE_U} x={-8} y={-4} />
              <Stand map={BUSH} unit={PINE_U} x={-74} y={12} />
              <div className={styles.stand} style={place(58, 28, DUCK_W, DUCK_H)}>
                <DuckFigure innerRef={scoutRef}>
                  <div className={styles.map}>
                    <PixelSprite map={MAP} palette={JOURNEY_PALETTE} unit={U} />
                  </div>
                </DuckFigure>
              </div>
            </div>

            {/* Second island: a lookout first, a cabin with a radio mast after the turn. */}
            <div className={styles.group} style={{ left: ISLE_2.x, top: ISLE_2.y }}>
              <div ref={turnerRef} className={styles.turner} data-after="false">
                <IslandBody {...ISLE_2_SIZE} grass={0.8} seed={23} />
                <Stand map={PINE} unit={PINE_U} x={-140} y={-40} />
                <Stand map={PINE} unit={PINE_U} x={-118} y={22} />
                <Stand map={PINE} unit={PINE_U} x={146} y={-22} />
                <Stand map={BUSH} unit={PINE_U} x={130} y={52} />

                <div className={styles.before}>
                  <Stand map={TOWER} unit={BIG_U} x={0} y={4}>
                    <DuckFigure innerRef={coderRef} style={{ left: (towerW - DUCK_W) / 2, top: -DUCK_H + 6 }}>
                      <Laptop />
                    </DuckFigure>
                  </Stand>
                </div>

                <div className={styles.after}>
                  <Stand map={HUT} unit={BIG_U} x={-72} y={-4} />
                  <Stand map={MAST} unit={BIG_U} x={42} y={-36}>
                    <span className={styles.mastLight} style={{ left: mastW / 2 - 3, top: -6 }} />
                    <div className={styles.signal} style={{ left: mastW / 2, top: 0 }}>
                      {[5, 9, 13].map((r, i) => (
                        <div key={r} className={styles.arc} style={{ "--i": i, left: -r * U - U / 2, top: -r * U - U / 2 } as CSSProperties}>
                          <PixelSprite map={signalArc(r)} palette={JOURNEY_PALETTE} unit={U} />
                        </div>
                      ))}
                    </div>
                  </Stand>
                  <div className={styles.stand} style={place(96, 46, DUCK_W, DUCK_H)}>
                    <DuckFigure>
                      <Laptop />
                    </DuckFigure>
                  </div>
                </div>
              </div>
            </div>

            {/* The boat, with the duck sitting low inside it. */}
            <div ref={boatRef} className={styles.boat} style={{ width: bw }} data-moving="false">
              <div className={styles.bob}>
                <DuckFigure innerRef={boatDuckRef} style={{ left: 28, top: -42 }} />
                <PixelSprite map={BOAT} palette={JOURNEY_PALETTE} unit={BIG_U} />
                <div className={styles.wake}>
                  {WAKE.map((frame, i) => (
                    <PixelSprite key={i} map={frame} palette={JOURNEY_PALETTE} unit={BIG_U} className={i ? styles.glintB : styles.glintA} />
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Faint shapes in the fog in the corners the text leaves free, so the frame does not feel empty. */}
        {FOG.map((pair, i) => (
          <div key={i} className={styles.fog} data-active={i === active} aria-hidden="true">
            {pair.map((ghost, j) => (
              <div key={j} className={styles.ghost} data-place={j ? "low" : "high"}>
                <PixelSprite map={ghost.map} palette={FOG_PALETTE} unit={FOG_U} />
                {ghost.lamp && (
                  <span
                    className={styles.lamp}
                    style={{ left: (ghost.lamp.x + 0.5) * FOG_U, top: (ghost.lamp.y + 0.5) * FOG_U }}
                  >
                    <i className={styles.beam} />
                  </span>
                )}
                {Array.from({ length: ghost.gulls ?? 0 }, (_, g) => (
                  <span key={g} className={styles.gull} style={{ "--g": g } as CSSProperties}>
                    {GULL.map((frame, f) => (
                      <PixelSprite key={f} map={frame} palette={FOG_PALETTE} unit={3} className={f ? styles.glintB : styles.glintA} />
                    ))}
                  </span>
                ))}
              </div>
            ))}
          </div>
        ))}

        {/* The step's text, in two blocks either side of the main figure. */}
        {STEPS.map((pair, i) => (
          <div
            key={i}
            className={styles.copy}
            data-active={i === active}
            aria-hidden={i !== active}
          >
            <p className={styles.text} data-place="lead">
              <Accented text={pair[0]} />
            </p>
            <p className={styles.text} data-place="follow">
              <Accented text={pair[1]} />
            </p>
          </div>
        ))}

        <nav className={styles.nav} aria-label={t.heading}>
          <ol className={styles.pips}>
            {STEPS.map((_, i) => (
              <li key={i}>
                <button
                  type="button"
                  className={styles.pip}
                  data-active={i === active}
                  aria-current={i === active}
                  aria-label={`${t.stepLabel} ${i + 1}`}
                  onClick={() => jumpTo(i)}
                  data-aura
                />
              </li>
            ))}
          </ol>
        </nav>
      </div>
    </section>
  );
}
