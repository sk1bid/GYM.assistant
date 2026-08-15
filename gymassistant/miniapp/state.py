"""
Состояние тренировки.

Собирается из БД при каждом запросе, а не хранится между ними. Это не лень, а
осознанный выбор: в боте «где мы сейчас» жило в FSM (MemoryStorage), и рестарт пода
обрывал тренировку на середине. Здесь план тренировки — это упражнения дня, факт —
записанные подходы; текущий шаг вычисляется как первое расхождение между ними
(services/workout.py). Закрыли Mini App, перезапустили под, открыли с другого
телефона — шаг тот же.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TrainingSession, User
from database.orm_extra import (
    orm_get_max_weight_by_identity,
    orm_get_prev_sets_by_identity,
    orm_get_rest_timer,
    orm_get_sets_of_session,
)
from database.orm_query import orm_get_exercises, orm_get_program, orm_get_training_day
from miniapp.serializers import day_json, exercise_json, rest_json
from services.progression import suggest
from services.workout import build_plan, current_step

DEFAULT_CIRCULAR_ROUNDS = 3


async def exercise_card(
    session: AsyncSession,
    user_id: int,
    exercise,
    current_session_id=None,
) -> dict:
    """
    Карточка упражнения на экране подхода: рекорд и что было в прошлый раз.

    Всё — по личности упражнения, а не по строке Exercise: «Жим лёжа» в старой и новой
    программе это одно упражнение, поэтому рекорды больше не обнуляются при переезде.
    """
    previous = await orm_get_prev_sets_by_identity(session, user_id, exercise, current_session_id)
    record = await orm_get_max_weight_by_identity(session, user_id, exercise)

    card = exercise_json(exercise)

    # Позапрошлый раз нужен только подсказке и только когда прошлый вообще был:
    # правило 2-for-2 повышает вес, лишь если план закрыт две тренировки подряд.
    before = (
        await orm_get_prev_sets_by_identity(session, user_id, exercise, current_session_id, back=2)
        if previous else []
    )
    hint = suggest(
        exercise.base_sets or 1,
        exercise.base_reps or 1,
        card["step"],
        previous,
        before,
    )

    return {
        **card,
        "record": record,
        "prev": [{"weight": s.weight, "reps": s.repetitions} for s in previous],
        # Что подставить в поле веса. null — предлагать нечего (упражнение новое
        # или все прошлые подходы без веса).
        "suggest": {"action": hint.action, "weight": hint.weight} if hint else None,
    }


async def training_state(session: AsyncSession, user: User, training_session: TrainingSession) -> dict:
    """Всё, что нужно экрану тренировки, одним объектом."""
    day = await orm_get_training_day(session, training_session.training_day_id)
    program = await orm_get_program(session, user.actual_program_id) if user.actual_program_id else None
    rounds = program.circular_rounds if program else DEFAULT_CIRCULAR_ROUNDS

    exercises = await orm_get_exercises(session, day.id) if day else []
    by_id = {e.id: e for e in exercises}

    plan = build_plan(exercises, rounds)
    done = await orm_get_sets_of_session(session, training_session.id)
    step = current_step(plan, done)

    current = None
    if step is not None:
        # Место упражнения в своём блоке. Нужно круговым: «круг 1 из 3» говорит,
        # какой круг, но не сколько снарядов осталось пройти внутри него, — а именно
        # это и хочется знать, стоя между тремя станциями. Порядок берём из плана,
        # он же порядок выполнения.
        block: list[int] = []
        for planned in plan:
            if planned.block_index == step.block_index and planned.exercise_id not in block:
                block.append(planned.exercise_id)

        current = {
            "exercise": await exercise_card(
                session, user.user_id, by_id[step.exercise_id],
                current_session_id=training_session.id,
            ),
            "set_number": step.set_number,
            "total_sets": step.total_sets,
            "is_circuit": step.is_circuit,
            "round_number": step.round_number,
            "total_rounds": step.total_rounds,
            "exercise_number": block.index(step.exercise_id) + 1,
            "total_exercises": len(block),
        }

    timer = await orm_get_rest_timer(session, user.user_id)

    return {
        "ok": True,
        "session_id": str(training_session.id),
        "day": day_json(day) if day else None,
        "finished": step is None,
        "progress": {"done": len(done), "total": len(plan)},
        "current": current,
        "plan": [
            {
                "exercise_id": s.exercise_id,
                "name": by_id[s.exercise_id].name,
                "set_number": s.set_number,
                "total_sets": s.total_sets,
                "is_circuit": s.is_circuit,
                "round_number": s.round_number,
                "total_rounds": s.total_rounds,
            }
            for s in plan
        ],
        "sets": [
            {
                "id": s.id,
                "exercise_id": s.exercise_id,
                # Упражнение могли удалить из дня уже после того, как подход записан.
                "name": by_id[s.exercise_id].name if s.exercise_id in by_id else "—",
                "weight": s.weight,
                "reps": s.repetitions,
                # Пропущенный подход экран показывает, но иначе: он занимает своё
                # место в тренировке, а результата за ним нет.
                "skipped": s.skipped,
            }
            for s in done
        ],
        "rest": rest_json(timer),
    }
