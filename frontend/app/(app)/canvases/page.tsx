import { redirect } from "next/navigation";

export default async function CanvasesRoute({ searchParams }: { searchParams: Promise<{ domain?: string }> }) {
  const { domain } = await searchParams;
  redirect(domain ? `/?view=canvases&domain=${encodeURIComponent(domain)}` : "/?view=canvases");
}
