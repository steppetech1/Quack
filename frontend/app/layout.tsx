import type { Metadata, Viewport } from "next";
import { Suspense } from "react";
import { Archivo, Caveat, JetBrains_Mono, Manrope } from "next/font/google";
import "katex/dist/katex.min.css";
import "./globals.css";
import { getDict, getLocale } from "@/lib/i18n/server";
import { LocaleProvider } from "@/lib/i18n/provider";

// Основной шрифт: текст и заголовки
const manrope = Manrope({
  variable: "--font-manrope",
  subsets: ["latin", "cyrillic"],
  weight: ["400", "500", "600", "700"],
});

const jetbrains = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin", "cyrillic"],
  weight: ["400", "500", "700", "800"],
});

// Широкий гротеск (Archivo Expanded SemiBold) для заголовка первого экрана лендинга.
// Только латиница: заголовок «Quack! — Learning Hub» одинаковый на обоих языках
const archivo = Archivo({
  variable: "--font-archivo",
  subsets: ["latin"],
  axes: ["wdth"],
});

const caveat = Caveat({
  variable: "--font-caveat",
  subsets: ["latin", "cyrillic"],
  weight: ["400", "500", "600"],
});

export async function generateMetadata(): Promise<Metadata> {
  const t = getDict(await getLocale());
  return { title: "Quack!", description: t.meta.description };
}

export const viewport: Viewport = {
  themeColor: "#141311",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  // The language comes from a cookie, so it is request-bound: everything that depends on it sits behind one Suspense
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${manrope.variable} ${jetbrains.variable} ${caveat.variable} ${archivo.variable} h-full antialiased`}
    >
      <body className="h-full overflow-hidden">
        {/* фильтр рваного края для утилиты text-striped */}
        <svg aria-hidden="true" focusable="false" width="0" height="0" className="pointer-events-none absolute">
          <filter id="quack-ragged" x="-2%" y="-10%" width="104%" height="120%" colorInterpolationFilters="sRGB">
            <feTurbulence type="fractalNoise" baseFrequency="0.9 0.06" numOctaves={2} seed={3} result="noise" />
            <feDisplacementMap in="SourceGraphic" in2="noise" scale={5} xChannelSelector="R" yChannelSelector="G" />
          </filter>
        </svg>
        <Suspense fallback={null}>
          <Localized>{children}</Localized>
        </Suspense>
      </body>
    </html>
  );
}

async function Localized({ children }: { children: React.ReactNode }) {
  const locale = await getLocale();
  return (
    <>
      {/* sets <html lang> before paint; the provider keeps it in sync after a switch */}
      <script dangerouslySetInnerHTML={{ __html: `document.documentElement.lang=${JSON.stringify(locale)}` }} />
      <LocaleProvider initial={locale}>{children}</LocaleProvider>
    </>
  );
}
