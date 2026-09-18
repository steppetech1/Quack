import { copy } from "@/components/home/copy";
import { Closing } from "@/components/home/Closing";
import { CursorAura } from "@/components/home/CursorAura";
import { HeroLandscape } from "@/components/home/HeroLandscape";
import { GlobeSection } from "@/components/home/GlobeSection";
import { HeroTitle } from "@/components/home/HeroTitle";
import { LayerStack } from "@/components/home/LayerStack";
import { PageSky } from "@/components/home/PageSky";
import { JourneySection } from "@/components/home/JourneySection";
import { Roadmap } from "@/components/home/Roadmap";
import { SiteFooter } from "@/components/home/SiteFooter";
import { SiteHeader } from "@/components/home/SiteHeader";
import styles from "@/components/home/home.module.css";
import { TransitionLink } from "@/components/transition/TransitionLink";

export default function HomePage() {
  const t = copy.hero;

  return (
    <div id="top" className={styles.page}>
      {/* Fixed behind everything: one sky for the whole page, and the aura that
          trails the cursor under the content. */}
      <PageSky />
      <CursorAura />

      <div className={styles.content}>
        <SiteHeader />

        <main className={styles.hero}>
          <HeroLandscape />

          <HeroTitle />

          <Roadmap />

          <TransitionLink className={styles.cta} href="/choice" data-aura>
            {t.cta}
          </TransitionLink>

          <p className={styles.subtitle}>
            {t.subtitleBefore}
            <span className={styles.accentSoft}>{t.subtitleAccent}</span>
            {t.subtitleAfter}
          </p>

          <span className={styles.scrollHint} aria-hidden="true">
            {t.scrollHint}
            <i className={styles.scrollArrow} />
          </span>
        </main>

        <JourneySection />

        <GlobeSection />

        <LayerStack />

        <Closing />

        <SiteFooter />
      </div>
    </div>
  );
}
