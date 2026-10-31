import { en } from "./en";
import { ru } from "./ru";
import type { Locale } from "./locale";

/** Same shape as `en`, with every string widened */
type Widen<T> = T extends string ? string : T extends (...a: infer A) => infer R ? (...a: A) => Widen<R> : { [K in keyof T]: Widen<T[K]> };

export type Dict = Widen<typeof en>;

export const dictionaries: Record<Locale, Dict> = { en, ru };
