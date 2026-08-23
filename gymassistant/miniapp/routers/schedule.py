"""Главный экран и расписание."""
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from database.orm_extra import (
    orm_delete_empty_sessions,
    orm_get_active_session,
    orm_get_rest_timer,
    orm_get_sets_of_session,
)
from database.orm_query import (
    orm_get_exercises,
    orm_get_program,
    orm_get_programs,
    orm_get_training_day,
)
from miniapp.config import WEEK_DAYS_RU
from miniapp.db import Session
from miniapp.deps import ClientTz, CurrentUser
from miniapp.ownership import own_day
from miniapp.serializers import day_json, exercise_json, program_json, rest_json
from miniapp.state import DEFAULT_CIRCULAR_ROUNDS
from services.clock import today_in
from services.progress import PlannedDay, day_of, missed_day, program_week, weekly_progress
from services.workout import build_plan, current_step

router = APIRouter(prefix="/api", tags=["schedule"])


def today_ru(tz: ZoneInfo) -> str:
    """День недели «сегодня» в поясе пользователя, а не сервера."""
    return WEEK_DAYS_RU[today_in(tz).weekday()]


def planned_json(day: PlannedDay) -> dict:
    """День программы с упражнениями — так его ждут расписание и главный экран."""
    return {**day_json(day.day), "exercises": [exercise_json(e) for e in day.exercises]}


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
        # Неделя программы забирается ОДИН раз и обслуживает все три ответа:
        # сегодняшний день, пропущенный и прогресс недели считают по ней же.
        days = await program_week(session, user.actual_program_id)
        planned_today = day_of(days, today_name)
        today = planned_json(planned_today) if planned_today else None
        missed = await missed_day(session, user.user_id, tz, days)
        week_progress = await weekly_progress(session, user.user_id, tz, days)

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
        "days": [planned_json(d) for d in await program_week(session, program.id)],
    }


@router.get("/day/{day_id}")
async def day(day_id: int, user: CurrentUser, session: Session):
    training_day = await own_day(session, user.user_id, day_id)
    exercises = await orm_get_exercises(session, training_day.id)

    # Кругов в блоке — настройка ПРОГРАММЫ, а не блока: build_plan разворачивает
    # по ней все круговые блоки дня. Экрану дня она нужна, чтобы показать её прямо
    # на блоке и дать поправить на месте, не уводя в настройки программы.
    program = await orm_get_program(session, training_day.training_program_id)

    return {
        "ok": True,
        "day": day_json(training_day),
        "exercises": [exercise_json(e) for e in exercises],
        "program": {
            "id": program.id,
            "circular_rounds": program.circular_rounds,
        } if program else None,
    }
