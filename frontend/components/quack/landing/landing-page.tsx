"use client";

import { useRef } from "react";
import { LandingFooter } from "./footer";
import { Hero } from "./hero";
import { LandingNav } from "./nav";
import { EfficiencyScene } from "./scenes/efficiency-scene";
import { GraphScene } from "./scenes/graph-scene";
import { STORY, usePinned } from "./scenes/story";
import { useStoryController } from "./scenes/story-controller";

/**
 * Лендинг: панель, первый экран, две сцены и подвал. На большом экране сцены закреплены: первый экран и обе сцены лежат
 * друг на друге в неподвижной рамке, а прокрутка рассыпает одну сцену на месте и собирает следующую (см. scenes/story.ts).
 * На телефоне — обычная прокрутка, сцены друг под другом. Прежние блоки и фон с графами пока не показываются (файлы остались).
 * Страница прокручивается внутри этого контейнера, а не окном: у body overflow hidden
 */
export function LandingPage() {
  const scrollRef = useRef<HTMLElement>(null);
  const pinned = usePinned();
  useStoryController(scrollRef, pinned);
  return (
    <main
      ref={scrollRef}
      data-pinned={pinned}
      className={`relative isolate h-full overflow-x-hidden overflow-y-auto bg-[#080504] text-[#f1e4d6] ${pinned ? "" : "motion-safe:scroll-smooth"}`}
    >
      <LandingNav scrollRef={scrollRef} />
      {pinned ? (
        <div className="relative" style={{ height: `${(STORY.end + 1) * 100}dvh` }}>
          <div className="sticky top-0 h-dvh overflow-hidden">
            <Hero scrollRef={scrollRef} pinned />
            <EfficiencyScene scrollRef={scrollRef} slot={1} />
            <GraphScene scrollRef={scrollRef} slot={2} />
          </div>
        </div>
      ) : (
        <>
          <Hero scrollRef={scrollRef} />
          <EfficiencyScene scrollRef={scrollRef} />
          <GraphScene scrollRef={scrollRef} />
        </>
      )}
      <LandingFooter />
    </main>
  );
}
