"""Главный экран и расписание."""
from datetime import date, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from database.orm_extra import (
    orm_delete_empty_sessions,
    orm_get_active_session,
    orm_get_exercises_of_days,
    orm_get_last_training_per_day,
    orm_get_rest_timer,
    orm_get_sessions_summary,
    orm_get_sets_of_session,
    utcnow,
)
from database.orm_query import (
    orm_get_exercises,
    orm_get_program,
    orm_get_programs,
    orm_get_training_day,
    orm_get_training_days,
)
from miniapp.config import WEEK_DAYS_RU
from miniapp.db import Session
from miniapp.deps import ClientTz, CurrentUser
from miniapp.ownership import own_day
from miniapp.serializers import day_json, exercise_json, program_json, rest_json
from miniapp.state import DEFAULT_CIRCULAR_ROUNDS
from services.clock import today_in
from services.workout import build_plan, current_step

router = APIRouter(prefix="/api", tags=["schedule"])


def today_ru(tz: ZoneInfo) -> str:
    """День недели «сегодня» в поясе пользователя, а не сервера."""
    return WEEK_DAYS_RU[today_in(tz).weekday()]


async def week(session: Session, program_id: int) -> list[dict]:
    """
    Дни программы в порядке Пн→Вс — в БД они лежат в порядке вставки.

    Упражнения всех семи дней забираются одним запросом. Раньше здесь был вызов
    orm_get_exercises внутри цикла: семь последовательных обращений к постгресу
    на каждое открытие главной и расписания, и их задержки складывались — экран
    заметно «думал» перед появлением.
    """
    days = await orm_get_training_days(session, program_id)
    by_name = {d.day_of_week.strip().lower(): d for d in days}

    ordered = [by_name[name.lower()] for name in WEEK_DAYS_RU if name.lower() in by_name]
    exercises = await orm_get_exercises_of_days(session, [d.id for d in ordered])

    return [
        {**day_json(day), "exercises": [exercise_json(e) for e in exercises.get(day.id, [])]}
        for day in ordered
    ]


async def missed_day(session: Session, user, tz, days: list[dict]) -> dict | None:
    """
    Последний пропущенный тренировочный день, если он есть.

    Пропуск — это день программы с упражнениями, чей день недели уже прошёл, а
    тренировки по нему за последнюю неделю не было. Отдаём ОДИН день, самый свежий,
    а не список: пять строк «пропущено» на главной были бы упрёком, а не помощью.

    Три границы, каждая по своей причине.

    **Сравниваем дни ПРОГРАММЫ, а не даты.** Отработал понедельник во вторник —
    это перенос, и в среду напоминать не о чем: сессия помнит `training_day_id`,
    и день считается закрытым независимо от того, какого числа его закрыли.

    **Тренировка обязана быть НЕ РАНЬШЕ самого дня.** День недели повторяется, а
    тренировка по нему закрывает ровно один его повтор — тот, что уже наступил к её
    моменту. Сначала здесь стояло «была ли вообще сессия по этому дню за семь суток»,
    и одна тренировка гасила сразу два повтора: отработал прошлое воскресенье во
    вторник — и в понедельник сервер считал закрытым уже наступившее воскресенье,
    а пропущенной показывал субботу, то есть день ПОЗАДИ ближайшего. Отсюда сравнение
    даты сессии (в поясе клиента, дата хранится naive-UTC) с датой самого дня.

    **Свежесть — неделя.** Окно на выборке тренировок: то, что делалось больше семи
    суток назад, день уже не закрывает. После сравнения дат это ещё и дешёвый
    предфильтр — дальше семи суток назад ни один искомый день всё равно не лежит.

    **Ищем шесть дней назад, а не семь.** Седьмой — это тот же день недели, что
    сегодня, то есть СЕГОДНЯШНЯЯ тренировка. Предлагать «отработать» то, что и так
    стоит в плане на сегодня, бессмысленно: экран показал бы один и тот же день
    дважды, и обе кнопки открыли бы одну и ту же тренировку. Заодно это и есть
    правило «пропущенное должно идти строго ПЕРЕД тем, что будет»: при поиске на
    шесть дней у дня всегда остаётся хотя бы сутки до его собственного повтора.
    """
    planned = {d["day_of_week"].strip().lower(): d for d in days if d["exercises"]}
    if not planned:
        return None

    today = today_in(tz)
    last_trained = await orm_get_last_training_per_day(
        session, user.user_id, utcnow() - timedelta(days=7)
    )

    for back in range(1, 7):
        past = today - timedelta(days=back)
        day = planned.get(WEEK_DAYS_RU[past.weekday()].lower())
        if not day:
            continue

        last = last_trained.get(day["id"])
        closed = last is not None and last.replace(tzinfo=timezone.utc).astimezone(tz).date() >= past
        if not closed:
            return {"id": day["id"], "day_of_week": day["day_of_week"], "days_ago": back}

    return None


