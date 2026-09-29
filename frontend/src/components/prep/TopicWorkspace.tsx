"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Icon } from "../choice/Icon";
import { daysBetween, formatShort, skillById, STATE_LABEL, TODAY, type Evidence, type StudySet, type Task } from "./prepData";
import { TOPIC_PROMPTS, topicReply } from "./prepAssistant";
import { addMaterial, answerTask, MOCK_SOLID, removeMaterial, settleMock, type Material, type PrepModel } from "./prepModel";
import { downloadMarkdown, generateCards, generateNotes, materialAsked, printPdf, renderMarkdown, type MaterialKind } from "./materials";
import { StateGlyph } from "./SkillGraph";
import { checksFor, TOPICS, type TopicContent } from "./topicContent";
import { TopicTheory } from "./TopicTheory";
import {
  completeRemoteTopic,
  fetchRemoteTopicTasks,
  openRemoteTopic,
  submitRemoteAnswer,
} from "./remoteTasks";
import { ApiError, isUuid } from "@/api/client";
import { fetchRemoteSets, REMOTE_PREP } from "./remoteSets";
import { applyRemoteKnowledgeToModel, explainRemoteNode, fetchRemoteKnowledge, refreshRemoteKnowledge } from "./remoteKnowledge";
import type { ExamId } from "./prepData";

/** The server's knowledge model after answers: states, recall and traps go into the model, and from
 * there to Quack's «Что изменилось» (PrepView reports every model change). */
async function pullKnowledge(base: PrepModel, exam: ExamId): Promise<PrepModel | null> {
  if (!REMOTE_PREP) return null;
  const data = await fetchRemoteKnowledge(exam, true).catch(() => null);
  // Answers rebuild the plan on the server: the sets screen gets the new order too
  fetchRemoteSets(exam, true).catch(() => {});
  return data ? applyRemoteKnowledgeToModel(base, data, exam) : null;
}
import {
  loadPrepMessages,
  pollObservationsDiff,
  requestChatObservation,
  sendPrepMessage,
} from "./remoteChat";
import styles from "./prep.module.css";

type Props = {
  model: PrepModel;
  set: StudySet;
  skillId: string;
  /** The set's topics in order, to step between them without going back to the graph */
  order: string[];
  /** This topic's own deadline */
  plannedBy: Date;
  onBack: () => void;
  onTopic: (skillId: string) => void;
  onModel: (model: PrepModel) => void;
  onToast: (text: string) => void;
};

type Line = {
  id: number | string;
  role: "student" | "assistant";
  text: string;
  material?: string;
  mode?: string;
  instanceId?: string;
  hintLevel?: number;
  referencedSkills?: string[];
  /** Стрим кончился без кадра `done` — связь оборвалась, ответ неполный */
  truncated?: boolean;
};
/** What the «Материалы» column shows: the list, the mock test or one material */
type Open = null | "mock" | string;

const KIND_LABEL: Record<MaterialKind, string> = { notes: "конспект", cards: "карточки" };

/**
 * A topic opened from the set's graph: a chat with the assistant and the topic's «Материалы» — the mock
 * test, and notes and flashcards the student asked the assistant to make. Nothing is waiting there in
 * advance: a material appears only after it was asked for, so the topic is not a course (product-logic §1).
 */
