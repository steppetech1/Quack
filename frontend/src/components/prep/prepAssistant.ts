// The preparation assistant (product-logic §4.5). It plans, it does not teach: given an exam and a
// date it lays the open sets out over the days left, says how long a day takes and what to start
// with. A request to explain material is turned back to planning and to «Проверь себя».
// Pure functions over the prep model; stands in for the backend until there is one.

import { allSets, allSkills, daysBetween, EXAMS, formatDate, formatShort, parseDeadline, skillById, SKILLS, TODAY, type ExamId, type StudySet } from "./prepData";
import type { PrepModel } from "./prepModel";
import type { TopicContent } from "./topicContent";

const DAY = 86_400_000;
const addDays = (d: Date, n: number) => new Date(d.getTime() + Math.round(n) * DAY);

/** A skill that is not yet solid takes about six hours of work to prove. */
const MINUTES_PER_SKILL = 360;
/** The last stretch before the date is kept for checking everything and a full practice test. */
const REVIEW_SHARE = 0.15;

const MONTHS = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря";

export const ASSISTANT_PROMPTS = [
  "Составь план до теста",
  "У меня ЕНТ 20 января, как успеть?",
  "Сколько заниматься в день?",
  "С чего начать?",
];

/** The date the student names: «до 1 декабря», «через 3 недели», «10 дней». */
function findDeadline(t: string): Date | null {
  const date = t.match(new RegExp(`(\\d{1,2})\\s+(${MONTHS})`));
  if (date) return parseDeadline(`${Number(date[1])} ${date[2]}`);
  const span = t.match(/(\d+)\s*(дн|день|недел|месяц)/);
  if (span) {
    const n = Number(span[1]);
    return addDays(TODAY, span[2].startsWith("недел") ? n * 7 : span[2].startsWith("месяц") ? n * 30 : n);
  }
  if (/через (месяц|неделю)/.test(t)) return addDays(TODAY, t.includes("месяц") ? 30 : 7);
  return null;
}

function examOf(t: string, fallback: ExamId): ExamId {
  if (/ент|унт|\bent\b/.test(t)) return "ent";
  if (/\bsat\b|сат/.test(t)) return "sat";
  return fallback;
}

const openSkills = (model: PrepModel, exam: ExamId) =>
  allSkills().filter((s) => s.exam === exam && model.states[s.id] !== "solid");

const minutesPerDay = (skills: number, days: number) =>
  Math.min(240, Math.max(20, Math.round((skills * MINUTES_PER_SKILL) / Math.max(1, days) / 5) * 5));

/** What to take first: roots, then confirmed traps, then the heaviest skills that are not held. */
function priorities(model: PrepModel, exam: ExamId) {
  const open = openSkills(model, exam);
  const why = (id: string) => {
    const s = skillById(id);
    if (s.root) return "корень: от него ошибки выше по карте";
    if ((model.misconceptions[id] ?? []).some((m) => m.status === "confirmed")) return "подтверждённая ловушка";
    return s.weight >= 8 ? "большой вес на экзамене" : "ещё не держится";
  };
  const rank = (id: string) => {
    const s = skillById(id);
    return (s.root ? 200 : 0) + ((model.misconceptions[id] ?? []).some((m) => m.status === "confirmed") ? 100 : 0) + s.weight;
  };
  return open
    .map((s) => s.id)
    .sort((a, b) => rank(b) - rank(a))
    .map((id) => ({ id, name: skillById(id).name, why: why(id) }));
}

