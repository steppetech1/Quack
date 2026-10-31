/** Russian plural forms: 1 день, 2 дня, 5 дней */
export function plural(n: number, one: string, few: string, many: string) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

/** English plural: 1 day, 2 days */
export const plural2 = (n: number, one: string, many: string) => (n === 1 ? one : many);