export function TopicWorkspace({ model, set, skillId, order, plannedBy, onBack, onTopic, onModel, onToast }: Props) {
  useEffect(() => {
    if (set.rawId && isUuid(set.rawId) && REMOTE_PREP) {
      openRemoteTopic(set.rawId, skillId);
    }
  }, [set.rawId, skillId]);

  useEffect(() => {
    if (!REMOTE_PREP) return;
    explainRemoteNode(skillId).then((res) => {
      if (res && res.items.length > 0) {
        const sourceMap: Record<string, Evidence["source"]> = {
          diagnostic: "замер",
          mock: "мок",
          chat: "чат",
          task: "задача",
          self_report: "замер",
        };
        const mapped: Evidence[] = res.items.map((e) => ({
          source: sourceMap[e.source] ?? "задача",
          text: e.summary || `Ответ (${e.direction > 0 ? "верно" : "ошибка"})`,
          date: new Date(e.observed_at),
        }));
        onModel({
          ...modelRef.current,
          evidence: {
            ...modelRef.current.evidence,
            [skillId]: mapped,
          },
        });
      }
    });
  }, [skillId]);

  useEffect(() => {
    if (!REMOTE_PREP || !set.rawId || !isUuid(set.rawId)) return;
    loadPrepMessages(set.rawId, skillId).then((history) => {
      if (history && history.length > 0) {
        setLines(
          history.map((m, idx) => ({
            id: m.id || idx,
            role: m.role === "assistant" ? "assistant" : "student",
            text: m.text,
            mode: m.markup?.mode ?? undefined,
            instanceId: m.markup?.gave_task_instance_id ?? undefined,
            hintLevel: m.markup?.hint_level ?? undefined,
            referencedSkills: m.markup?.referenced_skill_ids ?? undefined,
          }))
        );
      }
    });
  }, [set.rawId, skillId]);

  const [refreshing, setRefreshing] = useState(false);
  const handleRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      if (REMOTE_PREP && set.rawId && isUuid(set.rawId)) {
        let observeRes = null;
        try {
          observeRes = await requestChatObservation(set.rawId, skillId);
        } catch (err: unknown) {
          if (err && typeof err === "object" && "status" in err && (err as { status: number }).status === 429) {
            onToast("Наблюдатель уже запущен, подождите несколько секунд");
            setRefreshing(false);
            return;
          }
        }

        if (observeRes && observeRes.since_event_id !== undefined) {
          onToast("Наблюдатель запущен: сверяем сообщения с моделью");
          const diff = await pollObservationsDiff(
            set.rawId,
            observeRes.since_event_id,
            skillId
          );
          if (diff) {
            if (diff.status === "done") {
              if (diff.observations.length > 0) {
                onToast(`Анализ завершён: зафиксировано ${diff.observations.length} наблюдений`);
              } else {
                onToast("Новых наблюдений в диалоге не обнаружено");
              }
              explainRemoteNode(skillId).then((res) => {
                if (res && res.items.length > 0) {
                  const sourceMap: Record<string, Evidence["source"]> = {
                    diagnostic: "замер",
                    mock: "мок",
                    chat: "чат",
                    task: "задача",
                    self_report: "замер",
                  };
                  const mapped: Evidence[] = res.items.map((e) => ({
                    source: sourceMap[e.source] ?? "задача",
                    text: e.summary || `Ответ (${e.direction > 0 ? "верно" : "ошибка"})`,
                    date: new Date(e.observed_at),
                  }));
                  onModel({
                    ...modelRef.current,
                    evidence: {
                      ...modelRef.current.evidence,
                      [skillId]: mapped,
                    },
                  });
                }
              });
              setRefreshing(false);
              return;
            } else if (diff.status === "failed") {
              onToast(`Анализ чата завершился с ошибкой: ${diff.failed_reason || "попробуйте позже"}`);
              setRefreshing(false);
              return;
            }
          }
        }
      }

      const res = await refreshRemoteKnowledge({
        kind: "prep",
        topic_skill_id: skillId,
      });
      if (res?.status === "queued") {
        onToast("Наблюдатель запущен: сверяем сообщения с моделью");
      } else if (res?.status === "empty") {
        onToast("Новых сообщений для анализа пока нет");
      } else if (res?.status === "failed") {
        onToast("Сервис анализа временно недоступен");
      }
    } catch {
      onToast("Ошибка при запуске обновления");
    } finally {
      const pulled = await pullKnowledge(modelRef.current, set.exam);
      if (pulled) {
        onModel(pulled);
        onToast("Модель знаний обновлена — изменения в Quack!");
      }
      setRefreshing(false);
    }
  };

  const skill = skillById(skillId);
  const state = model.states[skillId];
  const content = TOPICS[skillId];
  const index = order.indexOf(skillId);
  const left = daysBetween(TODAY, plannedBy);
  const behind = state !== "solid" && left < 0;
  const materials = model.materials[skillId] ?? [];
  const [open, setOpen] = useState<Open>(null);
  // Phones show one pane at a time
  const [pane, setPane] = useState<"chat" | "materials">("chat");

  const [lines, setLines] = useState<Line[]>([]);
  const [typing, setTyping] = useState(false);
  const idRef = useRef(0);
  /** Последний вопрос — чтобы «повторить» после обрыва отправило именно его */
  const lastAskedRef = useRef<string>("");
  // The latest model: a reply lands a moment later and must not undo an answer given meanwhile
  const modelRef = useRef(model);
  modelRef.current = model;

  const say = (question: string, reply: () => Omit<Line, "id" | "role">) => {
    setLines((l) => [...l, { id: ++idRef.current, role: "student", text: question }]);
    setTyping(true);
    setTimeout(() => {
      setTyping(false);
      // Worked out before the state update: making a material also changes the model
      const answer = { id: ++idRef.current, role: "assistant" as const, ...reply() };
      setLines((l) => [...l, answer]);
    }, 900);
  };

  const make = (kind: MaterialKind, question: string) => {
    const count = materials.filter((m) => m.kind === kind).length;
    const material = kind === "notes" ? generateNotes(model, skillId, count) : generateCards(model, skillId, count);
    say(question, () => {
      // A topic without questions, content or traps yet gives nothing to put on a card
      if (material.kind === "cards" && !material.cards.length) {
        return { text: "Карточки пока не из чего собрать: в теме ещё нет вопросов и твоих ловушек. Порешай задачи — и попроси снова." };
      }
      onModel(addMaterial(modelRef.current, skillId, material));
      const size = material.kind === "cards" ? ` · ${material.cards.length} карточек` : "";
      return { text: `Готово: «${material.title}»${size}. Лежит в «Материалах» — открой, когда удобно.`, material: material.id };
    });
  };

  /** «Повторить» под оборванным ответом: обрубок убираем, вопрос шлём заново */
  const retryTruncated = (lineId: number | string) => {
    const again = lastAskedRef.current;
    if (!again || typing) return;
    setLines((prev) => prev.filter((l) => l.id !== lineId));
    void ask(again);
  };

  const ask = async (text: string) => {
    const q = text.trim();
    if (!q || typing) return;
    const kind = materialAsked(q);
    if (kind) return make(kind, q);

    if (REMOTE_PREP && set.rawId && isUuid(set.rawId)) {
      const studentLineId = ++idRef.current;
      const assistantLineId = ++idRef.current;
      setLines((prev) => [
        ...prev,
        { id: studentLineId, role: "student", text: q },
        { id: assistantLineId, role: "assistant", text: "" },
      ]);
      setTyping(true);
      let finished = false;

      try {
        await sendPrepMessage(
          { text: q, setId: set.rawId, topicSkillId: skillId },
          {
            onText: (delta) => {
              setLines((prev) =>
                prev.map((l) =>
                  l.id === assistantLineId ? { ...l, text: l.text + delta } : l
                )
              );
            },
            onDone: (done) => {
              finished = true;
              setTyping(false);
              setLines((prev) =>
                prev.map((l) =>
                  l.id === assistantLineId
                    ? {
                        ...l,
                        mode: done.mode ?? undefined,
                        instanceId: done.gave_task_instance_id ?? undefined,
                        hintLevel: done.hint_level ?? undefined,
                        referencedSkills: done.referenced_skill_ids ?? undefined,
                      }
                    : l
                )
              );
            },
            onError: (err) => {
              console.warn("Tutor stream error:", err);
              finished = true;
              setTyping(false);
              setLines((prev) =>
                prev.map((l) =>
                  l.id === assistantLineId && !l.text
                    ? { ...l, text: topicReply(q, model, skillId, set, content) }
                    : l
                )
              );
            },
          }
        );
        // Обрыв связи не бросает исключение — тело стрима просто кончается, и
        // без кадра `done` «печатает…» висело бы вечно поверх полуответа.
        if (!finished) {
          setTyping(false);
          setLines((prev) =>
            prev.map((l) =>
              l.id === assistantLineId
                ? {
                    ...l,
                    text: l.text || "Связь пропала, ответ не дошёл.",
                    truncated: true,
                  }
                : l
            )
          );
          lastAskedRef.current = q;
        }
      } catch (err: unknown) {
        console.warn("Tutor chat request failed, fallback to local:", err);
        setTyping(false);
        // Обрыв связи (status 0) — не отказ репетитора, а половина ответа.
        // Подменять её локальным текстом значило бы выдать чужой ответ за его.
        const dropped =
          err instanceof ApiError && err.status === 0 ? (lastAskedRef.current = q) : null;
        setLines((prev) =>
          prev.map((l) =>
            l.id !== assistantLineId
              ? l
              : dropped
                ? { ...l, text: l.text || "Связь пропала, ответ не дошёл.", truncated: true }
                : l.text
                  ? l
                  : { ...l, text: topicReply(q, model, skillId, set, content) }
          )
        );
      }
      return;
    }

    say(q, () => ({ text: topicReply(q, model, skillId, set, content) }));
  };

  const explain = (task: Task, choice: number) => {
    if (typing) return;
    setPane("chat");
    say(`Разбери вопрос: «${task.text}»`, () => ({ text: explainTask(task, choice, content) }));
  };

  const openMaterial = (id: Open) => {
    setOpen(id);
    setPane("materials");
  };

  return (
    <div className={styles.topicWork}>
      <header className={styles.setBar}>
        <button type="button" className={styles.backLink} onClick={onBack} aria-label="К графу сета" title="К графу сета">
          <Icon name="arrow-left" size={18} />
        </button>
        <div className={styles.setBarTitle}>
          <h2>{skill.name}</h2>
          <span className={styles.muted}>
            Сет {set.number} · {set.title} · тема {index + 1} из {order.length}
          </span>
        </div>
        <div className={styles.setBarSide}>
          <span className={behind ? styles.warn : styles.muted}>
            до {formatShort(plannedBy)} ·{" "}
            {state === "solid" ? "держится" : behind ? `просрочена на ${-left} дн.` : `осталось ${left} дн.`}
          </span>
          <span className={styles.checkState} data-state={state}>
            <StateGlyph state={state} size={12} /> {STATE_LABEL[state]}
          </span>
          {REMOTE_PREP && (
            <button
              type="button"
              className={styles.secondary}
              onClick={handleRefresh}
              disabled={refreshing}
              title="Сверить недавние сообщения темы с моделью знаний"
            >
              <Icon name="sparkles" size={13} /> {refreshing ? "Сверка..." : "Обновить статус"}
            </button>
          )}
        </div>
        {/* The other topics of the set, to move on without the graph */}
        <nav className={styles.topicSteps} aria-label="Темы сета">
          {order.map((id, i) => (
            <button key={id} type="button" aria-current={id === skillId} onClick={() => onTopic(id)} title={skillById(id).name}>
              <StateGlyph state={model.states[id]} size={10} />
              <span>
                {i + 1}. {skillById(id).name}
              </span>
            </button>
          ))}
        </nav>
      </header>

      <div className={styles.paneSwitch} role="tablist" aria-label="Что показать">
        <button type="button" role="tab" aria-selected={pane === "chat"} onClick={() => setPane("chat")}>
          <Icon name="message-circle" size={15} /> Чат
        </button>
        <button type="button" role="tab" aria-selected={pane === "materials"} onClick={() => setPane("materials")}>
          <Icon name="book-open-check" size={15} /> Материалы
          {materials.length > 0 && <span className={styles.paneCount}>{materials.length}</span>}
        </button>
      </div>

      <div className={styles.topicWorkStage} data-pane={pane === "chat" ? "chat" : "side"}>
        <section className={`${styles.canvas} ${styles.chatPane}`} aria-label="Чат по теме">
          <Chat
            skill={skill.name}
            lines={lines}
            typing={typing}
            onAsk={ask}
            onOpen={openMaterial}
            onRetry={retryTruncated}
          />
        </section>

        <aside className={`${styles.canvas} ${styles.sidePane}`} aria-label="Материалы">
          {open === null && (
            <MaterialList
              model={model}
              skillId={skillId}
              setRawId={set.rawId}
              materials={materials}
              busy={typing}
              onOpen={openMaterial}
              onAsk={(kind) => {
                setPane("chat");
                ask(kind === "notes" ? "Сделай конспект по теме" : "Сделай карточки по теме");
              }}
              onRemove={(id) => {
                onModel(removeMaterial(model, skillId, id));
                onToast("Материал удалён");
              }}
            />
          )}
          {/* The mock stays mounted: opening a material mid-test must not lose the test */}
          <div hidden={open !== "mock"}>
            <ViewerHead title="Мок-тест" onBack={() => setOpen(null)} />
            <MockTest set={set} model={model} skillId={skillId} onModel={onModel} onToast={onToast} onExplain={explain} />
          </div>
          {open !== null && open !== "mock" && (
            <MaterialView material={materials.find((m) => m.id === open)} onBack={() => setOpen(null)} />
          )}
        </aside>
      </div>
    </div>
  );
}

