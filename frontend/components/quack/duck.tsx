import { cn } from "@/lib/utils";

/**
 * Утка Quack!: геометрический силуэт в профиль, бумажного цвета,
 * клюв — охра. Одна крупная утка на экран.
 */
export function Duck({ className, title }: { className?: string; title?: string }) {
  return (
    <svg
      viewBox="0 0 120 96"
      className={cn("shrink-0", className)}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      <path
        d="M12 54 Q8 40 21 44 Q30 48 40 50 Q54 42 72 45 Q88 48 90 64 Q91 82 66 85 L36 85 Q14 83 12 54 Z"
        fill="var(--duck)"
      />
      <circle cx="78" cy="32" r="16" fill="var(--duck)" />
      <path d="M91 28 Q109 29 112 35 Q106 41 90 39 Z" className="fill-ribbon" />
      <circle cx="82.5" cy="28" r="2.4" fill="var(--cloth)" />
      <circle cx="82.5" cy="28" r="6.2" fill="none" stroke="var(--cloth)" strokeWidth="1.8" />
      <path d="M76.3 27.4 Q70 25 64 27.5" fill="none" stroke="var(--cloth)" strokeWidth="1.8" strokeLinecap="round" />
      <path
        d="M38 62 Q54 55 70 63 Q60 73 43 70"
        fill="none"
        stroke="var(--rule)"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
    </svg>
  );
}
