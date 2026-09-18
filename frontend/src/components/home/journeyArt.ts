import type { Palette } from "./PixelSprite";

/* Pixel art for the journey section. Everything sits on one 4px grid, the same
   one the pixel duck is drawn on, so the duck can stand in the scene as it is. */

export const U = 4;

export const JOURNEY_PALETTE: Palette = {
  w: "#2b4753", // water
  W: "#365a68", // water, streak
  h: "#7fa9b6", // glint
  s: "#d2b375", // sand
  S: "#b8975c", // sand, shade
  e: "#8f7549", // bank
  E: "#6c5836", // bank, bottom
  g: "#5c7c35", // grass
  G: "#4a672b", // grass, shade
  t: "#739545", // grass tufts
  l: "#3f6b3a", // needles
  L: "#2e5230", // needles, shade
  k: "#5a3d26", // trunk
  n: "#b98550", // wood, light
  m: "#94663a", // wood
  M: "#6b4526", // wood, dark
  v: "#2f2a26", // dark opening
  y: "#ffcf5a", // lamplight
  r: "#b0502f", // roof
  R: "#823920", // roof, eave
  x: "#b9b4ad", // laptop
  X: "#7d7771", // laptop, edge
  a: "var(--accent)",
  b: "#c79d3a", // brass
  B: "#8a6a22", // brass, dark
  o: "#2b2826", // outline
  i: "#8c959a", // steel
  I: "#5f676b", // steel, dark
  p: "#e9ddb6", // paper
  P: "#c8b88c", // paper, fold
};

/** Cheap deterministic noise, so a generated map is the same on every render. */
function noise(x: number, y: number, seed: number) {
  let h = Math.imul(x, 374761393) + Math.imul(y, 668265263) + Math.imul(seed, 1442695041);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967295;
}

/** Normalised distance from the centre of a w×h ellipse: 1 on its rim. */
function radius(x: number, y: number, w: number, h: number) {
  const nx = (x + 0.5 - w / 2) / (w / 2);
  const ny = (y + 0.5 - h / 2) / (h / 2);
  return Math.sqrt(nx * nx + ny * ny);
}

/** A few short glints on the water. Two frames with different seeds alternate. */
export function glintMap(w: number, h: number, seed: number) {
  const rows: string[] = [];
  for (let y = 0; y < h; y++) {
    let row = "";
    for (let x = 0; x < w; x++) {
      const d = radius(x, y, w, h);
      const lead = x > 0 && row[x - 1] === "h";
      row += d < 0.8 && (lead ? noise(x, y, seed + 3) > 0.4 : noise(x, y, seed) > 0.985) ? "h" : ".";
    }
    rows.push(row);
  }
  return rows;
}

/**
 * The top of an island seen from straight above: an oval of grass ringed with
 * sand. It lies flat on the tilted sea, so the perspective squashes it into
 * the view. `grass` is how far out the grass reaches, as a share of the radius.
 */
export function islandTop(w: number, h: number, grass: number, seed: number) {
  const rows: string[] = [];
  for (let y = 0; y < h; y++) {
    let row = "";
    for (let x = 0; x < w; x++) {
      const d = radius(x, y, w, h);
      const n = noise(x, y, seed);
      if (d > 1) row += ".";
      else if (d > grass + (noise(x >> 1, y >> 1, seed + 1) - 0.5) * 0.12) row += d > 0.93 ? "S" : n > 0.94 ? "S" : "s";
      else row += n > 0.86 ? "G" : n < 0.05 ? "t" : "g";
    }
    rows.push(row);
  }
  return rows;
}

/** One slice of an island's bank: the same oval, filled with one colour. Slices stack downwards. */
export function islandSlice(w: number, h: number, ch: string) {
  const rows: string[] = [];
  for (let y = 0; y < h; y++) {
    let row = "";
    for (let x = 0; x < w; x++) row += radius(x, y, w, h) <= 1 ? ch : ".";
    rows.push(row);
  }
  return rows;
}

/** Copies `patch` onto `base` with its top-left corner at (x, y). Dots are transparent. */
function stamp(base: string[], patch: string[], x: number, y: number) {
  const out = base.map((row) => row.split(""));
  patch.forEach((row, dy) => {
    [...row].forEach((ch, dx) => {
      if (ch !== "." && out[y + dy] && x + dx < out[y + dy].length) out[y + dy][x + dx] = ch;
    });
  });
  return out.map((row) => row.join(""));
}

export const BOAT = [
  "n......................n",
  "nnnnnnnnnnnnnnnnnnnnnnnn",
  "mmmmmmmmmmmmmmmmmmmmmmmm",
  ".MmmmmmmmmmmmmmmmmmmmmM.",
  "..MMMMMMMMMMMMMMMMMMMM..",
];