/** An answer taken apart: the correct option, the trap behind the pick, the rule */
function explainTask(task: Task, choice: number, content?: TopicContent) {
  if (task.solution && task.solution.length > 0) {
    return `Разбор задачи: ${task.solution.join(" → ")}`;
  }
  const right = task.options.find((o) => o.correct);
  const picked = task.options[choice];
  const parts = [`Правильный ответ — «${right?.label ?? "—"}».`];
  if (picked && !picked.correct) {
    parts.push(picked.trap ? `Ты выбрал «${picked.label}»: ${picked.trap.toLowerCase()}.` : `Ты выбрал «${picked.label}» — это не сходится с условием.`);
  } else {
    parts.push("Ты ответил верно — закрепи, почему так.");
  }
  if (content) parts.push(`Правило: ${content.points[0].charAt(0).toLowerCase()}${content.points[0].slice(1)}.`);
  return parts.join(" ");
}

/* ---------- Chat: answers what the student asks, and makes materials on request ---------- */

function Chat({
  skill,
  lines,
  typing,
  onAsk,
  onOpen,
  onRetry,
}: {
  skill: string;
  lines: Line[];
  typing: boolean;
  onAsk: (text: string) => void;
  onOpen: (id: string) => void;
  /** Спросить то же самое ещё раз после оборванного ответа */
  onRetry: (lineId: number | string) => void;
}) {
  const [input, setInput] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (lines.length) listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [lines, typing]);

  return (
    <div className={styles.topicChat}>
      <div className={styles.chatLines} ref={listRef} aria-live="polite">
        <p className={styles.chatLine} data-role="assistant">
          Это чат по теме «{skill}». Пришли решение — проверю, спроси, где ошибка, или попроси конспект или карточки: они появятся в
          «Материалах». В прогресс идут ответы в мок-тесте.
        </p>
        {lines.map((l) =>
          l.material ? (
            <div key={l.id} className={styles.chatReply}>
              <p className={styles.chatLine} data-role="assistant">
                {l.text}
              </p>
              <button type="button" className={styles.pinButton} onClick={() => onOpen(l.material!)}>
                <Icon name="book-open-check" size={13} /> Открыть
              </button>
            </div>
          ) : (
            <div key={l.id} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <p className={styles.chatLine} data-role={l.role}>
                {l.text || (typing && l.role === "assistant" ? "…" : "")}
              </p>
              {l.truncated && (
                <div style={{ alignSelf: "flex-start", marginTop: 2 }}>
                  <button
                    type="button"
                    className={styles.pinButton}
                    onClick={() => onRetry(l.id)}
                  >
                    <Icon name="sparkles" size={13} /> Ответ оборвался — повторить
                  </button>
                </div>
              )}
              {l.instanceId && (
                <div style={{ alignSelf: "flex-start", marginTop: 2 }}>
                  <button
                    type="button"
                    className={styles.pinButton}
                    onClick={() => onOpen("mock")}
                  >
                    <Icon name="circle-check" size={13} /> Решить выданную задачу
                  </button>
                </div>
              )}
            </div>
          )
        )}
        {typing && lines[lines.length - 1]?.role === "student" && (
          <p className={styles.chatLine} data-role="assistant" aria-label="Ассистент печатает">
            …
          </p>
        )}
      </div>

      <div className={styles.promptChips}>
        {["Сделай конспект", "Сделай карточки", ...TOPIC_PROMPTS].map((p) => (
          <button key={p} type="button" onClick={() => onAsk(p)} disabled={typing}>
            {p}
          </button>
        ))}
      </div>
      <form
        className={styles.chatForm}
        onSubmit={(e) => {
          e.preventDefault();
          onAsk(input);
          setInput("");
        }}
      >
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Пришли решение или спроси по теме…" aria-label="Сообщение" />
        <button type="submit" className={styles.primary} disabled={!input.trim() || typing}>
          Отправить
        </button>
      </form>
    </div>
  );
}

