"use client";

import { useSyncExternalStore } from "react";

/** Общая кривая easing из REDESIGN (ease-out) */
export const EASE_OUT = "cubic-bezier(0.16, 1, 0.3, 1)";

const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";

function subscribeReducedMotion(onChange: () => void) {
  // на всякий случай: старые браузеры без matchMedia
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  const mql = window.matchMedia(REDUCED_QUERY);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

function getReducedMotionSnapshot() {
  return window.matchMedia(REDUCED_QUERY).matches;
}

function getServerReducedMotionSnapshot() {
  return false;
}

/** true, если пользователь просит меньше движения (prefers-reduced-motion) */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReducedMotion,
    getReducedMotionSnapshot,
    getServerReducedMotionSnapshot,
  );
}
