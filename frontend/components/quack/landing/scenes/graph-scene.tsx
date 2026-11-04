"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { useT } from "@/lib/i18n/provider";
import { GlyphSprites } from "../hero-duck/glyphs";
import { drawLink, drawNode, makeDots, nodeSprites, type Dot } from "./effects";
import { NodeCard } from "./node-card";
import { Headline, Rise, useInView } from "./reveal";
import { DuckStage } from "./scene-duck";
import { approach, cellSize, rand, sceneForm } from "./scene-utils";
import { storyStore } from "./story";

/** Узлы большого графа: положение в долях рамки графа (x, y), цвет палитры, главный ли */
const NODES: { x: number; y: number; color: number; hub?: boolean }[] = [
  { x: 0.5, y: 0.36, color: 0, hub: true }, // 0 учитель
  { x: 0.17, y: 0.16, color: 1 }, // 1 материалы
  { x: 0.83, y: 0.16, color: 2 }, // 2 память
  { x: 0.3, y: 0.64, color: 1 }, // 3 задачи
  { x: 0.7, y: 0.64, color: 0 }, // 4 повторение
  { x: 0.93, y: 0.44, color: 1 }, // 5 дедлайн
  { x: 0.08, y: 0.44, color: 0 }, // 6 конспекты
  { x: 0.16, y: 0.78, color: 2 }, // 7 карточки
  { x: 0.5, y: 0.07, color: 2 }, // 8 помощники
  { x: 0.5, y: 0.7, color: 1 }, // 9 доверие
];
// порядок узлов — как в текстах (landing.story.how.nodes)
const LINKS: [number, number][] = [
  [0, 1],
  [0, 2],
  [0, 8],
  [0, 3],
  [0, 9],
  [1, 6],
  [2, 5],
  [6, 7],
  [3, 7],
  [3, 9],
  [4, 9],
  [2, 4],
  [1, 3],
  [5, 4],
];
const LOW = [3, 4, 7, 9]; // узлы, до которых дотягивается утка

type GNode = { x: number; y: number; vx: number; vy: number; hx: number; hy: number; R: number; dots: Dot[]; scatter: { x: number; y: number }; heat: number; phase: number };

/**
 * Третий экран: как работает платформа. По центру большой граф из десяти узлов (на каждый можно навестись и прочитать,
 * как устроена эта часть), под ним ходит небольшая утка и время от времени дёргает за нижние узлы — граф откликается
 * пружинами. Сверху по центру заголовок, слева и справа по короткому тексту.
 */