function plan(model: PrepModel, exam: ExamId, deadline: Date): string {
  const name = EXAMS[exam].name;
  const days = daysBetween(TODAY, deadline);
  if (days <= 0) return `${formatDate(deadline)} уже прошло — напиши новую дату, и я пересоберу план.`;

  const sets = allSets().filter((s) => s.exam === exam && !model.doneSets.includes(s.id));
  const open = openSkills(model, exam);
  if (!sets.length || !open.length) {
    return `По ${name} всё уже держится. До ${formatDate(deadline)} хватит «Проверь себя» раз в пару дней и одного пробного теста за неделю до срока.`;
  }

  const review = Math.max(2, Math.round(days * REVIEW_SHARE));
  const work = days - review;
  const reviewFrom = addDays(TODAY, work);
  const perDay = minutesPerDay(open.length, work);
  const head = `План к ${formatDate(deadline)} · ${name}: ${days} дн., примерно ${perDay} мин в день.`;
  const tail = `• ${formatShort(reviewFrom)} – ${formatShort(deadline)}: «Проверь себя» по всем топикам и пробный тест на время.`;

  // Too little time for every set: keep the order that matters and say what drops out
  if (work < sets.length * 3) {
    const first = priorities(model, exam).slice(0, Math.max(1, Math.floor(work / 3)));
    return [
      head,
      `На все сеты не хватит, поэтому бери по важности:`,
      ...first.map((p, i) => `${i + 1}. ${p.name} — ${p.why}`),
      tail,
      `Остальное — если останется время. Если срок сдвигается, напиши новую дату.`,
    ].join("\n");
  }

  // Days are shared out by how many skills of each set still need proving
  const loads = sets.map((set) => Math.max(0.5, set.skills.filter((id) => model.states[id] !== "solid").length));
  const total = loads.reduce((a, b) => a + b, 0);
  let cursor = TODAY;
  const lines = sets.map((set, i) => {
    const span = Math.max(2, (work * loads[i]) / total);
    const from = cursor;
    const to = i === sets.length - 1 ? addDays(reviewFrom, -1) : addDays(cursor, span - 1);
    cursor = addDays(to, 1);
    const todo = set.skills.filter((id) => model.states[id] !== "solid").map((id) => skillById(id).name);
    return `• ${formatShort(from)} – ${formatShort(to)}: сет ${set.number} «${set.title}»${todo.length ? ` — ${todo.join(", ")}` : " — повторить"}`;
  });

  return [head, ...lines, tail, `Каждый сет закрывай через «Проверь себя»: зелёный на карте — значит, доказано.`].join("\n");
}

/**
 * One reply of the assistant. `fallback` is the exam of the set in work, used when the message does not
 * name one.
 */
export function assistantReply(text: string, model: PrepModel, fallback: ExamId): string {
  const t = text.toLowerCase();
  const exam = examOf(t, fallback);

  if (/объясни|почему|как реш|что такое|научи|разбер|не понима|правило/.test(t)) {
    const first = priorities(model, exam)[0];
    return [
      "Объяснять материал — не моя задача: я помогаю спланировать подготовку.",
      first ? `Могу поставить «${first.name}» раньше в плане, а проверить, держится ли навык, — в «Проверь себя».` : "Проверить, держится ли навык, можно в «Проверь себя».",
      "Для теории лучше учебник или преподаватель.",
    ].join(" ");
  }

  const deadline = findDeadline(t);
  if (deadline || /план|успе|подготов|расписан|график|срок|дедлайн/.test(t)) {
    return plan(model, exam, deadline ?? EXAMS[exam].test);
  }

  if (/сколько|в день|час|минут|темп/.test(t)) {
    const days = daysBetween(TODAY, EXAMS[exam].test);
    const open = openSkills(model, exam).length;
    return `До теста ${EXAMS[exam].name} ${formatDate(EXAMS[exam].test)} — ${days} дн., не доказано ${open} навыков. Это примерно ${minutesPerDay(
      open,
      days * (1 - REVIEW_SHARE)
    )} мин в день, если заниматься каждый день. Два выходных в неделю — прибавь четверть.`;
  }

  if (/с чего|сначала|перв|приорит|важн/.test(t)) {
    const list = priorities(model, exam).slice(0, 3);
    if (!list.length) return `По ${EXAMS[exam].name} всё держится — начни с пробного теста.`;
    return [`${EXAMS[exam].name}, по порядку:`, ...list.map((p, i) => `${i + 1}. ${p.name} — ${p.why}`)].join("\n");
  }

  return "Я собираю план подготовки. Напиши экзамен и срок — например, «ЕНТ 20 января, как успеть?» — или спроси, сколько заниматься в день и с чего начать.";
}

