import type { Metadata } from "next";
import { ScanlineDemo } from "@/components/quack/scanline/scanline-demo";

export const metadata: Metadata = { title: "Scanline demo" };

export default function ScanlineDemoRoute() {
  return <ScanlineDemo />;
}
