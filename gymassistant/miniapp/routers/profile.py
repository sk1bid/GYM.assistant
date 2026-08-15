"""Профиль, история тренировок и прогресс."""
from datetime import date, timedelta, timezone

from fastapi import APIRouter

from database.orm_extra import (
    orm_get_exercise_progress,
    orm_get_max_volume_by_identity,
    orm_get_max_weight_by_identity,
    orm_get_sessions_summary,
    orm_get_sets_of_session,
)
from database.orm_query import (
    orm_get_exercise,
    orm_get_exercises,
    orm_get_programs,
    orm_get_training_days,
    orm_update_user,
)
from miniapp.db import Session
from miniapp.deps import ClientTz, CurrentUser
from miniapp.ownership import own_exercise, own_training_session
from miniapp.routers.training import parse_session_id
from miniapp.schemas import ProfileIn
from services.clock import today_in
from services.equipment import OTHER, weight_step

router = APIRouter(prefix="/api", tags=["profile"])


@router.get("/profile")
async def profile(user: CurrentUser, session: Session):
    sessions = await orm_get_sessions_summary(session, user.user_id, limit=1000)

    # Программы приезжают сюда, потому что вкладки «Программы» больше нет: раздел
    # живёт в профиле, и строке-ссылке нужно имя активной программы для подписи.
    # Отдельным запросом с фронта это была бы вторая круговая задержка ради одной строки.
    programs = await orm_get_programs(session, user.user_id)
    active = next((p for p in programs if p.id == user.actual_program_id), None)

    return {
        "ok": True,
        "user": {"name": user.name, "weight": user.weight},
        "total": {
            "sessions": len(sessions),
            "sets": sum(s.sets for s in sessions),
            "volume": sum(float(s.volume) for s in sessions),
        },
        "programs": {"count": len(programs), "active": active.name if active else None},
    }


@router.patch("/profile")
async def update_profile(body: ProfileIn, user: CurrentUser, session: Session):
    await orm_update_user(session, user.user_id, {"name": body.name.strip(), "weight": body.weight})
    return {"ok": True}


@router.get("/history")
async def history(user: CurrentUser, session: Session, offset: int = 0, limit: int = 20):
    """
    Список тренировок.

    В боте кнопки истории ссылались на UUID из модульного словаря
    utils/temporary_storage.py: он не чистился, тёк, и после рестарта пода все кнопки
    истории умирали. В вебе id просто едет в URL — словарь не нужен вообще.
    """
    rows = await orm_get_sessions_summary(session, user.user_id, limit=limit, offset=offset)
    return {
        "ok": True,
        "sessions": [
            {
                "id": str(r.id),
                "date": r.date.isoformat() if r.date else None,
                "sets": r.sets,
                "exercises": r.exercises,
                "volume": float(r.volume),
            }
            for r in rows
        ],
    }


@router.get("/history/{session_id}")
async def history_detail(session_id: str, user: CurrentUser, session: Session):
    """Одна тренировка: упражнения и все подходы по ним."""
    training = await own_training_session(session, user.user_id, parse_session_id(session_id))
    sets = await orm_get_sets_of_session(session, training.id)

    grouped: dict[int, dict] = {}
    for recorded in sets:
        if recorded.exercise_id not in grouped:
            exercise = await orm_get_exercise(session, recorded.exercise_id)
            grouped[recorded.exercise_id] = {
                "exercise_id": recorded.exercise_id,
                "name": exercise.name if exercise else "удалённое упражнение",
                # Снаряд нужен и здесь: подходы хранятся в килограммах, но на
                # блоке человек считал блоками, и в истории он должен увидеть их
                # же, иначе один и тот же подход выглядит на двух экранах
                # по-разному.
                "equipment": (exercise.equipment or OTHER) if exercise else OTHER,
                "step": weight_step(exercise.equipment, exercise.weight_step) if exercise else None,
                "sets": [],
            }
        grouped[recorded.exercise_id]["sets"].append({
            "id": recorded.id,
            "weight": recorded.weight,
            "reps": recorded.repetitions,
        })

    return {
        "ok": True,
        "session": {
            "id": str(training.id),
            "date": training.date.isoformat() if training.date else None,
            "sets": len(sets),
            "volume": sum(s.weight * s.repetitions for s in sets),
        },
        "exercises": list(grouped.values()),
    }