/* ---------- The assistant inside a topic ---------- */

export const TOPIC_PROMPTS = ["Где я ошибаюсь?", "Проверь моё решение", "Почему здесь так?", "Успею к дедлайну?"];

/**
 * A reply inside one topic of one set: checking a solution, the student's own mistakes, the deadline, an
 * explanation only when asked. It never opens with theory — that would make it a course (product-logic
 * §1, §4.4). Demo rules until the backend runs a model with this same context (product-logic §4.5).
 */
export function topicReply(text: string, model: PrepModel, skillId: string, set: StudySet, content: TopicContent | undefined): string {
  const t = text.toLowerCase();
  const skill = skillById(skillId);

  if (/провер|решени/.test(t)) {
    if (/\d/.test(t) && /=|→|->|x/.test(t)) {
      return `Демо: здесь бэкенд проверит каждый шаг. Сверь сам с правилом: ${
        content ? content.points[0].toLowerCase() : "каждый переход должен сохранять равенство"
      }. Если ответ не сошёлся — пришли, на каком шаге сомневаешься.`;
    }
    return "Пришли решение одной строкой, например «|2x − 1| = 5 → x = 3 и x = −2». Проверю шаги и скажу, где ошибка.";
  }

  if (/пример|покажи/.test(t)) {
    return content ? `Пример: ${content.example.q}\nРешение: ${content.example.a}` : `Примеров по теме «${skill.name}» пока нет.`;
  }

  if (/ошиб|ловушк|где я|не так|неправ/.test(t)) {
    const own = (model.misconceptions[skillId] ?? []).filter((m) => m.status === "confirmed" || m.status === "suspected");
    if (own.length) {
      return [
        `По твоим ответам в теме «${skill.name}»:`,
        ...own.map((m) => `• ${m.text}${m.trigger ? ` — ${m.trigger}` : ""}`),
        "Мок-тест справа покажет, ушла ли ошибка.",
      ].join("\n");
    }
    return `Своих ловушек в этой теме у тебя пока не видно. Частая у всех: ${content?.trap.toLowerCase() ?? "невнимательность в знаках"}`;
  }

  if (/успе|дедлайн|срок|сколько|когда|время|план/.test(t)) {
    const left = daysBetween(TODAY, set.deadline);
    const open = set.skills.filter((id) => model.states[id] !== "solid");
    if (!open.length) return `Все темы сета ${set.number} уже держатся — можно закрывать его.`;
    if (left <= 0) {
      return `Дедлайн сета был ${formatDate(set.deadline)}. Это не страшно: прогноз уже пересчитан. Осталось доказать тем: ${open.length} — начни с «${skillById(open[0]).name}».`;
    }
    const perDay = minutesPerDay(open.length, left);
    return `До дедлайна ${formatDate(set.deadline)} — ${left} дн., не доказано тем: ${open.length}. Примерно ${perDay} мин в день хватит. ${
      model.states[skillId] === "solid" ? "Эта тема уже держится — переходи к следующей." : "Эту тему закрой проверкой, когда будешь уверен."
    }`;
  }

  if (/тест|задач|мок/.test(t)) return "Мок-тест — справа. Тема станет «твёрдо», когда верных будет 5 или больше.";

  if (content && /объясн|проще|что такое|не понима|как|зачем|почему|теори/.test(t)) {
    // Asked for, so it is an answer, not a lesson; reading it does not count as progress
    return `${content.summary}\nЕсли непонятно что-то конкретное — спроси про этот шаг.`;
  }

  return `Я отвечаю на вопросы по теме «${skill.name}»: проверю решение, найду ошибку, объясню шаг, который непонятен. Спроси конкретно.`;
}
