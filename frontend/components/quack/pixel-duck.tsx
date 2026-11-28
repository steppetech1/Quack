"use client";

import { useEffect, useRef, useState, type CSSProperties, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useT } from "@/lib/i18n/provider";

/* Маленькая пиксельная утка-маскот (кряква-селезень). Один компонент, два режима:
   - "walk": ходит по верхней кромке букв заголовка пустого состояния (anchorRef) на пустом экране предмета;
   - "perch": стоит/сидит у ПРАВОГО края верхней кромки композера (родитель с position: relative), уходит гулять
     влево по клику и при простое, после прогулки возвращается на своё место справа и снова садится.
   Мультяшная чиби-кряква с тёмно-зелёной обводкой (`#0b2415`) без бликов: сетка 16×18 арт-пикселей, масштаб ×2 (32×36 px), 12 цветов палитры.
   Звука нет. Без библиотек: спрайт рисуется на `<canvas width={16} height={18}>` с `image-rendering: pixelated`
   (0 ререндеров React, честный nearest-neighbor при любом DPR, без сдвига ленты и замыливания SVG в Chromium),
   перемещение — один rAF-цикл с `translate(...)` со снапом к физическим пикселям экрана; цикл стоит, когда вкладка скрыта,
   утка вне экрана, утка на паузе (наведение/фокус/кряк), в покое (perch) и при prefers-reduced-motion.
   React рендерится только на клик (состояние «крякнула» — чистый текст без прямоугольника), на кадрах — никогда. Стили — в globals.css (.pd*). */

/** Осознанное исключение владельца из палитры дизайн-системы: цвета мультяшной кряквы с тёмно-зелёной обводкой (см. DESIGN.md → Pixel Duck) */
const PAL: Record<string, string> = {
  k: "#0b2415", // глубокая тёмно-зелёная контурная обводка
  e: "#08110b", // высокий мультяшный глаз (1×2)
  d: "#187a3d", // тёмно-зелёная тень головы и шеи
  g: "#14a131", // ярко-зелёная голова
  y: "#fffb40", // ярко-жёлтый верх клюва
  a: "#fed440", // тёпло-золотой клюв
  w: "#fefefe", // белый воротник
  i: "#e5e5ff", // светло-ледяное крыло и кончик хвоста
  l: "#bbbffc", // мягкая тень крыла
  c: "#bc7549", // тёпло-каштановая грудь
  r: "#71413d", // тёмно-коричневый низ груди
  o: "#fa6a0a", // оранжевые лапы
};

const S = 2; // целочисленный масштаб: 1 пиксель арта = 2 CSS-пикселя
const COLS = 16;
const ROWS = 18;
const W = COLS * S; // 32
const H = ROWS * S; // 36

/** Тело без нижней строки контакта (15 строк; на шагах 1 и 2 поднимается на 1 арт-пиксель вверх для живой походки) */
const BODY = [
  "..........kk....",
  "........kkgdk...",
  ".......kdggggk..",
  "......kdgggggk..",
  "......kdgeggayk.",
  "......kdgegayyyk",
  "......kdgggaaaak",
  "...kk..kddddkkk.",
  "...kikkkiwwwk...",
  ".kiiiiilrcccck..",
  ".kiiiiirrccccck.",
  ".kliiiirrccccck.",
  "..kllllrrrcccrk.",
  "..klllllrrrrrrk.",
  "...kllkllrrrrk..",
];
const BLANK = ".".repeat(COLS);
const patch = (rows: string[], p: Record<number, string>) => rows.map((r, i) => p[i] ?? r);

/** Кадр 0 смотрит вправо (при data-dir="left" .pd-flip отражает его 1:1 к исходному мультяшному спрайту) */
const BASE = [
  BLANK,
  ...BODY,
  "....kkokkkkkk...",
  ".....koookkook..",
];
const SIT = [BLANK, BLANK, ...BODY.slice(0, 14), "...klllllrrrrk..", "....kkkkkkkkk..."];