/* ---------- Материалы: the mock test and whatever the student asked to make ---------- */

function MaterialList({
  model,
  skillId,
  setRawId,
  materials,
  busy,
  onOpen,
  onAsk,
  onRemove,
}: {
  model: PrepModel;
  skillId: string;
  /** The backend's set id, when the set came from there */
  setRawId?: string;
  materials: Material[];
  busy: boolean;
  onOpen: (id: Open) => void;
  onAsk: (kind: MaterialKind) => void;
  onRemove: (id: string) => void;
}) {
  const total = checksFor(skillId).length;
  const last = (model.evidence[skillId] ?? []).find((e) => e.source === "мок");

  return (
    <div className={styles.materials}>
      <header className={styles.materialsHead}>
        <h3>Материалы</h3>
        <span className={styles.muted}>всё, что ты попросил сделать по теме</span>
      </header>

      {/* Теория идёт первой: с неё начинают тему, и от её открытия зависит after_guideline */}
      <TopicTheory setId={setRawId} skillId={skillId} />

      <button type="button" className={`${styles.materialRow} ${styles.materialMock}`} onClick={() => onOpen("mock")}>
        <span className={styles.materialIcon} data-kind="mock">
          <Icon name="circle-check" size={18} />
        </span>
        <span className={styles.materialText}>
          <strong>Мок-тест</strong>
          <span className={styles.muted}>
            {total} вопросов · засчитывается при {Math.min(MOCK_SOLID, total)} верных
            {last ? ` · последний ${formatShort(last.date)}` : ""}
          </span>
        </span>
        <Icon name="chevron-right" size={16} />
      </button>

      {(["notes", "cards"] as const).map((kind) => {
        const list = materials.filter((m) => m.kind === kind);
        return (
          <section key={kind} className={styles.materialGroup}>
            <p className={styles.eyebrow}>{kind === "notes" ? "Конспекты" : "Карточки"}</p>
            {list.length ? (
              list.map((m) => (
                <div key={m.id} className={styles.materialItem}>
                  <button type="button" className={styles.materialRow} onClick={() => onOpen(m.id)}>
                    <span className={styles.materialIcon} data-kind={m.kind}>
                      <Icon name={m.kind === "notes" ? "book-open-check" : "layers"} size={18} />
                    </span>
                    <span className={styles.materialText}>
                      <strong>{m.title}</strong>
                      <span className={styles.muted}>
                        {m.kind === "cards" ? `${m.cards.length} карточек` : "Markdown · PDF"} · {formatShort(m.createdAt)}
                      </span>
                    </span>
                    <Icon name="chevron-right" size={16} />
                  </button>
                  <button type="button" className={styles.pinRemove} aria-label={`Удалить «${m.title}»`} onClick={() => onRemove(m.id)}>
                    <Icon name="x" size={13} />
                  </button>
                </div>
              ))
            ) : (
              <button type="button" className={styles.materialEmpty} disabled={busy} onClick={() => onAsk(kind)}>
                <Icon name="sparkles" size={15} /> Попросить ассистента: {KIND_LABEL[kind]}
              </button>
            )}
          </section>
        );
      })}
      <p className={styles.note}>Конспекты и карточки делает ассистент по твоей просьбе в чате. Демо: позже их будет собирать бэкенд.</p>
    </div>
  );
}

