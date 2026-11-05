import { Duck } from "../duck";
import { cn } from "@/lib/utils";

/** Маленькая утка Quack! в короне: сидит на верхней кромке карточки. Корона лежит поверх головы утки (центр головы — 78,32 во viewBox 120×96) */
export function CrownDuck({ className }: { className?: string }) {
  return (
    <div className={cn("relative aspect-[120/96]", className)}>
      <Duck className="size-full" />
      <svg viewBox="0 0 120 96" className="absolute inset-0 size-full" aria-hidden>
        <g transform="rotate(-10 78 20)">
          <path
            d="M68 22 L66.5 8 L73 14 L78 5.5 L83 14 L89.5 8 L88 22 Z"
            fill="#ff8a2a"
            stroke="#080504"
            strokeWidth="1.2"
            strokeLinejoin="round"
          />
          <path d="M68.4 18.6 H87.6" stroke="#080504" strokeWidth="1" opacity="0.55" />
          <circle cx="66.5" cy="8" r="1.5" fill="#f6eadc" />
          <circle cx="78" cy="5.5" r="1.5" fill="#f6eadc" />
          <circle cx="89.5" cy="8" r="1.5" fill="#f6eadc" />
        </g>
      </svg>
    </div>
  );
}