/** 0 стоит, 1–2 шаги (тело +1px вверх, ноги по очереди), 3 кряк (клюв открыт), 4 сидит, 5 сидит с закрытым глазом */
const FRAMES = [
  BASE,
  // поднятая лапа идёт вперёд под телом (носок вперёд, голень на месте), а не торчит назад за контур
  [...BODY, "....kkokkkokk...", ".....kookkok....", ".........koook.."],
  [...BODY, "....kkokkkkokk..", ".....kok..kook..", ".....koook......"],
  patch(BASE, {
    4: "......kdgggggkk.",
    5: "......kdgeggayyk",
    6: "......kdgegk....",
    7: "......kdgggaaak.",
  }),
  SIT,
  patch(SIT, {
    6: "......kdggggayk.",
    7: "......kdgkgayyyk",
  }),
];

/** Горизонтальные полоски для быстрого рисования кадра в 16×18 <canvas> */
const FRAME_RECTS = FRAMES.map((rows) => {
  const rects: { fill: string; x: number; y: number; w: number }[] = [];
  rows.forEach((row, y) => {
    let x = 0;
    while (x < COLS) {
      const c = row[x];
      if (c === ".") {
        x++;
        continue;
      }
      let w = 1;
      while (x + w < COLS && row[x + w] === c) w++;
      rects.push({ fill: PAL[c], x, y, w });
      x += w;
    }
  });
  return rects;
});

const WALK_FRAMES = [1, 0, 2, 0] as const;
const WALK_STEP_S = 0.13;
const BLINK_WAIT_MS = 5_600;
const BLINK_DUR_MS = 180;

/** Простой в композере до того, как утка сама пойдёт гулять, мс */
export const PERCH_IDLE_MS = 25_000;
/** Сколько ходит после клика / после простоя, мс */
const PERCH_CLICK_WALK_MS = 8_000;
const PERCH_IDLE_WALK_MS = 10_000;
/** Сколько висит «Quack!», мс */
const QUACK_MS = 1_000;
/** Скорость ходьбы, px/с */
const SPEED = { walk: 30, perch: 44 };
/** Лапы чуть заходят в букву/рамку, чтобы утка стояла, а не висела */
const SINK = 1;
const EDGE_PAD = 12;
/** Сколько места над уткой нужно, чтобы подпись встала над ней, px */
const BUBBLE_ROOM = 40;

type Props =
  | { mode: "walk"; anchorRef: RefObject<HTMLElement | null>; /** текст заголовка: смена пересчитывает границы */ text: string }
  | { mode: "perch" };

type Geo = {
  lo: number;
  hi: number;
  /** верх хоста (translateY), когда нет профиля букв */
  ground: number;
  /** профиль верхней кромки букв первой строки */
  chars: { l: number; r: number; top: number }[] | null;
  floor: number;
};

type Ctl = { quack: (on: boolean) => void; hover: (on: boolean) => void; focus: (on: boolean) => void };

const rand = (a: number, b: number) => a + Math.random() * (b - a);

