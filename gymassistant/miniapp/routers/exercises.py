"""Упражнения внутри тренировочного дня: добавить, настроить, переставить, убрать."""
from fastapi import APIRouter, HTTPException

from database.orm_extra import orm_reorder_exercises
from database.orm_query import (
    move_exercise_down,
    move_exercise_up,
    orm_add_exercise,
    orm_delete_exercise,
    orm_get_admin_exercise,
    orm_get_exercises,
    orm_update_exercise,
)
from miniapp.db import Session
from miniapp.deps import CurrentUser
from miniapp.ownership import own_day, own_exercise, own_user_exercise
from miniapp.schemas import DayExerciseIn, DayExercisesIn, ExercisePatchIn, OrderIn
from miniapp.serializers import exercise_json

router = APIRouter(prefix="/api", tags=["exercises"])


async def day_exercises(session: Session, day_id: int) -> dict:
    """Все ответы этого раздела — обновлённый список упражнений дня."""
    exercises = await orm_get_exercises(session, day_id)
    return {"ok": True, "exercises": [exercise_json(e) for e in exercises]}


async def _append_to_day(session: Session, user_id: int, day_id: int, item: DayExerciseIn) -> None:
    """Одно упражнение каталога в конец дня. Владение днём проверяет вызывающий."""
    if bool(item.admin_exercise_id) == bool(item.user_exercise_id):
        raise HTTPException(400, "нужно ровно одно: admin_exercise_id или user_exercise_id")

    if item.admin_exercise_id:
        catalog = await orm_get_admin_exercise(session, item.admin_exercise_id)
        if not catalog:
            raise HTTPException(404, "упражнение не найдено в каталоге")
        kind, link = "admin", {"admin_exercise_id": catalog.id}
    else:
        catalog = await own_user_exercise(session, user_id, item.user_exercise_id)
        kind, link = "user", {"user_exercise_id": catalog.id}

    await orm_add_exercise(
        session,
        {
            "name": catalog.name,
            "description": catalog.description,
            "circle_training": item.circle_training,
            "equipment": catalog.equipment,
            **link,
        },
        day_id,
        kind,
    )


@router.post("/days/{day_id}/exercises")
async def add_exercise(day_id: int, body: DayExerciseIn, user: CurrentUser, session: Session):
    """Кладёт упражнение из каталога в конец дня."""
    await own_day(session, user.user_id, day_id)
    await _append_to_day(session, user.user_id, day_id, body)
    return await day_exercises(session, day_id)


@router.post("/days/{day_id}/exercises/batch")
async def add_exercises(day_id: int, body: DayExercisesIn, user: CurrentUser, session: Session):
    """
    Кладёт в день сразу несколько упражнений — ровно в том порядке, что прислали.

    Каталог отдаёт упражнения пачкой: человек заходит в «Спину» и берёт оттуда
    тягу, подтягивания и блок разом, а не возвращается в день за каждым. Порядок
    здесь не косметика — подряд идущие круговые собираются в ОДИН круговой блок,
    поэтому упражнения добавляются строго последовательно. Отдельными параллельными
    запросами с клиента этого было бы не добиться: они завершаются как придётся,
    и структура тренировки зависела бы от сети.
    """
    await own_day(session, user.user_id, day_id)

    for item in body.items:
        await _append_to_day(session, user.user_id, day_id, item)

    return await day_exercises(session, day_id)


@router.patch("/days/{day_id}/exercises/order")
async def reorder_exercises(day_id: int, body: OrderIn, user: CurrentUser, session: Session):
    """
    Весь порядок упражнений дня разом — под перетаскивание.

    Одно движение пальца задаёт сразу конечный порядок, а не серию обменов
    соседями: слать его пошагово значило бы N запросов и N промежуточных состояний,
    каждое из которых сервер записал бы как настоящую программу.
    """
    await own_day(session, user.user_id, day_id)

    if not await orm_reorder_exercises(session, day_id, body.ids):
        raise HTTPException(400, "порядок должен содержать все упражнения дня ровно по разу")

    return await day_exercises(session, day_id)


@router.patch("/exercises/{exercise_id}")
async def update_exercise(exercise_id: int, body: ExercisePatchIn, user: CurrentUser, session: Session):
    """
    Подходы, повторения, флаг кругового.

    Повторения раньше поменять было нельзя вообще: incr_reduce_sets_reps вызывался
    только с tp="sets", и base_reps у всех навсегда оставался равен 10.
    """
    exercise = await own_exercise(session, user.user_id, exercise_id)

    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if changes:
        await orm_update_exercise(session, exercise.id, changes)

    return await day_exercises(session, exercise.training_day_id)


@router.post("/exercises/{exercise_id}/move")
async def move_exercise(exercise_id: int, up: bool, user: CurrentUser, session: Session):
    """Порядок упражнений в дне. От него зависит и разбиение на круговые блоки."""
    exercise = await own_exercise(session, user.user_id, exercise_id)

    if up:
        await move_exercise_up(session, exercise.id)
    else:
        await move_exercise_down(session, exercise.id)

    return await day_exercises(session, exercise.training_day_id)


@router.delete("/exercises/{exercise_id}")
async def delete_exercise(exercise_id: int, user: CurrentUser, session: Session):
    exercise = await own_exercise(session, user.user_id, exercise_id)
    day_id = exercise.training_day_id

    await orm_delete_exercise(session, exercise.id)
    return await day_exercises(session, day_id)
