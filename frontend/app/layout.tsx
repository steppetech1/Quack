import type { Metadata, Viewport } from "next";
import { Suspense } from "react";
import { Caveat, Golos_Text, JetBrains_Mono, PT_Serif } from "next/font/google";
import "katex/dist/katex.min.css";
import "./globals.css";
import { getDict, getLocale } from "@/lib/i18n/server";
import { LocaleProvider } from "@/lib/i18n/provider";

const golos = Golos_Text({
  variable: "--font-golos",
  subsets: ["latin", "cyrillic"],
  weight: ["400", "500", "600"],
});

const jetbrains = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin", "cyrillic"],
  weight: ["400", "500"],
});

const caveat = Caveat({
  variable: "--font-caveat",
  subsets: ["latin", "cyrillic"],
  weight: ["400", "500", "600"],
});

const ptSerif = PT_Serif({
  variable: "--font-pt-serif",
  subsets: ["latin", "cyrillic"],
  weight: ["700"],
});

export async function generateMetadata(): Promise<Metadata> {
  const t = getDict(await getLocale());
  return { title: "Quack!", description: t.meta.description };
}

export const viewport: Viewport = {
  themeColor: "#1c1915",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  // The language comes from a cookie, so it is request-bound: everything that depends on it sits behind one Suspense
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${golos.variable} ${jetbrains.variable} ${ptSerif.variable} ${caveat.variable} h-full antialiased`}
    >
      <body className="h-full overflow-hidden">
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
