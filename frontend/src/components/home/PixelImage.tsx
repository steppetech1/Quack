"use client";

import { useEffect, useState, type CSSProperties } from "react";
import type { Palette } from "./PixelSprite";

/* The same pixel maps PixelSprite draws as SVG, drawn once into a tiny bitmap
   instead: one image pixel per map pixel, scaled up by the GPU with nearest-
   neighbour filtering. Inside a moving 3D scene this is the difference between
   re-rasterising thousands of vector rects on every camera move and scaling a
   texture of a few kilobytes. */

const cache = new Map<string, string>();

/** Resolves `var(--name)` palette entries against the document, since a canvas cannot. */
function resolve(color: string) {
  const m = /^var\((--[\w-]+)\)$/.exec(color);
  return m ? getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim() || "#000" : color;
}

function bitmap(map: string[], palette: Palette): string {
  const key = `${Object.entries(palette).join(";")}|${map.join("\n")}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const w = map[0].length;
  const h = map.length;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return "";
  const colors: Record<string, string> = {};
  for (const [ch, c] of Object.entries(palette)) colors[ch] = resolve(c);
  map.forEach((row, y) => {
    let x = 0;
    while (x < w) {
      const ch = row[x];
      const c = colors[ch];
      if (!c) {
        x++;
        continue;
      }
      let run = 1;
      while (x + run < w && row[x + run] === ch) run++;
      ctx.fillStyle = c;
      ctx.fillRect(x, y, run, 1);
      x += run;
    }
  });
  const url = canvas.toDataURL("image/png");
  cache.set(key, url);
  return url;
}

type PixelImageProps = {
  map: string[];
  palette: Palette;
  /** Screen pixels per map pixel. */
  unit: number;
  className?: string;
  style?: CSSProperties;
};

/** A pixel-art sprite as a crisp, GPU-scaled bitmap. Renders an empty box of the right size until the bitmap is ready. */
export function PixelImage({ map, palette, unit, className, style }: PixelImageProps) {
  const [src, setSrc] = useState<string | null>(null);
  const key = map.join("\n");

  useEffect(() => {
    setSrc(bitmap(map, palette));
    // The key stands in for the map: callers may rebuild an identical map on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, palette]);

  const size: CSSProperties = { width: map[0].length * unit, height: map.length * unit, ...style };
  if (!src) return <span className={className} style={{ display: "block", ...size }} aria-hidden="true" />;
  // eslint-disable-next-line @next/next/no-img-element -- a generated data URL, nothing for next/image to optimise
  return <img className={className} src={src} alt="" draggable={false} style={{ display: "block", imageRendering: "pixelated", ...size }} />;
}

let pool = "";

/** A soft round pool of sea: a small radial gradient, smoothly scaled up to any size. */
export function usePoolImage() {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    if (!pool) {
      const size = 96;
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = size;
      const ctx = canvas.getContext("2d");
      if (ctx) {
        const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
        g.addColorStop(0, "rgba(62, 104, 120, 0.95)");
        g.addColorStop(0.45, "rgba(43, 71, 83, 0.85)");
        g.addColorStop(0.75, "rgba(43, 71, 83, 0.35)");
        g.addColorStop(1, "rgba(43, 71, 83, 0)");
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, size, size);
        pool = canvas.toDataURL("image/png");
      }
    }
    setSrc(pool);
  }, []);
  return src;
}