export function PixelDuck(props: Props) {
  const t = useT();
  const { mode } = props;
  const anchorRef = props.mode === "walk" ? props.anchorRef : undefined;
  const text = props.mode === "walk" ? props.text : "";
  const hostRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const hopRef = useRef<HTMLSpanElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const ctl = useRef<Ctl | null>(null);
  const pos = useRef<{ x: number; y: number; dir: 1 | -1 }>({ x: NaN, y: NaN, dir: 1 });
  const quackTimer = useRef(0);
  const [n, setN] = useState(0);
  const [bubble, setBubble] = useState<{ x: number; y: number; side: "top" | "right" | "left" } | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    const btn = btnRef.current;
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    const parent = host?.parentElement;
    const anchor = anchorRef?.current ?? null;
    if (!host || !btn || !canvas || !ctx || !parent || (mode === "walk" && !anchor)) return;
    const watch: HTMLElement = anchor ?? parent;
    const perch = mode === "perch";
    const speed = SPEED[mode];
    const st0 = pos.current;
    ctx.imageSmoothingEnabled = false;

    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    let reduced = mq.matches;
    let visible = true;
    let hovered = false;
    let focused = false;
    let quacking = false;
    let pending = false; // простой наступил, пока вкладка скрыта / утка вне экрана
    let geo: Geo | null = null;
    let raf = 0;
    let last = 0;
    let active = false;
    let timer = 0;
    let blinkTimer = 0;
    let dead = false;
    let walkUntil = 0;
    let walkPhase = 0;
    let homing = false; // perch: прогулка кончилась, идём обратно на своё место справа
    let state: "sit" | "idle" | "walk" | "look" = perch ? "sit" : "idle";
    let currentFrame = -1;
    let left = perch ? 0 : rand(0.4, 1);
    let lastRx = NaN;
    let lastRy = NaN;
    let lastPose = "";
    let lastDir = 0;

    const paused = () => hovered || focused || quacking;
    const canWalk = () => !reduced && visible && !document.hidden && !!geo && geo.hi - geo.lo > 6;
    const canBlink = () => !reduced && visible && !document.hidden && !paused() && state === "sit";
    const showFrame = (f: number) => {
      if (f === currentFrame) return;
      currentFrame = f;
      ctx.clearRect(0, 0, COLS, ROWS);
      for (const r of FRAME_RECTS[f]) {
        ctx.fillStyle = r.fill;
        ctx.fillRect(r.x, r.y, r.w, 1);
      }
    };
    showFrame(perch ? 4 : 0);
    const clearBlink = () => {
      if (blinkTimer) {
        window.clearTimeout(blinkTimer);
        blinkTimer = 0;
      }
    };
    const armBlink = () => {
      if (dead || blinkTimer || !canBlink()) return;
      blinkTimer = window.setTimeout(() => {
        blinkTimer = 0;
        if (dead || !canBlink()) return;
        showFrame(5);
        blinkTimer = window.setTimeout(() => {
          blinkTimer = 0;
          if (dead) return;
          if (canBlink()) {
            showFrame(4);
            armBlink();
          } else syncFrame();
        }, BLINK_DUR_MS);
      }, BLINK_WAIT_MS);
    };
    function syncFrame() {
      if (quacking) {
        clearBlink();
        showFrame(3);
      } else if (state === "walk" && active) {
        clearBlink();
        showFrame(WALK_FRAMES[Math.floor(walkPhase / WALK_STEP_S) % WALK_FRAMES.length]);
      } else if (state === "sit") {
        if (currentFrame !== 5 || !canBlink()) showFrame(4);
        if (canBlink()) armBlink();
        else clearBlink();
      } else {
        clearBlink();
        showFrame(0);
      }
    }
    const setPose = (p: string) => {
      if (p !== lastPose) {
        lastPose = p;
        host.dataset.pose = p;
      }
    };
    const setDir = (d: 1 | -1) => {
      st0.dir = d;
      if (d !== lastDir) {
        lastDir = d;
        host.dataset.dir = d > 0 ? "right" : "left";
      }
    };
    const applyPose = () => setPose(state === "walk" && !active ? "idle" : state);

    const targetY = (x: number) => {
      if (!geo) return 0;
      if (!geo.chars) return geo.ground;
      // стоим на самой высокой букве под лапами
      const a = x + W * 0.25;
      const b = x + W * 0.88;
      let top = geo.floor;
      for (const c of geo.chars) if (c.r > a && c.l < b && c.top < top) top = c.top;
      return top - H + SINK;
    };

    const place = (dt: number, snap = false) => {
      if (!geo) return;
      const rx = Math.round(st0.x);
      const target = targetY(rx);
      if (snap || Number.isNaN(st0.y)) st0.y = target;
      else {
        st0.y += (target - st0.y) * Math.min(1, dt * 16);
        if (Math.abs(target - st0.y) < 0.5) st0.y = target;
      }
      const dpr = window.devicePixelRatio || 1;
      const pr = parent.getBoundingClientRect();
      const sx = Math.round((pr.left + rx) * dpr) / dpr - pr.left;
      const sy = Math.round((pr.top + st0.y) * dpr) / dpr - pr.top;
      if (sx !== lastRx || sy !== lastRy) {
        lastRx = sx;
        lastRy = sy;
        host.style.transform = `translate(${sx}px,${sy}px)`;
      }
    };

    const measure = () => {
      if (dead) return;
      if (perch) {
        const cw = parent.clientWidth;
        geo = { lo: EDGE_PAD, hi: Math.max(EDGE_PAD, cw - W - EDGE_PAD), ground: -parent.clientTop - H + SINK, chars: null, floor: 0 };
      } else {
        const node = anchor?.firstChild;
        if (!anchor || !node || node.nodeType !== 3) {
          geo = null;
          host.removeAttribute("data-ready");
          return;
        }
        const str = node.textContent ?? "";
        const cs = getComputedStyle(anchor);
        const ctx = document.createElement("canvas").getContext("2d");
        const fs = parseFloat(cs.fontSize) || 40;
        if (ctx) ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
        const fontAsc = ctx?.measureText("H").fontBoundingBoxAscent ?? fs;
        const xAsc = ctx?.measureText("x").actualBoundingBoxAscent ?? fs * 0.5;
        const pr = parent.getBoundingClientRect();
        const rng = document.createRange();
        const chars: NonNullable<Geo["chars"]> = [];
        let lineTop = NaN;
        let off = 0;
        for (const ch of str) {
          rng.setStart(node, off);
          rng.setEnd(node, off + ch.length);
          off += ch.length;
          const r = rng.getBoundingClientRect();
          if (!r.width) continue;
          if (Number.isNaN(lineTop)) lineTop = r.top;
          else if (r.top > lineTop + r.height * 0.5) break; // перенос: ходим только по первой строке
          const asc = Math.max(ctx?.measureText(ch).actualBoundingBoxAscent ?? 0, xAsc);
          chars.push({ l: r.left - pr.left, r: r.right - pr.left, top: r.top - pr.top + fontAsc - asc });
        }
        if (!chars.length) {
          geo = null;
          host.removeAttribute("data-ready");
          return;
        }
        const floor = chars.reduce((m, c) => Math.max(m, c.top), -Infinity);
        geo = { lo: chars[0].l, hi: chars[chars.length - 1].r - W, ground: 0, chars, floor };
      }
      if (Number.isNaN(st0.x)) st0.x = perch ? geo.hi : geo.lo + (geo.hi - geo.lo) * 0.25;
      st0.x = Math.min(geo.hi, Math.max(geo.lo, st0.x));
      if (perch && state === "sit") st0.x = geo.hi; // дом — правый край кромки, при смене ширины едем за ним
      setDir(st0.dir);
      place(0, true);
      host.setAttribute("data-ready", "");
      update();
    };

    const enter = (s: typeof state, secs: number) => {
      state = s;
      left = secs;
      if (s !== "walk") walkPhase = 0;
      applyPose();
      syncFrame();
    };

    // прогулка кончилась: идём вправо, на своё место, и там садимся (без ходьбы — сразу садимся на месте)
    const endWalk = () => {
      walkUntil = 0;
      if (perch && geo && canWalk() && st0.x < geo.hi - 1) {
        homing = true;
        setDir(1);
        state = "walk";
      } else {
        homing = false;
        if (geo) st0.x = geo.hi;
        state = "sit";
      }
      update();
    };

    const startWalk = (ms: number) => {
      if (!perch || !canWalk()) return;
      homing = false;
      walkUntil = Math.max(walkUntil, performance.now() + ms);
      if (state === "sit" && geo) {
        setDir(st0.x > (geo.lo + geo.hi) / 2 ? -1 : 1);
        enter("walk", rand(2, 4));
      }
      update();
    };

    const step = (now: number, dt: number) => {
      if (!geo) return;
      if (perch && homing) {
        st0.x += speed * dt;
        if (st0.x >= geo.hi) {
          st0.x = geo.hi;
          homing = false;
          state = "sit";
          return update();
        }
        walkPhase = (walkPhase + dt) % (WALK_STEP_S * WALK_FRAMES.length);
        showFrame(WALK_FRAMES[Math.floor(walkPhase / WALK_STEP_S) % WALK_FRAMES.length]);
        return place(dt);
      }
      if (perch && now >= walkUntil) return endWalk();
      left -= dt;
      if (state === "walk") {
        st0.x += st0.dir * speed * dt;
        walkPhase = (walkPhase + dt) % (WALK_STEP_S * WALK_FRAMES.length);
        showFrame(WALK_FRAMES[Math.floor(walkPhase / WALK_STEP_S) % WALK_FRAMES.length]);
        // граница — только по ходу движения: утка стартует от самого края, и в первом кадре (dt = 0) она у границы
        if ((st0.dir < 0 && st0.x <= geo.lo) || (st0.dir > 0 && st0.x >= geo.hi)) {
          st0.x = Math.min(geo.hi, Math.max(geo.lo, st0.x));
          enter("idle", rand(0.5, 1.1));
        } else if (left <= 0) enter("idle", rand(0.7, 1.8));
      } else if (state === "idle") {
        if (left <= 0) enter("look", 1);
      } else if (state === "look" && left <= 0) {
        const inward = st0.x <= geo.lo + 1 ? 1 : st0.x >= geo.hi - 1 ? -1 : 0;
        setDir((inward || (Math.random() < 0.4 ? -st0.dir : st0.dir)) as 1 | -1);
        enter("walk", rand(2.5, 5.5));
      }
      place(dt);
    };

    const tick = (now: number) => {
      raf = 0;
      const dt = last ? Math.max(0, Math.min(0.05, (now - last) / 1000)) : 0;
      last = now;
      step(now, dt);
      if (active && !raf) raf = requestAnimationFrame(tick);
    };

    function update() {
      active = canWalk() && !paused() && state !== "sit";
      if (active) {
        if (!raf) {
          last = 0;
          raf = requestAnimationFrame(tick);
        }
      } else {
        if (raf) {
          cancelAnimationFrame(raf);
          raf = 0;
          last = 0;
        }
        walkPhase = 0;
      }
      // остановка посреди плавного шага по буквам: встаём точно на кромку, а не зависаем между ними
      if (!active) place(0, true);
      applyPose();
      syncFrame();
      if (pending && state === "sit" && canWalk()) {
        pending = false;
        startWalk(PERCH_IDLE_WALK_MS);
      }
    }

    // простой в композере: один таймер, сбрасывается на input / keydown / pointerdown, не на кадрах
    const arm = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        if (canWalk()) startWalk(PERCH_IDLE_WALK_MS);
        else pending = !reduced;
      }, PERCH_IDLE_MS);
    };
    const onActivity = (e: Event) => {
      pending = false;
      // печатает — утка сидит (нажатия на самой утке не в счёт)
      if (e.type !== "pointerdown" && !btn.contains(e.target as Node) && state !== "sit" && !homing) endWalk();
      arm();
    };

    ctl.current = {
      quack: (on) => {
        quacking = on;
        if (on) {
          if (state === "sit") setPose("idle");
        } else if (perch) startWalk(PERCH_CLICK_WALK_MS);
        update();
      },
      hover: (on) => {
        hovered = on;
        update();
      },
      focus: (on) => {
        focused = on;
        update();
      },
    };

    const onVis = () => update();
    const onMq = () => {
      reduced = mq.matches;
      if (reduced) {
        walkUntil = 0;
        homing = false;
        state = perch ? "sit" : "idle";
        if (perch && geo) st0.x = geo.hi;
      }
      update();
    };
    const ro = new ResizeObserver(measure);
    ro.observe(watch);
    const io = new IntersectionObserver((es) => {
      visible = es[es.length - 1].isIntersecting;
      update();
    });
    io.observe(watch);
    document.addEventListener("visibilitychange", onVis);
    mq.addEventListener("change", onMq);
    if (perch) {
      parent.addEventListener("input", onActivity, { passive: true });
      parent.addEventListener("keydown", onActivity, { passive: true });
      parent.addEventListener("pointerdown", onActivity, { passive: true });
      arm();
    }
    void document.fonts?.ready.then(measure);
    measure();

    return () => {
      dead = true;
      ctl.current = null;
      if (raf) cancelAnimationFrame(raf);
      window.clearTimeout(timer);
      clearBlink();
      ro.disconnect();
      io.disconnect();
      document.removeEventListener("visibilitychange", onVis);
      mq.removeEventListener("change", onMq);
      if (perch) {
        parent.removeEventListener("input", onActivity);
        parent.removeEventListener("keydown", onActivity);
        parent.removeEventListener("pointerdown", onActivity);
      }
    };
  }, [mode, anchorRef, text]);

  useEffect(() => () => window.clearTimeout(quackTimer.current), []);

  const onClick = () => {
    ctl.current?.quack(true); // сначала пауза (утка встаёт на кромку), потом замер места для подписи
    const r = btnRef.current?.getBoundingClientRect();
    if (r) {
      // над уткой, если сверху есть место; у верхнего края окна — сбоку на уровне лап (не закрывая буквы и не обрезаясь)
      if (r.top >= BUBBLE_ROOM) setBubble({ x: Math.min(Math.max(r.left + r.width / 2, 48), window.innerWidth - 48), y: r.top - 10, side: "top" });
      else if (r.right + 90 < window.innerWidth) setBubble({ x: r.right + 4, y: r.bottom - 2, side: "right" });
      else setBubble({ x: r.left - 4, y: r.bottom - 2, side: "left" });
    }
    const hop = hopRef.current;
    if (hop) {
      hop.style.animation = "none";
      void hop.offsetWidth;
      hop.style.animation = "";
    }
    setN((v) => v + 1);
    window.clearTimeout(quackTimer.current);
    quackTimer.current = window.setTimeout(() => {
      setBubble(null);
      ctl.current?.quack(false);
    }, QUACK_MS);
  };

  const hostStyle = { width: W, height: H } as CSSProperties;
  return (
    <>
      <div ref={hostRef} className="pd" data-quack={bubble ? "" : undefined} style={hostStyle}>
        <button
          ref={btnRef}
          type="button"
          className="pd-btn"
          aria-label={t.duck.label}
          onClick={onClick}
          onPointerEnter={() => ctl.current?.hover(true)}
          onPointerLeave={() => ctl.current?.hover(false)}
          onFocus={() => ctl.current?.focus(true)}
          onBlur={() => ctl.current?.focus(false)}
        >
          <span ref={hopRef} className="pd-hop">
            <span className="pd-flip">
              <span className="pd-view">
                <canvas ref={canvasRef} className="pd-sheet" width={COLS} height={ROWS} style={{ width: W, height: H }} aria-hidden />
              </span>
            </span>
          </span>
        </button>
        <span role="status" className="sr-only">
          {bubble ? t.duck.quack : ""}
        </span>
      </div>
      {bubble &&
        createPortal(
          <span
            key={n}
            className="pd-bubble caps text-paper [text-shadow:0_1px_4px_rgb(0_0_0/0.85)]"
            data-side={bubble.side}
            style={{ left: bubble.x, top: bubble.y }}
          >
            {t.duck.quack}
          </span>,
          document.body,
        )}
    </>
  );
}