function ViewerHead({ title, meta, onBack, children }: { title: string; meta?: string; onBack: () => void; children?: React.ReactNode }) {
  return (
    <header className={styles.viewerHead}>
      <button type="button" className={styles.topicClose} onClick={onBack} aria-label="К материалам" title="К материалам">
        <Icon name="arrow-left" size={16} />
      </button>
      <div className={styles.viewerTitle}>
        <strong>{title}</strong>
        {meta && <span className={styles.muted}>{meta}</span>}
      </div>
      {children}
    </header>
  );
}

function MaterialView({ material, onBack }: { material?: Material; onBack: () => void }) {
  if (!material) {
    return (
      <>
        <ViewerHead title="Материал удалён" onBack={onBack} />
      </>
    );
  }
  if (material.kind === "cards") {
    return (
      <>
        <ViewerHead title={material.title} meta={`${material.cards.length} карточек · ${formatShort(material.createdAt)}`} onBack={onBack} />
        <Flashcards key={material.id} cards={material.cards} />
      </>
    );
  }
  return (
    <>
      <ViewerHead title={material.title} meta={`конспект · ${formatShort(material.createdAt)}`} onBack={onBack}>
        <div className={styles.viewerActions}>
          <button type="button" className={styles.secondary} onClick={() => downloadMarkdown(material.title, material.markdown)}>
            .md
          </button>
          <button type="button" className={styles.secondary} onClick={() => printPdf(material.title, material.markdown)}>
            PDF
          </button>
        </div>
      </ViewerHead>
      <article className={styles.markdown}>{renderMarkdown(material.markdown)}</article>
    </>
  );
}