export function GraphScene({ scrollRef, slot }: { scrollRef: RefObject<HTMLElement | null>; slot?: number }) {
  const t = useT().landing.story.how;
  const secRef = useRef<HTMLElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const cvRef = useRef<HTMLCanvasElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const [topRef, topIo] = useInView<HTMLDivElement>(0.35);
  const [leftRef, leftIo] = useInView<HTMLDivElement>(0.35);
  const [rightRef, rightIo] = useInView<HTMLDivElement>(0.35);
  const [pinIn, setPinIn] = useState(false);
  const topIn = slot !== undefined ? pinIn : topIo; // закреплённый режим: текст выезжает, когда сцена собрана
  const leftIn = slot !== undefined ? pinIn : leftIo;
  const rightIn = slot !== undefined ? pinIn : rightIo;
  const [hot, setHot] = useState(-1);
  const hotRef = useRef(-1);
  const nodesText = t.nodes;
  const pin = slot !== undefined; // закреплённый режим: рамка ровно в окно, на узком окне тексты уходят под граф в два столбца

  useEffect(() => {
    const sec = secRef.current;
    const stageEl = stageRef.current;
    const cv = cvRef.current;
    const ctx = cv?.getContext("2d");
    const scroller = scrollRef.current;
    if (!sec || !stageEl || !cv || !ctx || !scroller) return;
    const formOf = () => (slot !== undefined ? storyStore.forms[slot] : sceneForm(sec));
    let lastForm = 0;
    let active = false;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const duck = new DuckStage();
    if (duck.ok) {
      duck.el.style.zIndex = "1";
      sec.appendChild(duck.el);
    }
    let W = 0;
    let H = 0;
    let dpr = 1;
    let cell = 5;
    let duckPx = 200;
    let box = { x: 0, y: 0, w: 0, h: 0 };
    let groundY = 0;
    let stage = { x: 0, y: 0, w: 0, h: 0 };
    let nodes: GNode[] = [];
    let sprites: ReturnType<typeof nodeSprites> = [];
    let groundSprite: GlyphSprites | null = null;
    let font = "system-ui";
    let unit = 0.01; // мировых единиц на CSS px у утки
    let pointer: { x: number; y: number } | null = null;

    // --- поведение утки ---
    type Phase = "idle" | "approach" | "grab" | "tug";
    let phase: Phase = "idle";
    let phaseT = 0;
    let target = -1;
    let walkGoal = 0; // куда идёт в idle, CSS px сцены
    let nextTug = 4;
    let facingGoal = 0;
    let alignT = 0;
    let held = -1;

    const layout = () => {
      const r = sec.getBoundingClientRect();
      const sr = stageEl.getBoundingClientRect();
      if (r.width < 1 || sr.width < 1) return false;
      W = r.width;
      H = r.height;
      dpr = Math.min(2, window.devicePixelRatio || 1);
      cell = cellSize();
      stage = { x: sr.left - r.left, y: sr.top - r.top, w: sr.width, h: sr.height };
      cv.width = Math.round(W * dpr);
      cv.height = Math.round(H * dpr);
      font = getComputedStyle(sec).fontFamily;

      const base = Math.min(34, Math.max(16, stage.h * 0.055));
      const gw = stage.w;
      const gh = stage.h;
      nodes = NODES.map((n, i) => {
        const R = base * (n.hub ? 1.45 : 1);
        const hx = stage.x + n.x * gw;
        const hy = stage.y + n.y * gh * 0.97;
        const old = nodes[i];
        return {
          x: old ? hx : hx,
          y: old ? hy : hy,
          vx: 0,
          vy: 0,
          hx,
          hy,
          R,
          dots: makeDots(R, cell * 0.45),
          scatter: old?.scatter ?? { x: Math.cos(i * 2.4), y: Math.sin(i * 2.4) },
          heat: 0,
          phase: i * 1.7,
        };
      });
      sprites = nodeSprites(cell * 0.45, dpr);
      groundSprite = new GlyphSprites(cell * 0.45 * dpr, "muted", true);

      duckPx = Math.min(250, Math.max(150, stage.h * 0.3));
      unit = 3.4 / duckPx;
      const bh = duckPx * 1.9;
      box = { x: stage.x, y: stage.y + stage.h - bh, w: stage.w, h: bh };
      groundY = box.y + box.h * 0.93;
      // мировая высота центра рамки: земля утки приходится на groundY
      const GROUND = -0.82;
      const cy = GROUND + 0.43 * box.h * unit;
      duck.layout({ box, duckPx, cell: cell * 0.75, dpr, center: { x: 0, y: cy } });
      const p = duck.pose;
      p.x = 0;
      walkGoal = box.x + box.w / 2;
      return true;
    };

    const fromWorldX = (wx: number) => box.x + box.w / 2 + wx / unit;

    const duckUpdate = (now: number, dt: number) => {
      const p = duck.pose;
      const goal = { facing: facingGoal, stretch: 0.15, pitch: 0, rate: 3.2, walk: 0 };
      const tip = duck.beakTip();
      const x = fromWorldX(p.x);
      phaseT += dt;
      if (phase === "idle") {
        const err = walkGoal - x;
        if (Math.abs(err) > 12) {
          facingGoal = err > 0 ? 0 : Math.PI;
          goal.walk = 1;
          // идёт, когда уже смотрит в нужную сторону
          if (Math.abs(Math.cos(p.facing) - Math.cos(facingGoal)) < 0.35) p.x += Math.sign(err) * Math.min(1.1, Math.abs(err) * unit * 3) * dt;
          else goal.walk = 0.2;
        } else if (phaseT > 1.6) {
          walkGoal = box.x + box.w * rand(0.12, 0.88);
          phaseT = 0;
        }
        goal.pitch = 0.05 * Math.sin(now * 0.9);
        if (now > nextTug) {
          // выбрать нижний узел и подойти под него
          target = LOW[Math.floor(Math.random() * LOW.length)];
          phase = "approach";
          phaseT = 0;
          alignT = 0;
        }
      } else if (phase === "approach") {
        const n = nodes[target];
        // клюв должен оказаться под узлом: подгоняем положение утки по ошибке клюва
        const err = n.x - tip.x;
        facingGoal = err > 0 ? 0 : Math.PI;
        goal.stretch = 0.7;
        goal.pitch = 0.3;
        goal.walk = Math.abs(err) > 12 ? 1 : 0;
        if (Math.abs(err) > 12 && Math.abs(Math.cos(p.facing) - Math.cos(facingGoal)) < 0.35) {
          p.x += Math.sign(err) * Math.min(1.1, Math.abs(err) * unit * 3) * dt;
          alignT = 0;
        } else if (Math.abs(err) <= 12) alignT += dt;
        if (alignT > 0.25 || phaseT > 8) {
          phase = "grab";
          phaseT = 0;
          held = target;
        }
      } else if (phase === "grab") {
        goal.stretch = 1;
        goal.pitch = 0.55;
        goal.rate = 4;
        if (phaseT > 0.7) {
          phase = "tug";
          phaseT = 0;
        }
      } else {
        // рывок: голова резко вниз и назад, узел тянется за клювом, потом отпускается
        goal.stretch = 0.2;
        goal.pitch = -0.3;
        goal.rate = 4;
        if (phaseT > 0.55) {
          const n = nodes[held];
          if (n) {
            n.vy -= duckPx * 5; // отпущенный узел подпрыгивает
            n.vx += (Math.random() - 0.5) * duckPx * 3;
          }
          held = -1;
          phase = "idle";
          phaseT = 0;
          nextTug = now + rand(5, 8);
          walkGoal = box.x + box.w * rand(0.12, 0.88);
        }
      }
      // поза
      const da = ((facingGoal - p.facing + Math.PI * 3) % (Math.PI * 2)) - Math.PI;
      p.facing += da * (1 - Math.exp(-goal.rate * dt));
      p.walk = approach(p.walk, goal.walk, 7, dt);
      if (p.walk > 0.02) p.step = (p.step + dt / 0.8) % 1;
      p.stretch = approach(p.stretch, goal.stretch, goal.rate + 1, dt);
      p.headPitch = approach(p.headPitch, goal.pitch, 8, dt);
      p.time = now;
      p.flight = 0;
    };

    const physics = (dt: number, tip: { x: number; y: number } | null) => {
      const sub = dt > 0.025 ? 2 : 1;
      const h = dt / sub;
      for (let s = 0; s < sub; s++) {
        for (let i = 0; i < nodes.length; i++) {
          const n = nodes[i];
          // дом: лёгкое парение
          const tx = n.hx + Math.sin(nowRef * 0.6 + n.phase) * 4;
          const ty = n.hy + Math.cos(nowRef * 0.5 + n.phase * 1.3) * 4;
          let fx = (tx - n.x) * 7 - n.vx * 2.2;
          let fy = (ty - n.y) * 7 - n.vy * 2.2;
          if (held === i && tip) {
            fx += (tip.x - n.x) * 90 - n.vx * 12;
            fy += (tip.y - n.R * 0.4 - n.y) * 90 - n.vy * 12;
          }
          n.vx += fx * h;
          n.vy += fy * h;
        }
        // связи тянут соседей друг к другу: когда дёргают один узел, граф откликается
        for (const [a, b] of LINKS) {
          const A = nodes[a];
          const B = nodes[b];
          const dx = B.x - A.x;
          const dy = B.y - A.y;
          const d = Math.hypot(dx, dy) || 1;
          const rest = Math.hypot(B.hx - A.hx, B.hy - A.hy);
          const f = (d - rest) * 5;
          const ux = dx / d;
          const uy = dy / d;
          A.vx += ux * f * h;
          A.vy += uy * f * h;
          B.vx -= ux * f * h;
          B.vy -= uy * f * h;
        }
        for (const n of nodes) {
          n.x += n.vx * h;
          n.y += n.vy * h;
        }
      }
    };

    let nowRef = 0;
    let last = performance.now();
    const t0 = last;

    const frame = (dt: number, still: boolean) => {
      const now = still ? 2 : (performance.now() - t0) / 1000;
      nowRef = now;
      const form = formOf();
      lastForm = form;
      if (slot !== undefined) {
        const a = form > 0.8 ? true : form < 0.6 ? false : active; // текст уходит, как только сцена начала рассыпаться
        if (a !== active) {
          active = a;
          setPinIn(a);
        }
      }
      if (form < 0.3 && hotRef.current >= 0) setHover(-1);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cv.width, cv.height);
      if (form <= 0.005) {
        duck.render(now, dt, 0, reduced);
        return;
      }
      if (!still) duckUpdate(now, dt);
      duck.render(now, dt, form, reduced);
      const tip = still ? null : duck.beakTip();
      if (!still) physics(dt, tip);

      // линия земли: редкие тусклые точки, по краям гаснут
      if (groundSprite) {
        for (let x = stage.x; x < stage.x + stage.w; x += cell * 1.9) {
          const e = Math.min(1, Math.min(x - stage.x, stage.x + stage.w - x) / (stage.w * 0.12));
          ctx.globalAlpha = 0.3 * e * form;
          groundSprite.draw(ctx, "dot", 4, x * dpr, groundY * dpr);
        }
        ctx.globalAlpha = 1;
      }

      // связи
      for (const [a, b] of LINKS) {
        const A = nodes[a];
        const B = nodes[b];
        const sag = Math.hypot(B.x - A.x, B.y - A.y) * 0.06;
        const hotLink = hotRef.current === a || hotRef.current === b;
        drawLink(ctx, dpr, A.x, A.y, B.x, B.y, (A.x + B.x) / 2, (A.y + B.y) / 2 + sag, NODES[a].color, NODES[b].color, form * (hotLink ? 1 : 0.7));
      }
      // узлы и подписи
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        n.heat = approach(n.heat, hotRef.current === i ? 1 : 0, 10, Math.max(dt, 0.016));
        drawNode(ctx, sprites, n.dots, { x: n.x, y: n.y, R: n.R, color: NODES[i].color, alpha: 1, hot: n.heat, form, scatter: n.scatter }, now, dpr, reduced, stage.w * 0.35);
        const al = (0.6 + 0.4 * n.heat) * form * form;
        if (al > 0.02) {
          ctx.globalAlpha = al;
          ctx.fillStyle = n.heat > 0.5 ? "#f1e4d6" : "#bba995";
          ctx.font = `500 ${Math.round(12 * dpr)}px ${font}`;
          ctx.fillText(nodesText[i].label, n.x * dpr, (n.y + n.R * 1.7) * dpr);
        }
      }
      ctx.globalAlpha = 1;

      const card = cardRef.current;
      const idx = hotRef.current;
      if (card && idx >= 0) {
        const n = nodes[idx];
        const cw = card.offsetWidth;
        const ch = card.offsetHeight;
        let cx = n.x + n.R * 2.4 + 10;
        if (cx + cw > W - 8) cx = n.x - n.R * 2.4 - 10 - cw;
        const cy = Math.min(H - ch - 8, Math.max(8, n.y - ch / 2));
        card.style.transform = `translate(${Math.max(8, cx)}px, ${cy}px)`;
      }
    };

    const hit = () => {
      if (!pointer || lastForm < 0.6) return -1;
      let best = -1;
      let bd = Infinity;
      nodes.forEach((n, i) => {
        const d = Math.hypot(n.x - pointer!.x, n.y - pointer!.y);
        if (d < n.R * 2.1 + 8 && d < bd) {
          bd = d;
          best = i;
        }
      });
      return best;
    };
    const setHover = (i: number) => {
      if (i === hotRef.current) return;
      hotRef.current = i;
      setHot(i);
    };
    const onMove = (e: PointerEvent) => {
      const r = sec.getBoundingClientRect();
      pointer = { x: e.clientX - r.left, y: e.clientY - r.top };
      setHover(hit());
    };
    const onLeave = () => {
      pointer = null;
      setHover(-1);
    };
    const evt: HTMLElement = slot !== undefined ? scroller : sec;
    evt.addEventListener("pointermove", onMove);
    evt.addEventListener("pointerdown", onMove);
    evt.addEventListener("pointerleave", onLeave);

    let raf = 0;
    let visible = false;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const n = performance.now();
      const dt = Math.min(0.05, (n - last) / 1000);
      last = n;
      frame(dt, false);
      if (pointer) setHover(hit());
    };
    const sync = () => {
      const run = visible && !document.hidden && !reduced;
      if (run && !raf) {
        last = performance.now();
        raf = requestAnimationFrame(tick);
      }
      if (!run && raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
    };
    const io = new IntersectionObserver(
      ([e]) => {
        if (slot !== undefined) return;
        visible = e.isIntersecting;
        sync();
      },
      { rootMargin: "10% 0px 10% 0px" },
    );
    io.observe(sec);
    const ro = new ResizeObserver(() => {
      if (layout() && !raf) frame(0, true);
    });
    ro.observe(sec);
    ro.observe(stageEl);
    const onVis = () => sync();
    document.addEventListener("visibilitychange", onVis);
    const onScroll = () => {
      if (slot !== undefined) {
        const v = storyStore.forms[slot] > 0.002;
        if (v !== visible) {
          visible = v;
          sync();
          if (!v) frame(0, true); // сцена ушла: один раз стираем холсты и прячем текст, дальше кадры не считаем
        }
      }
      if (reduced && !raf) frame(0, true);
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    scroller.addEventListener("story-form", onScroll);
    if (slot !== undefined) visible = storyStore.forms[slot] > 0.002;

    if (layout()) frame(0, true);

    return () => {
      if (raf) cancelAnimationFrame(raf);
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVis);
      scroller.removeEventListener("scroll", onScroll);
      scroller.removeEventListener("story-form", onScroll);
      evt.removeEventListener("pointermove", onMove);
      evt.removeEventListener("pointerdown", onMove);
      evt.removeEventListener("pointerleave", onLeave);
      duck.dispose();
    };
  }, [scrollRef, nodesText, slot]);

  const card = hot >= 0 ? nodesText[hot] : null;

  return (
    <section ref={secRef} id="how" className={`overflow-hidden font-sans ${slot !== undefined ? "pointer-events-none absolute inset-0" : "relative min-h-full shrink-0 lg:h-full lg:min-h-[760px]"}`}>
      <canvas ref={cvRef} aria-hidden className="pointer-events-none absolute inset-0 h-full w-full" />
      <div className={`pointer-events-none relative z-10 mx-auto flex max-w-[1600px] flex-col items-center px-5 pt-20 sm:px-10 lg:h-full lg:pb-8 lg:pt-16 ${pin ? "h-full pb-6" : "min-h-full pb-10"}`}>
        <div ref={topRef} className="text-center">
          <Rise inView={topIn} as="p" className="font-mono text-[0.8125rem] uppercase tracking-[0.18em] text-[#8d8273]">
            {t.eyebrow}
          </Rise>
          <Headline lines={t.lines} accent={[1]} inView={topIn} delay={0.1} className="mt-3 text-[clamp(1.875rem,3.6vw,3.25rem)]" />
          <Rise inView={topIn} delay={0.55} as="p" className="mx-auto mt-4 max-w-[30rem] text-[clamp(0.9375rem,1.1vw,1.0625rem)] leading-[1.5] text-[#bba995]">
            {t.sub}
          </Rise>
        </div>
        <div className={`mt-4 grid min-h-0 w-full flex-1 items-center gap-x-6 gap-y-3 lg:grid-cols-[minmax(0,16rem)_minmax(0,1fr)_minmax(0,16rem)] lg:grid-rows-1 lg:gap-8 ${pin ? "grid-cols-2 grid-rows-[minmax(0,1fr)_auto]" : "gap-y-6"}`}>
          <div ref={leftRef} className="order-2 lg:order-1">
            <Rise inView={leftIn} as="p" className="font-sans text-[1.0625rem] font-semibold tracking-[-0.01em] text-[#f1e4d6]">
              {t.left.title}
            </Rise>
            <Rise inView={leftIn} delay={0.15} as="p" className="mt-2 text-[0.9375rem] leading-[1.55] text-[#bba995]">
              {t.left.text}
            </Rise>
          </div>
          <div ref={stageRef} className={`order-1 w-full lg:order-2 lg:col-span-1 lg:h-full lg:min-h-[28rem] ${pin ? "col-span-2 h-full min-h-0" : "h-[26rem]"}`} />
          <div ref={rightRef} className="order-3 lg:text-right">
            <Rise inView={rightIn} as="p" className="font-sans text-[1.0625rem] font-semibold tracking-[-0.01em] text-[#f1e4d6]">
              {t.right.title}
            </Rise>
            <Rise inView={rightIn} delay={0.15} as="p" className="mt-2 text-[0.9375rem] leading-[1.55] text-[#bba995]">
              {t.right.text}
            </Rise>
          </div>
        </div>
        <Rise inView={topIn} delay={0.9} as="p" className="mt-2 font-mono text-[0.75rem] uppercase tracking-[0.18em] text-[#8d8273]">
          {t.hint}
        </Rise>
      </div>
      <NodeCard cardRef={cardRef} title={card?.label ?? ""} text={card?.text ?? ""} visible={hot >= 0} />
    </section>
  );
}
