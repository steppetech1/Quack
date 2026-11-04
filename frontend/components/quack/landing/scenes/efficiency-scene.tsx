"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { useT } from "@/lib/i18n/provider";
import { Chain, drawLink, drawNode, Lightning, makeDots, nodeSprites, type Dot } from "./effects";
import { NodeCard } from "./node-card";
import { Headline, Rise, useInView } from "./reveal";
import { DuckStage } from "./scene-duck";
import { approach, cellSize, rand, sceneForm } from "./scene-utils";
import { storyStore } from "./story";

const NODES = 5;

/**
 * Второй экран: эффективность и память. Справа крупный заголовок и пара предложений, слева летит утка и тянет в клюве
 * цепочку узлов; изредка рядом бьёт молния. На узлы можно навестись — карточка с одним из принципов платформы.
 * Графика собирается из точек, когда экран въезжает, и рассыпается, когда уезжает.
 */
export function EfficiencyScene({ scrollRef, slot }: { scrollRef: RefObject<HTMLElement | null>; slot?: number }) {
  const t = useT().landing.story.efficiency;
  const secRef = useRef<HTMLElement>(null);
  const cvRef = useRef<HTMLCanvasElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const [textRef, ioIn] = useInView<HTMLDivElement>(0.35);
  const [pinIn, setPinIn] = useState(false);
  const inView = slot !== undefined ? pinIn : ioIn; // закреплённый режим: текст выезжает, когда сцена собрана
  const [hot, setHot] = useState(-1);
  const hotRef = useRef(-1);
  const nodesText = t.nodes;

  useEffect(() => {
    const sec = secRef.current;
    const cv = cvRef.current;
    const ctx = cv?.getContext("2d");
    const scroller = scrollRef.current;
    if (!sec || !cv || !ctx || !scroller) return;
    const formOf = () => (slot !== undefined ? storyStore.forms[slot] : sceneForm(sec));
    let lastForm = 0;
    let active = false;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const stage = new DuckStage();
    if (stage.ok) {
      stage.el.style.zIndex = "1";
      sec.appendChild(stage.el);
    }
    const chain = new Chain(NODES, 100);
    const lightning = new Lightning();
    let W = 0;
    let H = 0;
    let dpr = 1;
    let duckPx = 300;
    let cell = 5;
    let nodeR = 18;
    let narrow = false;
    let dots: Dot[][] = [];
    let sprites: ReturnType<typeof nodeSprites> = [];
    const scatter = Array.from({ length: NODES }, () => {
      const a = Math.random() * Math.PI * 2;
      return { x: Math.cos(a), y: Math.sin(a) };
    });
    const heat = new Array(NODES).fill(0);
    let pointer: { x: number; y: number } | null = null;
    let duckX = 0;

    const layout = () => {
      const r = sec.getBoundingClientRect();
      if (r.width < 1) return false;
      W = r.width;
      H = r.height;
      dpr = Math.min(2, window.devicePixelRatio || 1);
      narrow = W < 900;
      cell = cellSize();
      duckPx = narrow ? W * 0.32 : Math.min(W * 0.2, H * 0.4);
      cv.width = Math.round(W * dpr);
      cv.height = Math.round(H * dpr);
      const bw = Math.min(narrow ? W : W * 1.1, duckPx * 3.8);
      const bh = Math.min(H * 0.95, duckPx * 2.3);
      const bx = narrow ? (W - bw) / 2 : W * 0.27 - bw / 2;
      const by = narrow ? H * 0.5 : (H - bh) / 2;
      duckX = bx + bw / 2;
      stage.layout({ box: { x: bx, y: by, w: bw, h: bh }, duckPx, cell, dpr, center: { x: 0, y: 0.15 } });
      nodeR = duckPx * 0.058;
      dots = Array.from({ length: NODES }, () => makeDots(nodeR, cell * 0.45));
      sprites = nodeSprites(cell * 0.45, dpr);
      lightning.layout(cell, dpr);
      chain.setRest(duckPx * 0.24);
      return true;
    };

    const k = () => duckPx / 384;
    let last = performance.now();
    const t0 = last;

    const frame = (dt: number, still: boolean) => {
      const now = still ? 2.2 : (performance.now() - t0) / 1000;
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
        stage.render(now, dt, 0, reduced);
        return;
      }
      // утка летит влево (клюв влево) и держится в кадре: плавно «дрейфует» туда-сюда в своей части экрана
      const p = stage.pose;
      p.facing = Math.PI;
      p.flight = 1;
      p.time = now;
      p.x = 0.32 * Math.sin(now * 0.45) + 0.05 * Math.sin(now * 1.3);
      p.y = 0.07 * Math.sin(now * 0.9);
      p.headPitch = 0.04 * Math.sin(now * 1.1);
      stage.render(now, dt, form, reduced);
      const tip = stage.beakTip();
      // тяжесть тянет цепочку вниз, «ветер» полёта — назад (вправо)
      chain.step(still ? 0.016 : dt, tip.x, tip.y, 260 * k(), 760 * k());

      lightning.update(now, () => {
        const side = duckX < W * 0.35 ? 1 : Math.random() < 0.5 ? 1 : -1;
        const x = duckX + side * duckPx * rand(0.55, 1.15);
        return { x: Math.max(40, x), top: 0, bottom: H * rand(0.6, 0.82) };
      });
      lightning.draw(ctx, now, dpr, form, W, H);

      // связи: первая — тонкая нить от клюва, остальные между узлами
      const pts = chain.pts;
      for (let i = 0; i < NODES; i++) {
        const a = pts[i];
        const b = pts[i + 1];
        const sag = chain.pts.length > 1 ? duckPx * 0.035 : 0;
        const al = (i === 0 ? 0.7 : 1) * form;
        drawLink(ctx, dpr, a.x, a.y, b.x, b.y, (a.x + b.x) / 2, (a.y + b.y) / 2 + sag, (i + 2) % 3, (i + 3) % 3, al);
      }
      // узлы
      for (let i = 0; i < NODES; i++) {
        heat[i] = approach(heat[i], hotRef.current === i ? 1 : 0, 10, Math.max(dt, 0.016));
        const q = pts[i + 1];
        drawNode(ctx, sprites, dots[i], { x: q.x, y: q.y, R: nodeR, color: i % 3, alpha: 1, hot: heat[i], form, scatter: scatter[i] }, now, dpr, reduced, duckPx * 0.9);
      }

      // карточка следует за наведённым узлом
      const card = cardRef.current;
      if (card) {
        const idx = hotRef.current;
        if (idx >= 0) {
          const q = pts[idx + 1];
          const cw = card.offsetWidth;
          const ch = card.offsetHeight;
          let cx = q.x + nodeR * 2.6 + 8;
          if (cx + cw > W - 8) cx = q.x - nodeR * 2.6 - 8 - cw;
          const cy = Math.min(H - ch - 8, Math.max(8, q.y - ch / 2));
          card.style.transform = `translate(${Math.max(8, cx)}px, ${cy}px)`;
        }
      }
    };

    const hit = () => {
      if (!pointer || lastForm < 0.6) return -1;
      let best = -1;
      let bd = nodeR * 2.2 + 10;
      for (let i = 0; i < NODES; i++) {
        const q = chain.pts[i + 1];
        const d = Math.hypot(q.x - pointer.x, q.y - pointer.y);
        if (d < bd) {
          bd = d;
          best = i;
        }
      }
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
    // закреплённые сцены лежат друг на друге и не ловят указатель сами — слушаем весь прокручиваемый контейнер
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
      if (pointer) setHover(hit()); // узел уплывает из-под курсора — карточка уходит вместе с ним
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
        if (slot !== undefined) return; // в закреплённом режиме видимость считается по собранности
        visible = e.isIntersecting;
        sync();
      },
      { rootMargin: "10% 0px 10% 0px" },
    );
    io.observe(sec);
    const ro = new ResizeObserver(() => {
      if (layout()) {
        chain.reset(duckX - duckPx * 0.5, H * 0.5);
        if (!raf) frame(0, true);
      }
    });
    ro.observe(sec);
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

    if (layout()) {
      chain.reset(duckX - duckPx * 0.5, H * 0.5);
      if (reduced) for (let i = 0; i < 90; i++) chain.step(0.016, duckX - duckPx * 0.5, H * 0.5, 260 * k(), 760 * k());
      frame(0, true);
    }

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
      stage.dispose();
    };
  }, [scrollRef, slot]);

  const card = hot >= 0 ? nodesText[hot] : null;

  return (
    <section ref={secRef} id="efficiency" className={`overflow-hidden ${slot !== undefined ? "pointer-events-none absolute inset-0" : "relative h-full min-h-[680px] shrink-0"}`}>
      <canvas ref={cvRef} aria-hidden className="pointer-events-none absolute inset-0 h-full w-full" />
      <div className="pointer-events-none relative z-10 mx-auto flex h-full max-w-[1600px] items-start px-5 pb-10 pt-24 sm:px-10 lg:items-center lg:justify-end lg:pt-14">
        <div ref={textRef} className="w-full lg:w-[44%] lg:max-w-[40rem]">
          <Headline lines={t.lines} accent={[1]} inView={inView} className="text-[clamp(2.25rem,4.8vw,4.5rem)]" />
          <Rise inView={inView} delay={0.45} as="p" className="mt-6 max-w-[34rem] font-sans text-[clamp(1rem,1.25vw,1.1875rem)] leading-[1.55] text-[#bba995]">
            {t.text}
          </Rise>
          <Rise inView={inView} delay={0.7} as="p" className="mt-8 font-mono text-[0.8125rem] uppercase tracking-[0.18em] text-[#8d8273]">
            {t.hint}
          </Rise>
        </div>
      </div>
      <NodeCard cardRef={cardRef} title={card?.title ?? ""} text={card?.text ?? ""} visible={hot >= 0} />
    </section>
  );
}