/* ---------- Flashcards: one big card, tap to flip, arrows to move — the Quizlet way ---------- */

function Flashcards({ cards: initial }: { cards: { front: string; back: string }[] }) {
  const [cards, setCards] = useState(initial);
  const [at, setAt] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const card = cards[at];

  if (!card) return <p className={styles.muted}>В этом наборе нет карточек.</p>;

  const go = (step: number) => {
    setFlipped(false);
    setAt((i) => Math.min(cards.length - 1, Math.max(0, i + step)));
  };

  const shuffle = () => {
    setCards((list) => [...list].sort(() => Math.random() - 0.5));
    setAt(0);
    setFlipped(false);
  };

  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowRight") go(1);
    else if (e.key === "ArrowLeft") go(-1);
    else if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      setFlipped((f) => !f);
    }
  };

  return (
    <div className={styles.flash} tabIndex={0} onKeyDown={onKey} aria-label="Карточки: пробел — перевернуть, стрелки — листать">
      <div className={styles.flashProgress} aria-hidden="true">
        <span style={{ width: `${((at + 1) / cards.length) * 100}%` }} />
      </div>
      <button
        type="button"
        className={styles.flashCard}
        data-flipped={flipped || undefined}
        onClick={() => setFlipped((f) => !f)}
        aria-label={flipped ? "Ответ. Нажми, чтобы увидеть вопрос" : "Вопрос. Нажми, чтобы увидеть ответ"}
      >
        <span className={styles.flashInner}>
          <span className={styles.flashFace}>
            <span className={styles.flashSide}>Вопрос</span>
            {card.front}
          </span>
          <span className={`${styles.flashFace} ${styles.flashBack}`}>
            <span className={styles.flashSide}>Ответ</span>
            {card.back}
          </span>
        </span>
      </button>
      <div className={styles.flashControls}>
        <button type="button" className={styles.flashNav} onClick={() => go(-1)} disabled={at === 0} aria-label="Предыдущая">
          <Icon name="chevron-right" size={18} className={styles.flipX} />
        </button>
        <span className={styles.flashCount}>
          {at + 1} / {cards.length}
        </span>
        <button type="button" className={styles.flashNav} onClick={() => go(1)} disabled={at === cards.length - 1} aria-label="Следующая">
          <Icon name="chevron-right" size={18} />
        </button>
      </div>
      <div className={styles.flashFoot}>
        <button type="button" className={styles.link} onClick={shuffle}>
          Перемешать
        </button>
        <span className={styles.muted}>нажми на карточку или пробел · стрелки листают</span>
      </div>
    </div>
  );
}

