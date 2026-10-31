import type { Domain, Topic } from "../types";
import { topicTexts } from "./topic-texts";

/** Детерминированное «сколько держится в памяти» по id и состоянию */
const retentionOf = (id: string, state: Topic["state"]): number => {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 1000;
  const r = h / 1000;
  if (state === "solid") return 0.85 + 0.15 * r;
  if (state === "review") return 0.35 + 0.2 * r;
  if (state === "confused") return 0.2 + 0.2 * r;
  return 0;
};

const t = (
  hubId: string | undefined,
  id: string,
  title: string,
  line: string,
  state: Topic["state"],
  requires: string[] = [],
  misconception?: string,
  related?: string[],
): Topic => ({
  id,
  hubId,
  title,
  line,
  ...topicTexts[id],
  state,
  requires,
  misconception,
  related,
  retention: retentionOf(id, state),
});

const matan: Domain = {
  id: "matan",
  title: "Матанализ",
  titleDat: "матанализу",
  readiness: 62,
  hubs: [
    { id: "base", num: "0", title: "Основы", about: "Здесь собран язык, на котором написан весь курс: множества, функции, неравенства и индукция. Без этого остальные главы читаются с трудом.", lastStudied: "2026-09-18" },
    { id: "limits", num: "I", title: "Пределы", about: "Предел отвечает на вопрос, к чему что-то приближается, и это самая важная идея всего курса. Здесь учишься считать пределы и сравнивать, как быстро величины стремятся к нулю.", lastStudied: "2026-10-02" },
    { id: "cont", num: "II", title: "Непре­рыв­ность", about: "Непрерывная функция — это функция без обрывов и скачков, и у неё есть полезные гарантии вроде существования корня. Здесь же разбираешься, как выглядят разрывы.", lastStudied: "2026-10-21" },
    { id: "deriv", num: "III", title: "Произ­вод­ная", about: "Производная показывает, как быстро функция меняется, и с её помощью исследуют графики и считают пределы. Здесь же формула Тейлора, которая приближает функции многочленами.", lastStudied: "2026-10-26" },
    {
      id: "series",
      num: "IV",
      title: "Ряды",
      about: "Ряд — это бесконечная сумма, и главный вопрос в том, получится ли в ней конечное число. Здесь учишься признакам сходимости и работе со степенными рядами.",
      deadline: { label: "Коллоквиум по рядам", date: "2026-11-01" },
      lastStudied: "2026-10-29",
    },
    { id: "integrals", num: "V", title: "Интег­ралы", about: "Интеграл — это действие, обратное производной, и способ считать площади. Здесь основные приёмы: замена переменной, интегрирование по частям и разложение дробей.", suggested: true },
  ],
  topics: [
    t("base", "sets", "Множества и логика", "Кванторы, операции над множествами, отрицание утверждений", "solid"),
    t("base", "functions", "Функции и графики", "Область определения, композиция, обратная функция", "solid", ["sets"]),
    t("base", "ineq", "Неравенства и модуль", "Оценки, неравенство треугольника, работа с |x|", "solid", ["sets"]),
    t("base", "induction", "Индукция", "Доказательство утверждений для всех натуральных n", "review", ["sets"]),
    t("base", "seq", "Последо­вательно­сти", "Ограниченность, монотонность, подпоследовательности", "solid", ["functions", "ineq"]),

    t("limits", "lim-seq", "Предел последо­вательно­сти", "Определение через ε и N, единственность предела", "solid", ["seq"]),
    t("limits", "lim-fn", "Предел функции", "Определения по Коши и по Гейне и их равносильность", "solid", ["lim-seq", "functions"]),
    t("limits", "one-sided", "Односторонние пределы", "Предел слева и справа, когда обычного нет", "solid", ["lim-fn"]),
    t("limits", "remarkable", "Замечательные пределы", "sin x / x и (1 + 1/n)ⁿ — и всё, что из них следует", "review", ["lim-fn"]),
    t("limits", "infinitesimal", "Бесконечно малые", "Сравнение и порядок малости, o-символика", "solid", ["lim-fn"], undefined, ["taylor"]),
    t("limits", "equiv", "Эквивалентности", "Замена на эквивалентные в произведениях и частных", "review", ["remarkable", "infinitesimal"], undefined, ["taylor"]),
    t(
      "limits",
      "indet",
      "Неопреде­лённости",
      "0/0, ∞/∞, 1^∞ и как их раскрывать",
      "confused",
      ["equiv"],
      "Считаешь, что 1^∞ всегда равно 1",
    ),

    t("cont", "continuity", "Непрерывность функции", "Три равносильных определения и арифметика непрерывных", "solid", ["lim-fn"]),
    t("cont", "discont", "Точки разрыва", "Устранимый разрыв, скачок и разрыв второго рода", "review", ["continuity", "one-sided"], undefined, ["asymptotes"]),
    t("cont", "weierstrass", "Теоремы Вейерштрасса", "Непрерывная на отрезке ограничена и достигает экстремумов", "solid", ["continuity"], undefined, ["extrema"]),
    t("cont", "bolzano", "Теорема Больцано — Коши", "Промежуточные значения и поиск корней", "new", ["continuity"]),
    t("cont", "uniform", "Равномерная непрерывность", "Когда δ не зависит от точки; теорема Кантора", "new", ["weierstrass"]),

    t("deriv", "deriv-def", "Определение производной", "Предел отношения приращений и касательная", "solid", ["continuity"]),
    t("deriv", "rules", "Правила диффе­ренци­рования", "Сумма, произведение, частное и таблица производных", "solid", ["deriv-def"]),
    t("deriv", "chain", "Сложная функция", "Производная композиции и обратной функции", "solid", ["rules"]),
    t("deriv", "extrema", "Монотонность и экстремумы", "Знак производной и необходимое условие экстремума", "solid", ["chain"]),
    t(
      "deriv",
      "lhopital",
      "Правило Лопиталя",
      "Предел отношения через отношение производных",
      "confused",
      ["chain", "indet"],
      "Применяешь, не проверив, что там 0/0 или ∞/∞",
      ["equiv"],
    ),
    t("deriv", "taylor", "Формула Тейлора", "Приближение многочленом и остаток в форме Пеано", "review", ["chain"]),
    t("deriv", "convex", "Выпуклость и перегибы", "Вторая производная и форма графика", "new", ["extrema"]),
    t("deriv", "asymptotes", "Асимптоты", "Вертикальные, горизонтальные и наклонные", "new", ["convex"]),

    t("series", "num-series", "Числовые ряды", "Частичные суммы и сходимость ряда", "solid", ["lim-seq"]),
    t("series", "necessary", "Необходимый признак", "Если ряд сходится, его члены стремятся к нулю", "solid", ["num-series"]),
    t("series", "comparison", "Признаки сравнения", "Оценка ряда через известный: геометрический, гармонический", "review", ["necessary", "equiv"]),
    t(
      "series",
      "dalembert",
      "Признак Даламбера",
      "Сходимость по пределу отношения соседних членов",
      "confused",
      ["comparison"],
      "Считаешь, что при q = 1 ряд расходится",
      ["lim-seq"],
    ),
    t("series", "cauchy-root", "Радикальный признак Коши", "Сходимость по пределу корня n-й степени из члена", "new", ["comparison"], undefined, ["remarkable"]),
    t("series", "alternating", "Знако­чере­дующиеся ряды", "Признак Лейбница, абсолютная и условная сходимость", "new", ["necessary"], undefined, ["induction"]),
    t("series", "power", "Степенные ряды", "Ряды по степеням (x − a) и область сходимости", "review", ["dalembert", "taylor"]),
    t("series", "radius", "Радиус сходимости", "Формула Коши — Адамара и проверка концов интервала", "new", ["power", "cauchy-root"]),

    t("integrals", "antideriv", "Первообразная", "Неопределённый интеграл и таблица", "new", ["rules"]),
    t("integrals", "substitution", "Замена переменной", "Подстановка и внесение под дифференциал", "new", ["antideriv", "chain"]),
    t("integrals", "by-parts", "Интегрирование по частям", "∫u dv = uv − ∫v du и когда его брать", "new", ["antideriv"], undefined, ["taylor"]),
    t("integrals", "rational", "Рациональные дроби", "Разложение на простейшие и их интегрирование", "new", ["substitution"]),

    // Темы без главы
    t(undefined, "hyperbolic", "Гиперболические функции", "sh, ch и th: показательные родственники sin и cos", "solid", [], undefined, ["remarkable", "rules"]),
    t(undefined, "newton", "Метод Ньютона", "Поиск корня скольжением по касательным", "review", ["deriv-def"], undefined, ["bolzano"]),
    t(undefined, "stirling", "Формула Стирлинга", "Как быстро растёт n!, одной аккуратной формулой", "new", ["seq"], undefined, ["equiv", "num-series"]),
    t(undefined, "parametric", "Параметрические кривые", "Кривые вида x(t), y(t) и их касательные", "new", ["functions", "chain"]),
  ],
  outside: [
    { id: "complex", title: "Комплексные числа", fromDomain: "Линейная алгебра", linkedTo: "power" },
  ],
  sessions: [
    { id: "s1", href: "/session/radius", date: "2026-10-29", title: "Радиус сходимости", topics: ["Степенные ряды", "Радиус сходимости"] },
    { id: "s2", date: "2026-10-28", title: "Где ломается Даламбер", topics: ["Признак Даламбера"] },
    { id: "s3", date: "2026-10-26", title: "Лопиталь без фокусов", topics: ["Правило Лопиталя", "Неопределённости"] },
    { id: "s4", date: "2026-10-24", title: "Тейлор до o(x³)", topics: ["Формула Тейлора"] },
    { id: "s5", date: "2026-10-21", title: "Разрывы второго рода", topics: ["Точки разрыва"] },
    { id: "s6", date: "2026-10-19", title: "Лекция 7: признаки сравнения", topics: ["Признаки сравнения", "Числовые ряды"] },
    { id: "s7", date: "2026-10-14", title: "Почему 1^∞ — не единица", topics: ["Неопреде­лённости", "Замечательные пределы"] },
    { id: "s8", date: "2026-10-09", title: "ε–δ на пальцах", topics: ["Предел функции"] },
  ],
  today: [
    { id: "d1", href: "/session/radius", title: "Продолжить степенные ряды", reason: "остановились на радиусе сходимости", minutes: 25 },
    { id: "d2", title: "Подготовка к коллоквиуму", reason: "в «Рядах» не изучены 3 темы", minutes: 40 },
    { id: "d3", title: "Повторить замечательные пределы", reason: "прошло 9 дней, пора освежить", minutes: 10 },
  ],
};

const empty = (id: string, title: string, titleDat: string): Domain => ({
  id,
  title,
  titleDat,
  hubs: [],
  topics: [],
  outside: [],
  sessions: [],
  today: [],
});

export const domains: Domain[] = [
  matan,
  empty("linal", "Линейная алгебра", "линейной алгебре"),
  empty("prob", "Теория вероятностей", "теории вероятностей"),
  empty("db", "Базы данных", "базам данных"),
];