@router.get("/stats")
async def stats(user: CurrentUser, session: Session):
    """
    Рекорды по всем упражнениям, которые пользователь когда-либо делал.

    Схлопываем по личности упражнения: «Жим лёжа» из старой программы и из новой —
    одна строка рекордов, а не две.
    """
    records: dict[tuple, dict] = {}

    for program in await orm_get_programs(session, user.user_id):
        for day in await orm_get_training_days(session, program.id):
            for exercise in await orm_get_exercises(session, day.id):
                key = (
                    ("admin", exercise.admin_exercise_id)
                    if exercise.admin_exercise_id
                    else ("user", exercise.user_exercise_id)
                )
                if key in records:
                    continue

                max_weight = await orm_get_max_weight_by_identity(session, user.user_id, exercise)
                if not max_weight:
                    continue  # ни одного подхода — в рекордах ему делать нечего

                records[key] = {
                    "exercise_id": exercise.id,
                    "name": exercise.name,
                    "max_weight": max_weight,
                    "max_volume": await orm_get_max_volume_by_identity(session, user.user_id, exercise),
                }

    return {
        "ok": True,
        "records": sorted(records.values(), key=lambda r: r["max_weight"], reverse=True),
    }


@router.get("/stats/activity")
async def activity(user: CurrentUser, session: Session, tz: ClientTz, weeks: int = 18):
    """
    Календарь тренировок — «квадратики», как на GitHub.

    Сетка всегда начинается с понедельника: колонка = неделя, строка = день недели.
    Без выравнивания на понедельник столбцы поехали бы, и соседние квадраты
    оказались бы разными днями недели — читать такую карту нельзя.

    Дата тренировки хранится в naive-UTC, а раскладывать надо по КАЛЕНДАРЮ
    пользователя: тренировка в 23:30 по Новосибирску — это 16:30 UTC того же дня,
    а вот в 06:00 по НСК — уже 23:00 UTC предыдущего. Без перевода в пояс клиента
    часть тренировок села бы в соседнюю клетку.

    Отдельного ORM-запроса нет намеренно: сводка по тренировкам уже посчитана
    для истории и профиля, здесь она просто схлопывается по дням.
    """
    weeks = max(1, min(weeks, 53))
    today = today_in(tz)
    start = today - timedelta(days=today.weekday() + 7 * (weeks - 1))

    rows = await orm_get_sessions_summary(session, user.user_id, limit=1000)

    by_day: dict[date, dict] = {}
    for row in rows:
        if not row.date:
            continue

        local = row.date.replace(tzinfo=timezone.utc).astimezone(tz).date()
        if not (start <= local <= today):
            continue

        cell = by_day.setdefault(local, {"sets": 0, "volume": 0.0})
        cell["sets"] += row.sets
        cell["volume"] += float(row.volume)

    days = []
    for offset in range((today - start).days + 1):
        current = start + timedelta(days=offset)
        cell = by_day.get(current)
        days.append({
            "date": current.isoformat(),
            "sets": cell["sets"] if cell else 0,
            "volume": cell["volume"] if cell else 0.0,
        })

    return {
        "ok": True,
        "start": start.isoformat(),
        "today": today.isoformat(),
        "weeks": weeks,
        "days": days,
        # Дни, а не сессии: подпись стоит прямо над сеткой, а клетка сетки — это день.
        # Две тренировки за сутки — один закрашенный квадрат, и «19 тренировок»
        # над восемнадцатью квадратами читалось как ошибка отрисовки.
        "active_days": len(by_day),
    }


@router.get("/stats/exercise/{exercise_id}")
async def exercise_progress(exercise_id: int, user: CurrentUser, session: Session):
    """График по упражнению: одна точка на тренировку."""
    exercise = await own_exercise(session, user.user_id, exercise_id)
    rows = await orm_get_exercise_progress(session, user.user_id, exercise)

    return {
        "ok": True,
        "name": exercise.name,
        "points": [
            {
                "date": r.date.isoformat() if r.date else None,
                "max_weight": float(r.max_weight or 0),
                "volume": float(r.volume or 0),
                "sets": r.sets,
            }
            for r in rows
        ],
    }
