"use client";

import Link from "next/link";
import { useEffect, useRef, type ComponentProps, type PointerEvent, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useLandingStage } from "./stage/landing-context";

type RevealProps = {
  children: ReactNode;
  className?: string;
  mode?: "rise" | "wipe";
  delayMs?: number;
};

/**
 * Scroll-triggered reveal using an unclipped outer sentinel for IntersectionObserver
 * and an inner animated element so `clip-path` wipe reveals always trigger reliably.
 */
export function Reveal({ children, className, mode = "rise", delayMs = 0 }: RevealProps) {
  const outerRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const { scrollRef } = useLandingStage();

  useEffect(() => {
    const outer = outerRef.current;
    const inner = innerRef.current;
    if (!outer || !inner) return;

    const reveal = () => {
      inner.dataset.revealed = "true";
    };

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      reveal();
      return;
    }

    const root = scrollRef.current;
    const checkInitial = () => {
      const r = outer.getBoundingClientRect();
      const limit = root ? root.getBoundingClientRect().bottom : window.innerHeight;
      if (r.top < limit && r.bottom > 0) {
        requestAnimationFrame(reveal);
        return true;
      }
      return false;
    };

    if (checkInitial()) return;

    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            reveal();
            io.disconnect();
            break;
          }
        }
      },
      { root, threshold: 0.01, rootMargin: "0px 0px -2% 0px" },
    );
    io.observe(outer);
    return () => io.disconnect();
  }, [scrollRef]);

  return (
    <div ref={outerRef} className={className}>
      <div
        ref={innerRef}
        data-reveal={mode}
        style={{ "--reveal-delay": `${delayMs}ms` } as React.CSSProperties}
        className="landing-reveal"
      >
        {children}
      </div>
    </div>
  );
}

/**
 * Subtle damped magnetic pull for primary pill links on fine-pointer desktops.
 */
export function MagneticLink({
  children,
  className,
  strength = 5,
  ...props
}: ComponentProps<typeof Link> & { strength?: number }) {
  const ref = useRef<HTMLAnchorElement>(null);

  const onMove = (e: PointerEvent<HTMLAnchorElement>) => {
    if (e.pointerType !== "mouse") return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const dx = ((e.clientX - (r.left + r.width / 2)) / Math.max(1, r.width / 2)) * strength;
    const dy = ((e.clientY - (r.top + r.height / 2)) / Math.max(1, r.height / 2)) * strength;
    el.style.transform = `translate3d(${dx.toFixed(2)}px, ${dy.toFixed(2)}px, 0)`;
  };

  const onLeave = () => {
    if (ref.current) ref.current.style.transform = "translate3d(0, 0, 0)";
  };

  return (
    <Link
      ref={ref}
      onPointerMove={onMove}
      onPointerLeave={onLeave}
      className={cn("transition-[transform,background-color,box-shadow] duration-300 ease-(--ease-out)", className)}
      {...props}
    >
      {children}
    </Link>
  );
}

/**
 * Architectural container (`6px` radius, `1px` hairline seam) with restrained warm amber pointer sheen.
 */
export function SpotlightPanel({
  children,
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  const ref = useRef<HTMLDivElement>(null);

  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (e.pointerType !== "mouse") return;
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${e.clientX - r.left}px`);
    el.style.setProperty("--my", `${e.clientY - r.top}px`);
  };

  return (
    <div
      ref={ref}
      onPointerMove={onMove}
      className={cn(
        "spotlight-panel rounded-[6px] border border-[#f1e4d6]/12 bg-[#0e0b09]/86 backdrop-blur-sm",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}
