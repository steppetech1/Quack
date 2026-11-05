/** Общие мелочи для сцен лендинга: математика, шаг частиц, прогресс прокрутки «собралось / рассыпалось» */

export const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
export const easeOut = (x: number) => 1 - Math.pow(1 - clamp01(x), 3);
export const smoothstep = (a: number, b: number, x: number) => {
  const t = clamp01((x - a) / (b - a));
  return t * t * (3 - 2 * t);
};
export const approach = (v: number, target: number, rate: number, dt: number) => v + (target - v) * (1 - Math.exp(-rate * dt));
export const rand = (a: number, b: number) => a + Math.random() * (b - a);
export function hash(x: number, y: number) {
  const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return s - Math.floor(s);
}

/** Шаг решётки частиц — тот же, что у утки первого экрана (~59 ячеек на её ширину): частицы везде одного размера */
export function cellSize(): number {
  return Math.max(170, window.innerWidth * 0.21) / 59;
}

/**
 * Как «собрана» графика сцены, 0..1. Секция въезжает снизу — графика собирается из точек (пока верх секции идёт от низа экрана
 * к верху), секция уезжает вверх — рассыпается, как утка на первом экране.
 */
export function sceneForm(sec: HTMLElement): number {
  const r = sec.getBoundingClientRect();
  const vh = window.innerHeight;
  const t = r.top / vh; // 1 — секция только показалась снизу, 0 — стоит на экране, <0 — уезжает вверх
  const assemble = 1 - smoothstep(0.08, 0.85, t);
  const dissolve = smoothstep(0.04, 0.55, -t);
  return assemble * (1 - dissolve);
}

/** Секция хоть сколько-то видна (с запасом сверху и снизу) */
export function sceneVisible(sec: HTMLElement, margin = 0.1): boolean {
  const r = sec.getBoundingClientRect();
  const vh = window.innerHeight;
  return r.bottom > -vh * margin && r.top < vh * (1 + margin);
}
