"use client";

import { useRef } from "react";
import { CompareSplit } from "./compare-split";
import { Features } from "./features";
import { FlowSection } from "./flow-section";
import { LandingFooter } from "./footer";
import { Hero } from "./hero";
import { KineticBand } from "./kinetic-band";
import { LandingNav } from "./nav";
import { ProductSection } from "./product-section";
import { LandingStageCanvas } from "./stage/landing-stage-canvas";
import { LandingStageProvider } from "./stage/landing-context";

/**
 * Quack! landing page: unified 3D WebGL stage (giant QUACK watermark, 3/4 stippled duck,
 * radial tick field, liquid cursor wake, and 3D knowledge constellation)
 * layered cleanly beneath ultra-simple visual sections.
 */
export function LandingPage() {
  const scrollRef = useRef<HTMLElement>(null);

  return (
    <LandingStageProvider scrollRef={scrollRef}>
      <main
        ref={scrollRef}
        className="relative isolate h-full overflow-x-hidden overflow-y-auto bg-[#080504] text-[#f3eadc]"
      >
        <LandingStageCanvas />
        <LandingNav />
        <Hero />
        <KineticBand />
        <ProductSection />
        <FlowSection />
        <CompareSplit />
        <Features />
        <LandingFooter />
      </main>
    </LandingStageProvider>
  );
}
