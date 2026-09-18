"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { PixelDuck } from "@/components/duck/PixelDuck";
import { PixelSprite, type Palette } from "./PixelSprite";
import styles from "./sky.module.css";

const PALETTE: Palette = { n: "#ffffff" };

const CLOUD_A = [
  "......nnnn..........",
  "....nnnnnnnn..nn....",
  "..nnnnnnnnnnnnnnnn..",
  ".nnnnnnnnnnnnnnnnnnn",
  "nnnnnnnnnnnnnnnnnnnn",
  ".nnnnnnnnnnnnnnnnnn.",
];

const CLOUD_B = [
  "..........nnnnn.............",
  "...nnnn.nnnnnnnnn...........",
  ".nnnnnnnnnnnnnnnnnnn..nnn...",
  "nnnnnnnnnnnnnnnnnnnnnnnnnnn.",
  ".nnnnnnnnnnnnnnnnnnnnnnnnnnn",
];

type Cloud = {
  shape: string[];
  /** Screen pixels per sprite pixel: bigger reads as nearer. */
  unit: number;
  /** Starting height, as a share of the band the clouds wrap around in. */
  y: number;
  /** Where it rests without motion, as a share of the width. */
  x: number;
  /** How much of the page scroll the cloud follows: nearer ones move more. */
  depth: number;
  seconds: number;
  opacity: number;
};

/* Spread over the band so that any screen of the page has two or three in view. */
const CLOUDS: Cloud[] = [
  { shape: CLOUD_A, unit: 8, y: 0.08, x: 0.18, depth: 0.3, seconds: 160, opacity: 0.07 },
  { shape: CLOUD_B, unit: 6, y: 0.2, x: 0.7, depth: 0.22, seconds: 190, opacity: 0.06 },
  { shape: CLOUD_A, unit: 5, y: 0.34, x: 0.42, depth: 0.16, seconds: 230, opacity: 0.05 },
  { shape: CLOUD_B, unit: 8, y: 0.47, x: 0.08, depth: 0.32, seconds: 150, opacity: 0.07 },
  { shape: CLOUD_A, unit: 6, y: 0.6, x: 0.86, depth: 0.22, seconds: 200, opacity: 0.06 },
  { shape: CLOUD_B, unit: 5, y: 0.73, x: 0.3, depth: 0.14, seconds: 240, opacity: 0.05 },
  { shape: CLOUD_A, unit: 7, y: 0.86, x: 0.6, depth: 0.26, seconds: 175, opacity: 0.065 },
];

/** Extra height of the wrap band past the viewport, so a cloud leaves before it returns. */
const BAND_EXTRA = 300;

/**
 * One sky behind the whole page. Pixel clouds drift sideways on their own and
 * slide up a little slower than the content as the page scrolls, wrapping around,
 * so every part of the page — header, sections, footer — has some overhead.
 * Now and then a small wedge of ducks crosses it.
 */
export function PageSky() {
  const cloudRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let frame = 0;

    const place = () => {
      frame = 0;
      const band = window.innerHeight + BAND_EXTRA;
      const scroll = reduced ? 0 : window.scrollY;
      CLOUDS.forEach((cloud, i) => {
        const el = cloudRefs.current[i];
        if (!el) return;
        const y = (((cloud.y * band - scroll * cloud.depth) % band) + band) % band;
        el.style.transform = `translateY(${(y - BAND_EXTRA / 2).toFixed(1)}px)`;
      });
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(place);
    };

    place();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  return (
    <div className={styles.sky} aria-hidden="true">
      {CLOUDS.map((cloud, i) => (
        <div
          key={i}
          ref={(el) => {
            cloudRefs.current[i] = el;
          }}
          className={styles.cloud}
          style={{ opacity: cloud.opacity } as CSSProperties}
        >
          <div
            className={styles.drift}
            style={
              {
                "--seconds": `${cloud.seconds}s`,
                // Start each one part-way across, where it rests without motion.
                "--delay": `${-cloud.x * cloud.seconds}s`,
                "--rest": `${cloud.x * 100}vw`,
              } as CSSProperties
            }
          >
            <PixelSprite map={cloud.shape} palette={PALETTE} unit={cloud.unit} />
          </div>
        </div>
      ))}

      <div className={styles.flockLayer}>
        <Flock />
      </div>
    </div>
  );
}

/* A wedge flying right: the leader in front, the rest trailing behind it. */
const WEDGE = [
  [72, 36],
  [36, 18],
  [36, 54],
  [0, 0],
  [0, 72],
];

const FIRST_MS = 9000;
const MIN_GAP = 35000;
const MAX_GAP = 60000;

type Pass = { key: number; top: number };

/** Small ducks in a wedge, crossing the sky every half a minute or so. */
function Flock() {
  const [passes, setPasses] = useState<Pass[]>([]);
  const nextKey = useRef(0);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let timer: ReturnType<typeof setTimeout>;
    const schedule = (delay: number) => {
      timer = setTimeout(() => {
        if (!document.hidden) {
          const pass = { key: nextKey.current++, top: 8 + Math.random() * 50 };
          setPasses((list) => [...list, pass]);
        }
        schedule(MIN_GAP + Math.random() * (MAX_GAP - MIN_GAP));
      }, delay);
    };
    schedule(FIRST_MS);
    return () => clearTimeout(timer);
  }, []);

  return passes.map((pass) => (
    <div
      key={pass.key}
      className={styles.flock}
      style={{ top: `${pass.top}%` }}
      onAnimationEnd={(e) => {
        if (e.target === e.currentTarget) setPasses((list) => list.filter((p) => p.key !== pass.key));
      }}
    >
      {WEDGE.map(([x, y], i) => (
        <div key={i} className={styles.flockDuck} style={{ left: x, top: y }}>
          <PixelDuck tempo="fast" />
        </div>
      ))}
    </div>
  ));
}
