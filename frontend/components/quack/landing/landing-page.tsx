"use client";

import { useRef } from "react";
import { Backdrop } from "./backdrop";
import { Compare } from "./compare";
import { Features } from "./features";
import { LandingFooter } from "./footer";
import { Hero } from "./hero";
import { LandingNav } from "./nav";
import { Pluses } from "./pluses";

/** Лендинг целиком: общий фон, панель, первый экран, сравнение, два блока плюсов и подвал. Страница прокручивается внутри этого контейнера, а не окном: у body overflow hidden */
export function LandingPage() {
  const scrollRef = useRef<HTMLElement>(null);
  return (
    <main
      ref={scrollRef}
      className="relative isolate h-full overflow-x-hidden overflow-y-auto bg-[#080504] text-[#f1e4d6] motion-safe:scroll-smooth"
    >
      <Backdrop scrollRef={scrollRef} />
      <LandingNav scrollRef={scrollRef} />
      <Hero scrollRef={scrollRef} />
      <Compare scrollRef={scrollRef} />
      <Pluses />
      <Features />
      <LandingFooter />
    </main>
  );
}