/** Foam trailing the stern; two frames. */
export const WAKE = [
  ["hh..h...", "..hh..h."],
  [".hh...h.", "h..hh..."],
];

export const PINE = [
  "...l...",
  "..lll..",
  "..lLl..",
  ".lllll.",
  "..lLl..",
  ".llllL.",
  "lllLlll",
  ".lllll.",
  "llLllll",
  "lllllLl",
  "...k...",
  "...k...",
];

export const BUSH = [".lll.", "llLll", "lLlll"];

/** A wooden lookout on stilts. The duck sits on its flat roof. */
export const TOWER = [
  "nnnnnnnnnnnnnnnnnnnn",
  "mmmmmmmmmmmmmmmmmmmm",
  ".MMMMMMMMMMMMMMMMMM.",
  "..mnmmnmmnmmnmmnmm..",
  "..mnmmnmvvvvnmmnmm..",
  "..mnmmnmvvvvnmmnmm..",
  "..mnmmnmmnmmnmmnmm..",
  "nnnnnnnnnnnnnnnnnnnn",
  "MMMMMMMMMMMMMMMMMMMM",
  "..M..............M..",
  "..MM............MM..",
  "..M.M..........M.M..",
  "..M..M........M..M..",
  "..M...M......M...M..",
  "..M....M....M....M..",
  "..M.....M..M.....M..",
  "..M......MM......M..",
  "..M......MM......M..",
  "..M.....M..M.....M..",
  "..M....M....M....M..",
  "..M...M......M...M..",
  "..M..M........M..M..",
  "..MnnnnnnnnnnnnnnM..",
  "..M..............M..",
  ".MMM............MMM.",
];

const WALL = "nmmnmmnmmnmmnmmnmmnm";

/** The cabin that replaces the lookout: a lit window and an open door. */
export const HUT = stamp(
  [
    "...........rr...........",
    ".........rrrrrr.........",
    ".......rrrrrrrrrr.......",
    ".....rrrrrrrrrrrrrr.....",
    "...rrrrrrrrrrrrrrrrrr...",
    ".RRRRRRRRRRRRRRRRRRRRRR.",
    ...Array.from({ length: 10 }, () => `..${WALL}..`),
    ".MMMMMMMMMMMMMMMMMMMMMM.",
  ],
  [
    // Door on the left, window on the right, both framed in dark wood.
    "MMMMM......MMMMMM",
    "MvvvM......MyyyyM",
    "MvvvM......MyMyyM",
    "MvvvM......MyyyyM",
    "MvvvM......MMMMMM",
    "MvvvM............",
    "MvvyM............",
    "MvvvM............",
  ],
  4,
  8
);

/** A small lattice radio mast; the light on top is drawn separately so it can blink. */
export const MAST = (() => {
  const w = 9;
  const h = 32;
  const c = 4;
  const rows: string[] = [];
  for (let y = 0; y < h; y++) {
    const row = Array(w).fill(".");
    if (y < 4) {
      row[c] = "I";
    } else {
      const t = (y - 4) / (h - 5);
      const half = Math.round(1 + t * 3);
      row[c - half] = "I";
      row[c + half] = "I";
      const phase = (y - 4) % 4;
      if (phase === 0) for (let x = c - half + 1; x < c + half; x++) row[x] = "i";
      else {
        const off = Math.round((phase / 4) * (2 * half));
        row[c - half + off] = "i";
        row[c + half - off] = "i";
      }
    }
    rows.push(row.join(""));
  }
  return rows;
})();

/** Back of an open laptop, lid towards the viewer, with a small accent logo. */
export const LAPTOP = ["XxxxxxxxX", "XxxxxxxxX", "XxxxaxxxX", "XxxxxxxxX", "XXXXXXXXX", ".XXXXXXX."];

/** An open map held up in front of the duck: land, sea, and a dotted route to a red mark. */
export const MAP = [
  "pppppPpppppp",
  "pggppPpppwwp",
  "pgggpPppwwwp",
  "ppgapPppwwpp",
  "pppaaPappppp",
  "pppppaPpaprp",
  "pppppPpppppp",
];

/** Rings of the signal leaving the mast, smallest first. Each is an arc of pixels. */
export function signalArc(r: number) {
  const size = 2 * r + 1;
  const rows = Array.from({ length: size }, () => Array(size).fill("."));
  for (let a = -150; a <= -30; a += 4) {
    const rad = (a * Math.PI) / 180;
    const x = Math.round(r + Math.cos(rad) * r);
    const y = Math.round(r + Math.sin(rad) * r);
    rows[y][x] = "a";
  }
  return rows.map((row) => row.join(""));
}

/* Silhouettes in the fog round the edges of the scene, in two flat tones
   (light on top, darker at the waterline), drawn faint and soft by the stylesheet. */

