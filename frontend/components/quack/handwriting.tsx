import { cn } from "@/lib/utils";

export const hashSeed = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 997, 7);

/** Строка от руки: живой шрифт, у каждой строки свой едва заметный наклон и сдвиг (детерминированно) */
export function Hand({ text, seed, className, tilt = 1 }: { text: string; seed: number; className?: string; tilt?: number }) {
  const rot = (((seed * 37) % 13) - 6) * 0.1 * tilt; // −0.6…0.6°
  const dx = (((seed * 11) % 9) - 4) * tilt; // −4…4 px
  return (
    <span
      className={cn("inline-block font-hand font-medium", className)}
      style={{ transform: `translateX(${dx}px) rotate(${rot}deg)`, transformOrigin: "left center" }}
    >
      {text}
    </span>
  );
}