async def weekly_progress(session: Session, user, tz: ZoneInfo, days: list[dict]) -> dict:
    """
    Прогресс недели и серия — мотивационная сводка для главного экрана.

    «Сделано» — сколько РАЗНЫХ дней текущей календарной недели (Пн→сегодня) были
    тренировочными; «цель» — сколько тренировочных дней в программе. Серия — сколько
    недель подряд цель была ЗАКРЫТА.

    `left` — сколько тренировочных дней ПРОГРАММЫ на этой неделе ещё впереди
    (сегодняшний считается, пока не отработан). Без него экран обещал невозможное:
    в пятницу при цели 4 и нуле сделанных он писал «ещё 4 тренировки», хотя до
    воскресенья таких дней в программе оставалось разве что один. Разница между
    «сколько не хватает до цели» и «сколько ещё физически влезет» — и есть повод
    считать это на сервере, а не вычитать на клиенте.

    **Неделя входит в серию, только если закрыта целиком** (`done >= goal`). Раньше
    хватало одной тренировки за неделю — и серия росла месяцами у человека, который
    из четырёх запланированных дней делал один. Число в карточке обязано означать
    то, чем его называют: «серия» рядом с кольцом 0/4 — это про закрытые кольца,
    а не про «хоть раз зашёл в зал».

    В какие именно дни недели тренировались — не важно: цель задана числом дней,
    а не расписанием. Отработал субботнюю программу в среду — неделя всё равно
    закрыта, для этого `done` и считает РАЗНЫЕ ДАТЫ, а не совпадения с днями недели.

    Серия при этом мягкая: текущая неделя не рвёт её, пока не кончилась. Если цель
    этой недели ещё не закрыта, отсчёт начинается с прошлой (грейс) — иначе серия
    обнулялась бы каждый понедельник. Так же устроены недельные серии у Apple
    Fitness+ и цель «N дней в неделю» у Fitbit: неделя календарная, Пн→Вс, а не
    скользящее окно.

    Оговорка, которую стоит помнить: `goal` берётся из ТЕКУЩЕЙ программы, истории
    её правок мы не храним. Сменив программу с трёх дней на пять, пользователь
    пересудит и прошлые недели по новой мерке. Чинится только версионированием
    программы — до тех пор это осознанное упрощение, а не недосмотр.

    Дата тренировки хранится в naive-UTC, а неделя раскладывается по КАЛЕНДАРЮ
    пользователя: тренировка в 6 утра по Новосибирску — это 23:00 UTC накануне,
    и без перевода в пояс часть тренировок села бы в соседнюю неделю.
    """
    rows = await orm_get_sessions_summary(session, user.user_id, limit=400)

    trained: set[date] = set()
    for row in rows:
        if row.date:
            trained.add(row.date.replace(tzinfo=timezone.utc).astimezone(tz).date())

    today = today_in(tz)
    week_start = today - timedelta(days=today.weekday())
    this_week = {day for day in trained if week_start <= day <= today}

    # Дни недели программы, в которых есть упражнения. Имя дня сверяем нормализованным:
    # в базе оно лежит как ввёл пользователь, регистр и пробелы бывают любыми.
    order = {name.lower(): index for index, name in enumerate(WEEK_DAYS_RU)}
    planned = {
        order[day["day_of_week"].strip().lower()]
        for day in days
        if day["exercises"] and day["day_of_week"].strip().lower() in order
    }

    left = sum(1 for weekday in planned if weekday > today.weekday())
    if today.weekday() in planned and today not in this_week:
        left += 1

    by_week: dict[date, set[date]] = {}
    for day in trained:
        by_week.setdefault(day - timedelta(days=day.weekday()), set()).add(day)

    goal = len(planned)

    def closed(monday: date) -> bool:
        # goal == 0 — в программе нет тренировочных дней, закрывать нечего. Без этой
        # проверки любая неделя оказалась бы «закрытой» и серия росла бы из ничего.
        return bool(goal) and len(by_week.get(monday, ())) >= goal

    anchor = week_start if closed(week_start) else week_start - timedelta(days=7)
    streak = 0
    while closed(anchor):
        streak += 1
        anchor -= timedelta(days=7)

    return {"done": len(this_week), "goal": goal, "streak": streak, "left": left}


