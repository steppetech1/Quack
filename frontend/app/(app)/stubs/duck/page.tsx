import type { Metadata } from "next";
import { DuckDemo } from "@/components/quack/scanline/duck-demo";

export const metadata: Metadata = { title: "Duck demo" };

export default function DuckDemoRoute() {
  return <DuckDemo />;
}
