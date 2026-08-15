"""
Что уходит на фронт.

Собрано в одном месте, чтобы упражнение выглядело одинаково во всех разделах —
в расписании, в конструкторе и на экране подхода.
"""
from database.models import Exercise, RestTimer, TrainingDay, TrainingProgram
from services.clock import utcnow
from services.equipment import OTHER, weight_step


def day_json(day: TrainingDay) -> dict:
    return {"id": day.id, "day_of_week": day.day_of_week}


def exercise_json(exercise: Exercise) -> dict:
    return {
        "id": exercise.id,
        "name": exercise.name,
        "description": exercise.description,
        "sets": exercise.base_sets,
        "reps": exercise.base_reps,
        "circle": exercise.circle_training,
        "position": exercise.position,
        "equipment": exercise.equipment or OTHER,
        # Шаг кнопок «−/+» под весом — всегда число. Показывать ли поле веса,
        # клиент решает по снаряду: у своего веса поле необязательно, но не
        # запрещено (подтягивания с поясом), и шаг там нужен такой же.
        "step": weight_step(exercise.equipment, exercise.weight_step),
    }


def program_json(program: TrainingProgram, active_id: int | None) -> dict:
    return {
        "id": program.id,
        "name": program.name,
        "active": program.id == active_id,
        "settings": {
            "rest_between_set": program.rest_between_set,
            "rest_between_exercise": program.rest_between_exercise,
            "circular_rounds": program.circular_rounds,
            "circular_rest_between_rounds": program.circular_rest_between_rounds,
            "circular_rest_between_exercise": program.circular_rest_between_exercise,
        },
    }


def rest_json(timer: RestTimer | None) -> dict | None:
    """
    Сколько осталось отдыхать — по часам сервера.

    Фронт рисует обратный отсчёт от этого числа, но не является его источником:
    вкладку могли закрыть, телефон — заблокировать, а таймер всё это время шёл.
    """
    if not timer or not timer.active:
        return None

    left = int((timer.ends_at - utcnow()).total_seconds())
    if left <= 0:
        return None

    return {"left": left, "total": timer.total_seconds, "next_up": timer.next_up}
