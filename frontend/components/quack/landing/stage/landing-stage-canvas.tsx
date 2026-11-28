"use client";

import { useEffect, useRef } from "react";
import { Duck } from "../../duck";
import { useLandingStage } from "./landing-context";
import { createLandingStage, type LandingStage } from "./landing-stage";

const SECTION_IDS = ["top", "flow", "compare", "tools"] as const;

export function LandingStageCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const {
    scrollRef,
    stateRef,
    setActiveSection,
    emitAnchor,
    emitProgress,
    registerStageTrigger,
  } = useLandingStage();

  useEffect(() => {
    const canvas = canvasRef.current;
    const root = scrollRef.current;
    if (!canvas || !root) return;

    let stage: LandingStage;
    try {
      stage = createLandingStage(canvas, stateRef, emitAnchor);
    } catch {
      canvas.dataset.flat = "";
      return;
    }

    registerStageTrigger(() => stage.triggerQuack());

    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    let reduced = mq.matches;
    let raf = 0;
    const pointer = { x: 0, y: 0, active: false };
    const t0 = performance.now();
    let lastT = t0;
    let lastActiveId = "top";
    let lastSectionCheck = 0;

    const readScroll = () => {
      const top = root.scrollTop;
      const ch = Math.max(1, root.clientHeight);
      const maxScroll = Math.max(1, root.scrollHeight - ch);
      const st = stateRef.current;
      st.scrollTop = top;
      st.clientHeight = ch;
      st.scrollHeight = root.scrollHeight;
      st.scrollProgress = Math.min(1, Math.max(0, top / maxScroll));
      st.scrollVh = top / ch;
    };

    const detectActiveSection = (nowMs: number) => {
      if (nowMs - lastSectionCheck < 90) return;
      lastSectionCheck = nowMs;
      const rootTop = root.getBoundingClientRect().top;
      const probeY = rootTop + root.clientHeight * 0.36;
      let current = "top";
      for (const id of SECTION_IDS) {
        const el = document.getElementById(id);
        if (!el) continue;
        if (el.getBoundingClientRect().top <= probeY) {
          current = id;
        }
      }
      if (current !== lastActiveId) {
        lastActiveId = current;
        setActiveSection(current);
      }
    };

    const stepAndRender = (nowMs: number, staticDraw = false) => {
      readScroll();
      const st = stateRef.current;
      const dt = staticDraw ? 0 : Math.min(0.05, Math.max(0.0001, (nowMs - lastT) / 1000));
      lastT = nowMs;

      if (reduced || staticDraw) {
        st.smoothScrollVh = st.scrollVh;
        st.smoothScrollProgress = st.scrollProgress;
        st.velocity = 0;
      } else {
        const prevSmoothVh = st.smoothScrollVh;
        const k = 1 - Math.exp(-10.5 * dt);
        st.smoothScrollVh += (st.scrollVh - st.smoothScrollVh) * k;
        st.smoothScrollProgress += (st.scrollProgress - st.smoothScrollProgress) * k;
        const rawVel = (st.smoothScrollVh - prevSmoothVh) / Math.max(dt, 0.008);
        st.velocity += (rawVel - st.velocity) * (1 - Math.exp(-8 * dt));
      }

      emitProgress(st.smoothScrollProgress, st.scrollTop);
      detectActiveSection(nowMs);

      const sec = reduced ? 2.4 : (nowMs - t0) / 1000;
      stage.render(sec, dt, pointer.x, pointer.y, pointer.active, reduced);
    };

    const tick = () => {
      raf = requestAnimationFrame(tick);
      stepAndRender(performance.now(), false);
    };

    const syncLoop = () => {
      const shouldRun = !document.hidden && !reduced;
      if (shouldRun && !raf) {
        lastT = performance.now();
        raf = requestAnimationFrame(tick);
      }
      if (!shouldRun && raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
    };

    const ro = new ResizeObserver(() => {
      const r = canvas.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return;
      stage.resize(r.width, r.height, window.devicePixelRatio);
      if (!raf) stepAndRender(performance.now(), true);
    });
    ro.observe(canvas);

    const onScroll = () => {
      if (!raf) stepAndRender(performance.now(), true);
    };
    const onMove = (e: PointerEvent) => {
      if (e.pointerType !== "mouse" && e.pointerType !== "pen") return;
      pointer.x = (e.clientX / Math.max(1, window.innerWidth)) * 2 - 1;
      pointer.y = 1 - (e.clientY / Math.max(1, window.innerHeight)) * 2;
      pointer.active = true;
    };
    const onLeave = () => {
      pointer.active = false;
    };
    const onVis = () => syncLoop();
    const onMotion = () => {
      reduced = mq.matches;
      syncLoop();
      if (reduced) stepAndRender(performance.now(), true);
    };

    root.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("pointermove", onMove, { passive: true });
    document.documentElement.addEventListener("pointerleave", onLeave);
    document.addEventListener("visibilitychange", onVis);
    mq.addEventListener("change", onMotion);

    const r = canvas.getBoundingClientRect();
    if (r.width >= 1 && r.height >= 1) {
      stage.resize(r.width, r.height, window.devicePixelRatio);
    }
    stepAndRender(performance.now(), true);
    syncLoop();

    return () => {
      registerStageTrigger(null);
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      root.removeEventListener("scroll", onScroll);
      window.removeEventListener("pointermove", onMove);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", onVis);
      mq.removeEventListener("change", onMotion);
      stage.dispose();
    };
  }, [scrollRef, stateRef, setActiveSection, emitAnchor, emitProgress, registerStageTrigger]);

  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-[#080504]">
      <canvas ref={canvasRef} className="peer block size-full data-flat:hidden" />
      <div className="hidden size-full peer-data-flat:block">
        <div className="absolute inset-0 bg-[radial-gradient(60%_45%_at_50%_75%,rgba(237,185,102,0.12)_0%,rgba(8,5,4,0)_100%)]" />
        <p
          aria-hidden
          className="text-striped pointer-events-none absolute top-[34%] left-1/2 -translate-x-1/2 -translate-y-1/2 font-[family-name:var(--font-archivo)] text-[clamp(4.5rem,24vw,18rem)] font-bold leading-none tracking-[-0.05em] text-[#edb966]/14 uppercase select-none"
        >
          QUACK
        </p>
        <div className="absolute bottom-14 left-1/2 -translate-x-1/2 opacity-90">
          <Duck className="w-56 text-[#edb966]" />
        </div>
      </div>
    </div>
  );
}
