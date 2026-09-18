"use client";

import { useEffect, useRef, useState } from "react";
import { DuckLane } from "./DuckLane";
import { Globe } from "./Globe";
import styles from "./globe-section.module.css";
import { Reveal } from "./Reveal";

/**
 * The globe of countries we have universities in, with a lane of ducks down
 * each side. The ducks only fly while the section is on screen.
 */
export function GlobeSection() {
  const ref = useRef<HTMLElement>(null);
  const [onScreen, setOnScreen] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver((entries) => setOnScreen(entries.some((e) => e.isIntersecting)), {
      threshold: 0.2,
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <section ref={ref} className={styles.section} id="globe">
      <Reveal className={styles.globeWrap}>
        <Globe />
      </Reveal>

      <DuckLane active={onScreen} side="left" firstDelay={4200} />
      <DuckLane active={onScreen} side="right" />
    </section>
  );
}
