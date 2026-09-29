"""Full jury cycle over the live API: register -> chat -> profile -> matching ->
diagnostic -> plan adapted -> set opened -> theory -> tasks -> knowledge moved ->
set report -> Quack recommendation accepted.

Needs the stack up (make up, make api, make worker-interactive, make worker-bulk) and a real
LLM_API_KEY: the chat, the texts and the set report are model output, not fixtures.

Run:  make e2e   (or: cd backend && uv run --frozen python ../scripts/e2e_jury.py [base_url])
Exit code: 0 clean, 1 a step it cannot continue past, 2 finished with remarks. Every step is
named by its "=== N. ... ===" header, so the last header before a [!!] line is the step that broke.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
EXAM = "ENT_MATH"
OK, BAD = "[ok]", "[!!]"
notes: list[str] = []


def say(ok: bool, text: str) -> bool:
    print(f"{OK if ok else BAD} {text}", flush=True)
    if not ok:
        notes.append(text)
    return ok


async def poll(fn, ready, *, limit_s=90, step_s=3, label=""):
    """Call fn() until ready(result) or the budget runs out. Returns (result, seconds)."""
    started = time.monotonic()
    result = None
    while time.monotonic() - started < limit_s:
        result = await fn()
        if ready(result):
            return result, round(time.monotonic() - started, 1)
        await asyncio.sleep(step_s)
    print(f"     ...gave up on {label} after {limit_s}s", flush=True)
    return result, round(time.monotonic() - started, 1)


async def stream_chat(c: httpx.AsyncClient, kind: str, text: str, **extra):
    """POST a chat message, collect the SSE frames. Returns (status, events, ttfb_ms)."""
    body = {"text": text, **extra}
    events: list[dict] = []
    started = time.monotonic()
    ttfb = None
    async with c.stream("POST", f"/chat/{kind}/messages", json=body, timeout=180) as r:
        if r.status_code != 200:
            await r.aread()
            return r.status_code, [], None
        buffer = ""
        async for chunk in r.aiter_text():
            if ttfb is None:
                ttfb = round((time.monotonic() - started) * 1000)
            buffer += chunk.replace("\r\n", "\n")
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                data = "\n".join(
                    line[5:].lstrip(" ")
                    for line in frame.split("\n")
                    if line.startswith("data:")
                )
                if data:
                    events.append(json.loads(data))
    return 200, events, ttfb


def reply_text(events: list[dict]) -> str:
    return "".join(e.get("text", "") for e in events if e.get("type") == "text_delta")


async def main() -> int:
    email = f"e2e{random.randrange(10**6)}@quack.kz"
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        print(f"\n=== 1. Регистрация ({email}) ===")
        r = await c.post(
            "/auth/register",
            json={"name": "Е2Е", "email": email, "password": "password123"},
        )
        if not say(r.status_code == 201, f"register -> {r.status_code}"):
            return 1
        student_id = r.json()["student_id"]
        me = await c.get("/auth/me")
        say(me.status_code == 200, f"/auth/me -> {me.status_code} по cookie")

        print("\n=== 2. Чат с ассистентом (подбор) ===")
        status, events, ttfb = await stream_chat(
            c, "selection", "Привет! Я в 11 классе, хочу на программиста в Казахстане."
        )
        text = reply_text(events)
        say(status == 200, f"первое сообщение -> {status}, первый байт {ttfb} мс")
        say(len(text) > 20, f"ассистент ответил {len(text)} символов: {text[:90]!r}")
        tools = [e["tool"] for e in events if e.get("type") == "tool_call"]
        print(f"     инструменты агента: {tools or 'нет'}")

        status2, events2, _ = await stream_chat(
            c, "selection", "Готов заниматься 8 часов в неделю, пробный ЕНТ был 95."
        )
        say(status2 == 200, f"второе сообщение -> {status2}")
        errors2 = [e for e in events2 if e.get("type") == "error"]
        say(not errors2, f"второй ответ дошёл до конца, без кадра error: {errors2[:1]}")
        tools2 = [e["tool"] for e in events2 if e.get("type") == "tool_call"]
        print(f"     инструменты агента: {tools2 or 'нет'}")

        hist = await c.get("/chat/selection/messages", params={"limit": 50})
        msgs = hist.json() if hist.status_code == 200 else []
        roles = [m.get("role") or m.get("type") for m in msgs]
        say(
            len(msgs) >= 4,
            f"история чата: {len(msgs)} сообщений {roles}",
        )

        print("\n=== 3. Анкета ===")
        wanted = {
            "level.grade": 11,
            "level.admission_year": 2027,
            "direction.field": "Информатика",
            "pace.hours_per_week": 8,
            "academics.ent_trial_score": 95,
            "preferences.countries": ["Казахстан"],
        }
        applied = 0
        for path, value in wanted.items():
            pr = await c.patch(
                "/profile", json={"path": path, "value": value, "by": "user"}
            )
            applied += pr.status_code == 200
        say(applied == len(wanted), f"PATCH /profile: {applied}/{len(wanted)} слотов")
        prof = (await c.get("/profile")).json()
        say(
            prof.get("readiness", 0) > 0,
            f"готовность анкеты readiness={prof.get('readiness')}",
        )

        print("\n=== 4. Подбор программ ===")
        cat = await c.get("/programs", params={"limit": 20})
        programs = cat.json().get("items", []) if cat.status_code == 200 else []
        say(len(programs) > 0, f"каталог: {len(programs)} программ")
        match, secs = await poll(
            lambda: c.get("/matching", params={"limit": 20}),
            lambda r: r.status_code == 200 and r.json().get("items"),
            limit_s=30,
            label="/matching",
        )
        items = match.json().get("items", []) if match.status_code == 200 else []
        say(len(items) > 0, f"/matching: {len(items)} совпадений за {secs}s")
        if items:
            top = items[0]
            print(
                f"     топ: {(top['program']['university'] + ' / ' + top['program']['direction'])[:52]!r} "
                f"realism={top['realism']} score={top.get('score')}"
            )
            pid = top["program"]["id"]
            sv = await c.post(f"/saved/{pid}")
            say(sv.status_code in (200, 201, 204), f"сохранил программу -> {sv.status_code}")
            saved = (await c.get("/saved")).json()
            n_saved = len(saved.get("items", saved if isinstance(saved, list) else []))
            say(n_saved >= 1, f"/saved: {n_saved} программ")

        print("\n=== 5. План до замера ===")
        before = (await c.get("/sets", params={"exam_id": EXAM})).json()
        ids_before = [s["id"] for s in before["upcoming"]]
        topics_before = [t["skill_id"] for s in before["upcoming"] for t in s["topics"]]
        say(
            len(before["upcoming"]) > 0,
            f"сетов {len(before['upcoming'])}, тем {len(topics_before)}, "
            f"первые: {topics_before[:3]}",
        )

        print("\n=== 6. Замер ===")
        start = await c.post("/diagnostic", json={"exam_id": EXAM, "n_tasks": 6})
        if not say(start.status_code in (200, 201), f"старт замера -> {start.status_code}"):
            print(start.text[:300])
            return 1
        run = start.json()
        run_id = run["run_id"]
        task = run.get("next_task")
        answered = 0
        while task is not None and answered < 12:
            numeric = not task.get("options")
            answer = "1" if numeric else 0
            ar = await c.post(
                f"/diagnostic/{run_id}/answer",
                json={
                    "instance_id": task["id"],
                    "answer": answer,
                    "time_spent_sec": 30,
                    "mode": "diagnostic",
                },
            )
            if ar.status_code != 200:
                say(False, f"ответ #{answered + 1} -> {ar.status_code}: {ar.text[:160]}")
                break
            answered += 1
            task = ar.json().get("next_task")
        say(answered >= 6, f"отвечено вопросов: {answered}")
        fin = await c.post(f"/diagnostic/{run_id}/finish")
        say(fin.status_code == 200, f"finish -> {fin.status_code}")
        if fin.status_code == 200:
            res = fin.json()["state"] if "state" in fin.json() else fin.json()
            result = fin.json().get("result") or res
            firm = result.get("firm", [])
            shaky = result.get("shaky", [])
            suspected = result.get("suspected", [])
            say(
                not (set(firm) & set(shaky)),
                f"firm {len(firm)} и shaky {len(shaky)} не пересекаются",
            )
            say(
                len(suspected) == len(set(suspected)),
                f"подозрения без дублей: {len(suspected)}",
            )
            print(f"     слова замера: {str(result.get('words'))[:120]}")

        print("\n=== 7. План после замера (адаптация) ===")
        after, secs = await poll(
            lambda: c.get("/sets", params={"exam_id": EXAM}),
            lambda r: r.status_code == 200
            and [s["id"] for s in r.json()["upcoming"]] != ids_before,
            limit_s=45,
            label="пересборка плана",
        )
        aj = after.json()
        topics_after = [t["skill_id"] for s in aj["upcoming"] for t in s["topics"]]
        say(
            topics_after[:3] != topics_before[:3],
            f"очередь тем изменилась за {secs}s: было {topics_before[:3]} "
            f"стало {topics_after[:3]}",
        )
        say(
            aj.get("forecast") is not None,
            f"прогноз: {json.dumps(aj.get('forecast'), ensure_ascii=False)[:150]}",
        )

        print("\n=== 8. Сет, теория, задачи ===")
        target = aj["current"] or (aj["upcoming"][0] if aj["upcoming"] else None)
        if target is None:
            say(False, "нет сета для работы")
            return 1
        set_id = target["id"]
        if aj["current"] is None:
            op = await c.post(f"/sets/{set_id}/open")
            say(op.status_code == 200, f"открыл сет -> {op.status_code}")
            target = op.json() if op.status_code == 200 else target
        skill_id = target["topics"][0]["skill_id"]
        print(f"     сет {set_id[:8]} тема {skill_id}")
        to = await c.post(f"/sets/{set_id}/topics/{skill_id}/open")
        say(to.status_code == 200, f"открыл тему -> {to.status_code}")

        tx, secs = await poll(
            lambda: c.get(f"/texts/{set_id}/{skill_id}", params={"kind": "guideline"}),
            lambda r: r.status_code == 200 and r.json().get("status") in ("ready", "stale"),
            limit_s=120,
            label="конспект",
        )
        tj = tx.json()
        say(
            tj.get("status") in ("ready", "stale") and tj.get("text"),
            f"конспект {tj.get('status')} за {secs}s, {len(tj.get('text') or '')} символов",
        )
        if tj.get("text"):
            print(f"     начало: {tj['text'][:110]!r}")
        op_ = await c.post(
            f"/texts/{set_id}/{skill_id}/opened", json={"kind": "guideline"}
        )
        say(op_.status_code in (200, 204), f"пометил теорию открытой -> {op_.status_code}")

        kn_before = (await c.get("/knowledge", params={"exam_id": EXAM})).json()

        graded = 0
        for i in range(3):
            tr = await c.post(
                "/tasks",
                json={"skill_id": skill_id, "set_id": set_id, "with_trap": None},
            )
            if tr.status_code not in (200, 201):
                say(False, f"выдача задачи -> {tr.status_code}: {tr.text[:160]}")
                break
            inst = tr.json()
            numeric = not inst.get("options")
            ans = await c.post(
                f"/tasks/{inst['id']}/answer",
                json={
                    "instance_id": inst["id"],
                    "answer": "1" if numeric else 0,
                    "time_spent_sec": 45,
                    "mode": inst.get("mode", "practice"),
                    "after_guideline": i == 0,
                },
            )
            if ans.status_code != 200:
                say(False, f"ответ на задачу -> {ans.status_code}: {ans.text[:160]}")
                break
            graded += 1
            aj2 = ans.json()
            st = aj2.get("state_after") or {}
            print(
                f"     задача {i + 1}: тип={inst['type']} верно={aj2['grade']['correct']} "
                f"p_recall={round(st.get('p_recall', -1), 3) if isinstance(st, dict) else '?'} "
                f"слова={aj2.get('state_words', '')[:50]!r} "
                f"проекция={aj2.get('projection_status')}"
            )
        say(graded == 3, f"задач решено и оценено: {graded}")

        print("\n=== 9. Карта знаний изменилась ===")

        def snap(payload):
            out = {}
            for item in payload.get("skills") or []:
                out[item.get("skill_id")] = (
                    item.get("p_recall"),
                    item.get("n_evidence"),
                )
            return out

        kn_after, secs = await poll(
            lambda: c.get("/knowledge", params={"exam_id": EXAM}),
            lambda r: r.status_code == 200 and snap(r.json()) != snap(kn_before),
            limit_s=45,
            label="карта знаний",
        )
        a, b = snap(kn_before), snap(kn_after.json())
        moved = [k for k in b if k in a and b[k] != a[k]]
        say(
            len(moved) > 0,
            f"сдвинулось навыков: {len(moved)} за {secs}s, например "
            f"{moved[0] if moved else '-'}: {a.get(moved[0]) if moved else ''}"
            f" -> {b.get(moved[0]) if moved else ''}",
        )

        print("\n=== 10. Чат по теме с контекстом ===")
        st3, ev3, ttfb3 = await stream_chat(
            c,
            "prep",
            "Объясни коротко, в чём я ошибаюсь в этой теме?",
            set_id=set_id,
            topic_skill_id=skill_id,
        )
        t3 = reply_text(ev3)
        say(st3 == 200, f"чат подготовки -> {st3}, первый байт {ttfb3} мс")
        say(len(t3) > 20, f"ответ {len(t3)} символов: {t3[:90]!r}")

        print("\n=== 11. Закрытие сета и отчёт ===")
        cur = (await c.get(f"/sets/{set_id}")).json()
        closed = 0
        for t in cur["topics"]:
            cr = await c.post(f"/sets/{set_id}/topics/{t['skill_id']}/complete")
            closed += cr.status_code == 200
        say(closed == len(cur["topics"]), f"закрыто тем: {closed}/{len(cur['topics'])}")
        summ, secs = await poll(
            lambda: c.get(f"/sets/{set_id}/summary"),
            lambda r: r.status_code == 200 and r.json().get("status") == "ready",
            limit_s=120,
            label="отчёт по сету",
        )
        if summ.status_code == 200:
            sj = summ.json()
            say(
                bool(sj.get("stats")),
                f"отчёт {sj.get('status')} за {secs}s, статистика: "
                f"{json.dumps(sj.get('stats'), ensure_ascii=False)[:150]}",
            )
            say(bool(sj.get("text")), f"текст отчёта: {str(sj.get('text'))[:110]!r}")
        else:
            say(False, f"/sets/{set_id}/summary -> {summ.status_code}")

        print("\n=== 12. Quack: рекомендации и дашборд ===")
        q, secs = await poll(
            lambda: c.get("/quack"),
            lambda r: r.status_code == 200 and r.json().get("items"),
            limit_s=90,
            label="рекомендации",
        )
        qj = q.json()
        say(
            len(qj.get("items", [])) > 0,
            f"рекомендаций: {len(qj.get('items', []))} за {secs}s",
        )
        pace = qj.get("pace") or {}
        say(bool(pace.get("exams")), f"темп по экзаменам: {len(pace.get('exams', []))}")
        act = qj.get("activity") or {}
        say(
            act.get("active_days") is not None,
            f"активность: {act.get('active_days')} активных дней",
        )
        ov = await c.get("/overview")
        say(ov.status_code == 200, f"/overview -> {ov.status_code}")
        return 0 if not notes else 2


def run() -> int:
    print(f"\n>>> сквозной прогон против {BASE}")
    code = asyncio.run(main())
    print("\n=== ИТОГ ===")
    if notes:
        for n in notes:
            print(f"{BAD} {n}")
    else:
        print(f"{OK} весь цикл прошёл без замечаний")
    return code


if __name__ == "__main__":
    raise SystemExit(run())
