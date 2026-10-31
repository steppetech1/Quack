export type TopicState = "solid" | "review" | "confused" | "new";

export type Topic = {
  id: string;
  /** Глава; у «свободной» темы без главы не задана */
  hubId?: string;
  title: string;
  /** Одна строка: что это за тема */
  line: string;
  /** Одна простая фраза без жаргона: для карточки в графе */
  simple: string;
  /** 2–3 предложения: что это и зачем нужно дальше */
  about: string;
  state: TopicState;
  /** Активное заблуждение, если есть */
  misconception?: string;
  /** Сколько ещё держится в памяти, 0–1 (для кольца вокруг точки) */
  retention?: number;
  /** Темы, которые нужны до этой */
  requires: string[];
  /** Похоже по смыслу (ненаправленная связь, без «нужно до») */
  related?: string[];
};

export type Hub = {
  id: string;
  /** Номер главы: «0» для основ, дальше римские */
  num: string;
  title: string;
  /** Два предложения простым языком: о чём глава */
  about: string;
  suggested?: boolean;
  /** Когда последний раз занимался этой главой (ISO-дата) */
  lastStudied?: string;
  deadline?: { label: string; date: string };
};

export type OutsideTopic = {
  id: string;
  title: string;
  fromDomain: string;
  linkedTo: string;
};

export type Session = {
  id: string;
  /** Адрес, если сессия уже открывается */
  href?: string;
  date: string;
  title: string;
  topics: string[];
};

export type TodayItem = {
  id: string;
  /** Адрес сессии, если пункт уже ведёт в неё */
  href?: string;
  title: string;
  reason: string;
  minutes: number;
};

export type Domain = {
  id: string;
  title: string;
  /** Дательный падеж для фраз «по …»: «линейной алгебре» */
  titleDat: string;
  hubs: Hub[];
  topics: Topic[];
  outside: OutsideTopic[];
  sessions: Session[];
  today: TodayItem[];
  /** Готовность к ближайшему дедлайну, 0–100 */
  readiness?: number;
  /** Статус на бэкенде: onboarding (темы ещё не подтверждены), active, archived. У моков не задан */
  status?: string;
};

export type NoteKind = "def" | "thm" | "text";

export type NoteBlock = {
  kind: NoteKind;
  /** Мини-разметка RichText */
  text: string;
  /** Откуда взято: «лекция 7, слайд 12» */
  src?: string;
};

export type CardKind = "definition" | "sign" | "distinction" | "myth";

export type StudyCard = { id: string; kind: CardKind; front: string; back: string };

export type CanvasSketch = "ratio" | "plot" | "estimate";

export type CanvasItem = { id: string; title: string; date: string; sketch: CanvasSketch };

export type Material = { id: string; title: string; ref: string; kind: "pdf" | "slides" | "book" };

export type MomentKind = "first" | "practice" | "mistake" | "repeat";

export type Moment = { id: string; date: string; sessionId?: string; title: string; kind: MomentKind; topicId: string };

export type TopicDetails = {
  summary?: string;
  notes: NoteBlock[];
  /** Признаки в задаче: когда применять */
  when: string[];
  example?: { title: string; text: string };
  /** Личные ловушки студента */
  traps: { text: string; date: string }[];
  cards: StudyCard[];
  canvases: CanvasItem[];
  materials: Material[];
  moments: Moment[];
};

export type CheatSheet = {
  formulas: { name: string; tex: string }[];
  methods: { sign: string; how: string }[];
  traps: string[];
};

// ── Канвасы (список и просмотр) ────────────────────────────────────────

export type CanvasSolved = "yes" | "no" | "hint2";

/** Строка, написанная от руки: tex — как прочитала распознавалка, read — то же простым текстом для поиска */
export type HandLine = { id: string; tex: string; read: string; /** Как написал бы студент: простой Unicode */ hand: string };

/** Пометка слоя учителя после строки решения */
export type TeacherMark = { id: string; afterLine: string; kind: "error" | "note"; text: string };

export type CanvasBlock =
  | { id: string; kind: "task"; title: string; condition: string; /** Условие одной строкой для превью */ brief: string; lines: HandLine[]; marks: TeacherMark[] }
  | { id: string; kind: "hint"; title: string; text: string }
  | { id: string; kind: "card"; title: string; formulas: { name: string; tex: string }[] };

export type Canvas = {
  id: string;
  domainId: string;
  title: string;
  date: string;
  /** id тем домена */
  topics: string[];
  sessionId?: string;
  solved: CanvasSolved;
  sketch: CanvasSketch;
  /** Пропорции листа на превью */
  shape: "tall" | "square" | "wide";
  blocks: CanvasBlock[];
};

export type PlanStep = {
  id: string;
  label: string;
  /** Якорь в ленте, куда прокручивает клик по пройденному шагу */
  anchor?: string;
  /** Ответвление, которое студент сделал сам */
  branch?: boolean;
};

/** plain — текстовое чтение формулы для скринридера (имя radio) */
export type Option = { id: string; text: string; plain?: string; correct?: boolean };

export type Turn =
  | { kind: "student"; id: string; text: string }
  | {
      kind: "teacher";
      id: string;
      memory?: { session: string; date: string };
      thinking?: string;
      text: string;
    }
  | {
      kind: "question";
      id: string;
      num: number;
      prompt: string;
      options: Option[];
    };

export type Reply = { text: string };

export type SessionData = {
  id: string;
  domainId: string;
  title: string;
  goal: string;
  topics: { title: string; state: "solid" | "review" | "confused" | "new" }[];
  turns: Turn[];
  /** Реакция учителя на каждый вариант, ключ — id варианта или "idk" */
  replies: Record<string, Reply>;
  /** Добавляется к реакции, если студент объяснил утке ход мысли */
  thoughtLead: string;
  plan: { done: PlanStep[]; current: PlanStep; next: PlanStep[] };
  planAfter: { done: PlanStep[]; current: PlanStep; next: PlanStep[] };
  progress: { label: string; done: number; total: number };
  progressAfter: { done: number; total: number };
  agent: { task: string };
};

export type SkillBinding = "notes" | "cards" | "cheat";

export type Skill = {
  id: string;
  name: string;
  text: string;
  bindings: SkillBinding[];
  /** Команда для чата без «/», если скилл можно вызвать через / */
  command?: string;
  enabled: boolean;
};

export type ChatPart = string | { ref: string; label: string };
export type ChatMsg = { id: string; role: "teacher" | "student"; parts: ChatPart[] };