/* ---------- Mock test: every question of the topic; the state is settled at the end ---------- */

/** Options order: keep server order if instanceId is present (server already randomized it), else deterministic shuffle */
function getOptionOrder(task: Task, round: number): number[] {
  if (task.instanceId) {
    return task.options.map((_, i) => i);
  }
  let seed = [...`${task.id}:${round}`].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
  const order = task.options.map((_, i) => i);
  for (let i = order.length - 1; i > 0; i--) {
    seed = (seed * 1103515245 + 12345) >>> 0;
    const j = seed % (i + 1);
    [order[i], order[j]] = [order[j], order[i]];
  }
  return order;
}

function MockTest({
  set,
  model,
  skillId,
  onModel,
  onToast,
  onExplain,
}: {
  set: StudySet;
  model: PrepModel;
  skillId: string;
  onModel: (m: PrepModel) => void;
  onToast: (t: string) => void;
  onExplain: (task: Task, choice: number) => void;
}) {
  const [tasks, setTasks] = useState<Task[]>(() => checksFor(skillId));
  const [loadingTasks, setLoadingTasks] = useState(false);
  const [answering, setAnswering] = useState(false);
  const [round, setRound] = useState(0);
  const [picks, setPicks] = useState<number[]>([]);
  // After an answer the question stays on screen until «Дальше»
  const [shownAt, setShownAt] = useState(0);
  const [settled, setSettled] = useState<{ from: string; to: string } | null>(null);
  const questionStartTimeRef = useRef(Date.now());

  useEffect(() => {
    let active = true;
    if (REMOTE_PREP) {
      setLoadingTasks(true);
      fetchRemoteTopicTasks(skillId, set.rawId && isUuid(set.rawId) ? set.rawId : null).then((remote) => {
        if (!active) return;
        setLoadingTasks(false);
        if (remote && remote.length > 0) {
          setTasks(remote);
        } else {
          setTasks(checksFor(skillId));
        }
      });
    } else {
      setTasks(checksFor(skillId));
    }
    return () => {
      active = false;
    };
  }, [skillId, set.rawId, round]);

  const pool = tasks;
  const task = pool[shownAt];
  const pick = picks[shownAt];
  const finished = shownAt >= pool.length;
  const correct = picks.filter((p, i) => pool[i]?.options[p]?.correct).length;
  const need = Math.min(MOCK_SOLID, Math.max(1, pool.length));

  if (loadingTasks) return <p className={styles.muted}>Загружаем вопросы по теме…</p>;
  if (!pool.length) return <p className={styles.muted}>Вопросы по этой теме появятся, когда до неё дойдёт маршрут.</p>;

  const answer = async (i: number) => {
    if (pick !== undefined || answering) return;
    const currentTask = pool[shownAt];
    if (!currentTask) return;
    const elapsed = Math.max(1, Math.round((Date.now() - questionStartTimeRef.current) / 1000));

    if (currentTask.instanceId) {
      setAnswering(true);
      try {
        const optionKey = currentTask.options[i]?.key ?? currentTask.options[i]?.label;
        const res = await submitRemoteAnswer(currentTask.instanceId, optionKey, elapsed);

        if (currentTask.options[i]) {
          currentTask.options[i].correct = res.grade.correct;
          if (res.grade.matched_misconception_id) {
            currentTask.options[i].trap = res.grade.matched_misconception_id;
          }
        }
        currentTask.solution = res.solution;

        const answered = answerTask(model, skillId, currentTask, i, true).model;
        onModel(answered);
        setPicks((p) => [...p, i]);
        pullKnowledge(answered, set.exam).then((m) => m && onModel(m));
      } catch (err) {
        console.warn("Failed remote answer, falling back to local:", err);
        onModel(answerTask(model, skillId, currentTask, i, true).model);
        setPicks((p) => [...p, i]);
      } finally {
        setAnswering(false);
      }
    } else {
      onModel(answerTask(model, skillId, currentTask, i, true).model);
      setPicks((p) => [...p, i]);
    }
  };

  const nextQuestion = async () => {
    const last = shownAt + 1 >= pool.length;
    setShownAt((n) => n + 1);
    questionStartTimeRef.current = Date.now();
    if (!last) return;

    // The whole mock decides the state, not a single answer
    const r = settleMock(model, skillId, correct, pool.length);
    onModel(r.model);
    setSettled({ from: STATE_LABEL[r.from], to: STATE_LABEL[r.to] });
    if (r.setPassed) onToast(`Сет ${r.setPassed.number} доказан целиком — отчёт в «Обзоре»`);
    pullKnowledge(r.model, set.exam).then((m) => m && onModel(m));

    if (r.to === "solid" && set.rawId && isUuid(set.rawId) && REMOTE_PREP) {
      try {
        const updated = await completeRemoteTopic(set.rawId, skillId);
        if (updated) {
          onToast(`Тема «${skillById(skillId).name}» закрыта на сервере`);
          fetchRemoteSets(set.exam, true).catch(() => {});
        }
      } catch (err) {
        console.warn("Failed to complete topic on server:", err);
      }
    }
  };

  const restart = () => {
    setPicks([]);
    setShownAt(0);
    setSettled(null);
    questionStartTimeRef.current = Date.now();
    setRound((n) => n + 1);
  };

  return (
    <div className={styles.mockTest} key={round}>
      <header className={styles.mockHead}>
        <div>
          <p className={styles.eyebrow}>Мок-тест · демо · нужно {need} верных</p>
          <h3>{finished ? "Итог" : `Вопрос ${shownAt + 1} из ${pool.length}`}</h3>
        </div>
        <span className={styles.mockDots} aria-hidden="true">
          {pool.map((t, i) => (
            <span
              key={t.id}
              data-result={
                picks[i] === undefined ? (i === shownAt ? "current" : undefined) : t.options[picks[i]]?.correct ? "correct" : "wrong"
              }
            />
          ))}
        </span>
      </header>

      {finished ? (
        <div className={styles.mockResult}>
          <p className={styles.mockScore}>
            <strong>{correct}</strong> из {pool.length} · {correct >= need ? "тема доказана" : `до зачёта не хватило ${need - correct}`}
          </p>
          {settled && (
            <p className={styles.modelUpdate}>
              <StateGlyph state={model.states[skillId]} size={12} />
              На графе: {settled.to}
              {settled.from !== settled.to && ` (было: ${settled.from})`}
            </p>
          )}
          <ul className={styles.mockReview}>
            {pool.map((t, i) => {
              const ok = t.options[picks[i]]?.correct;
              return (
                <li key={t.id} data-correct={ok}>
                  <Icon name={ok ? "check" : "x"} size={14} />
                  <span>{t.text}</span>
                  {!ok && (
                    <button type="button" className={styles.link} onClick={() => onExplain(t, picks[i])}>
                      Разобрать
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
          <div className={styles.actions}>
            <button type="button" className={styles.primary} onClick={restart}>
              Пройти ещё раз
            </button>
          </div>
        </div>
      ) : (
        <div className={styles.task}>
          <p className={styles.taskText}>{task.text}</p>
          <div className={styles.options} role="group" aria-label="Варианты ответа">
            {getOptionOrder(task, round).map((i, place) => {
              const o = task.options[i];
              return (
                <button
                  key={o.label}
                  type="button"
                  className={styles.option}
                  data-result={pick !== undefined ? (o.correct ? "correct" : pick === i ? "wrong" : undefined) : undefined}
                  disabled={pick !== undefined || answering}
                  onClick={() => answer(i)}
                >
                  <span className={styles.optionLetter}>{"ABCD"[place]}</span>
                  {o.label}
                </button>
              );
            })}
          </div>

          {pick !== undefined && (
            <div className={styles.feedback} data-correct={Boolean(task.options[pick]?.correct)}>
              <strong>
                {task.options[pick]?.correct
                  ? "Верно"
                  : task.options[pick]?.trap
                    ? `Ловушка: ${task.options[pick]!.trap!.toLowerCase()}`
                    : "Неверно"}
              </strong>
              <div className={styles.actions}>
                <button type="button" className={styles.primary} onClick={nextQuestion}>
                  {shownAt + 1 < pool.length ? "Дальше" : "Итог"}
                </button>
                <button type="button" className={styles.secondary} onClick={() => onExplain(task, pick)}>
                  <Icon name="message-circle" size={15} /> Разобрать в чате
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