async def active_training(session: Session, user, training) -> dict:
    """
    Идущая тренировка для главного экрана.

    Одного id мало. Тренируют не только сегодняшний день: пропущенный отрабатывают
    кнопкой, любой другой выбирают шторкой. Пока тренировка идёт, главный экран
    рассказывает про НЕЁ, а не про календарь, — значит ему нужны и день, и его
    упражнения, и то, где человек внутри плана остановился. Иначе получалось
    расхождение: карточка называла субботу, а список под ней оставался сегодняшним.

    Упражнения бесплатны: они всё равно загружены, чтобы построить план. Прогресс
    и текущий шаг считаются ровно как на экране тренировки (`training_state`) —
    план дня против записанных подходов. Три запроса, и только когда тренировка
    правда идёт.
    """
    day = await orm_get_training_day(session, training.training_day_id)
    exercises = await orm_get_exercises(session, day.id) if day else []

    program = await orm_get_program(session, user.actual_program_id) if user.actual_program_id else None
    rounds = (program.circular_rounds if program else None) or DEFAULT_CIRCULAR_ROUNDS

    plan = build_plan(exercises, rounds)
    done = await orm_get_sets_of_session(session, training.id)
    step = current_step(plan, done)

    return {
        "session_id": str(training.id),
        "day": {
            **day_json(day),
            "exercises": [exercise_json(e) for e in exercises],
        } if day else None,
        "done": len(done),
        "total": len(plan),
        # Что делать прямо сейчас. None — план отработан целиком и осталось только
        # нажать «Завершить», поэтому выделять в списке нечего.
        "next_exercise_id": step.exercise_id if step else None,
    }


@router.get("/bootstrap")
async def bootstrap(user: CurrentUser, session: Session, tz: ClientTz):
    """Всё, что нужно приложению при открытии, одним запросом."""
    # Тренировки, начатые и брошенные без единого подхода, только мусорят историю.
    await orm_delete_empty_sessions(session, user.user_id)

    programs = await orm_get_programs(session, user.user_id)
    active = await orm_get_active_session(session, user.user_id)
    timer = await orm_get_rest_timer(session, user.user_id)

    today_name = today_ru(tz)
    today = None
    missed = None
    week_progress = None
    if user.actual_program_id:
        days = await week(session, user.actual_program_id)
        today = next((d for d in days if d["day_of_week"].strip().lower() == today_name.lower()), None)
        missed = await missed_day(session, user, tz, days)
        week_progress = await weekly_progress(session, user, tz, days)

    return {
        "ok": True,
        "user": {"id": user.user_id, "name": user.name, "weight": user.weight},
        "programs": [program_json(p, user.actual_program_id) for p in programs],
        "has_program": bool(user.actual_program_id),
        "today": today,
        "today_name": today_name,
        "missed": missed,
        "week": week_progress,
        "active": await active_training(session, user, active) if active else None,
        "rest": rest_json(timer),
    }


@router.get("/schedule")
async def schedule(user: CurrentUser, session: Session, tz: ClientTz):
    """Неделя активной программы."""
    if not user.actual_program_id:
        return {"ok": True, "program": None, "days": [], "today": today_ru(tz)}

    program = await orm_get_program(session, user.actual_program_id)
    return {
        "ok": True,
        "program": program_json(program, user.actual_program_id),
        "today": today_ru(tz),
        "days": await week(session, program.id),
    }


@router.get("/day/{day_id}")
async def day(day_id: int, user: CurrentUser, session: Session):
    training_day = await own_day(session, user.user_id, day_id)
    exercises = await orm_get_exercises(session, training_day.id)
    return {
        "ok": True,
        "day": day_json(training_day),
        "exercises": [exercise_json(e) for e in exercises],
    }