export const FOG_PALETTE: Palette = { f: "#aec1c7", F: "#7e9298" };

/** Rows padded to one width, so a hand-drawn map never comes out ragged. */
function even(map: string[]) {
  const w = Math.max(...map.map((r) => r.length));
  return map.map((r) => r.padEnd(w, "."));
}

/** A wreck sinking stern first: the bow up in the air, a broken mast with a rag of sail, portholes. */
export const WRECK = even([
  "......................F",
  "..........f..........FF",
  "..........ff........fFF",
  "..........fff......ffFF",
  "..........ff......fffFF",
  "..........f......ff.fFF",
  "..........f.....fffffFF",
  "..........f....fff.ffFF",
  ".........ff...fffffffF",
  "........fff..fffff.ffF",
  ".......fffffffffffffF",
  "......ffff.fffffffffF",
  ".....fffffffffff.ffF",
  "....ffffffffffffffF",
  "..FFFFFFFFFFFFFFFF...F.F",
  ".FFFFFFFFFFFFF....F.....",
]);

export const LIGHTHOUSE = [
  "...f...",
  "..fff..",
  ".f...f.",
  ".fffff.",
  "..fff..",
  "..FFF..",
  "..fff..",
  "..fff..",
  ".FFFFF.",
  ".fffff.",
  ".fffff.",
  ".FFFFF.",
  ".fffff.",
  "fffffff",
];
/** Where the lamp sits in the lighthouse map, in its pixels. */
export const LAMP = { x: 3, y: 2 };

const HUT_SHAPE = [
  "....fff....",
  "..fffffff..",
  "fffffffffff",
  ".fffffffff.",
  ".ff..fffff.",
  ".ff..ff..f.",
  ".fffffff.f.",
  ".FFFFFFFFF.",
];

const PALM = [
  "ff.....ff",
  ".fff.fff.",
  "...fff...",
  "..f.f.f..",
  ".f..f..f.",
  "....f....",
  ".....f...",
  ".....f...",
  "....f....",
  "....f....",
];

const TALL_PINE = [
  "...f...",
  "..fff..",
  ".fffff.",
  "..fff..",
  ".fffff.",
  "fffffff",
  "..fff..",
  ".fffff.",
  "fffffff",
  "...F...",
];

/** A rock with a sea arch worn through it, and a smaller rock beside it. */
export const ARCH = even([
  "......fffff.........",
  "....fffffffff.......",
  "...fffffffffff......",
  "..fffff...ffffff....",
  "..ffff.....fffff....",
  ".ffff.......ffff..f.",
  ".fff.........fff.fff",
  "FFF...........FFFFFF",
]);

/** Three sea stacks, the tallest on the left. */
export const STACKS = even([
  "....f",
  "...fff",
  "...fff.........f",
  "..ffff........fff",
  "..fffff.......fff",
  "..fffff......ffff....f",
  ".ffffff......fffff..fff",
  ".ffffff.....ffffff..fff",
  "FFFFFFFF....FFFFFF.FFFFF",
]);

/**
 * An islet on the horizon: a low dome, light above and dark at the waterline,
 * with props standing on it centred on the given columns. Also returns where
 * each prop's top-left corner ended up, for anything drawn on top of it.
 */
export function fogIsle(w: number, h: number, props: [string[], number][] = []) {
  const top = Math.max(0, ...props.map(([m]) => m.length));
  const rows = Array.from({ length: top + h }, () => Array(w).fill("."));
  const surface: number[] = [];
  for (let x = 0; x < w; x++) {
    const nx = (x + 0.5 - w / 2) / (w / 2);
    const rise = Math.max(1, Math.round(h * Math.sqrt(Math.max(0, 1 - nx * nx))));
    surface.push(top + h - rise);
    for (let y = top + h - rise; y < top + h; y++) rows[y][x] = y >= top + h - 2 ? "F" : "f";
  }
  const anchors = props.map(([map, cx]) => {
    const pw = map[0].length;
    const x0 = cx - Math.floor(pw / 2);
    const y0 = surface[Math.min(w - 1, Math.max(0, cx))] - map.length + 1;
    map.forEach((row, dy) =>
      [...row].forEach((ch, dx) => {
        if (ch !== "." && rows[y0 + dy] && x0 + dx >= 0 && x0 + dx < w) rows[y0 + dy][x0 + dx] = ch;
      })
    );
    return { x: x0, y: y0 };
  });
  return { map: rows.map((r) => r.join("")), anchors };
}

/** Pieces the fog scenes are built from. */
export const FOG_PROPS = { HUT_SHAPE, PALM, TALL_PINE };

/** A gull, wings up and wings down. */
export const GULL = [
  ["f...f", ".f.f.", "..f.."],
  [".....", "fffff", "..f.."],
];
