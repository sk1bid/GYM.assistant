"""
Движок тренировки: чем она должна быть занята прямо сейчас.

Ключевое отличие от бота: здесь нет состояния. В боте «где мы сейчас» жило в FSM
(MemoryStorage), поэтому рестарт пода обрывал тренировку на середине. Здесь текущий
шаг *вычисляется* из плана дня и уже записанных подходов — а они лежат в БД.
Закрыли Mini App, перезапустили под, открыли с другого телефона — шаг тот же.

Модуль чистый: ни aiogram, ни FastAPI, ни ORM-сессии. Только план + факт → шаг.
"""
from dataclasses import dataclass
from typing import List, Sequence

# Сколько кругов в круговом блоке, если программа молчит. Живёт здесь, рядом
# с build_plan, который по этому числу и разворачивает блок: спрашивают его
# и Mini App, и воркер напоминаний, а общего у них только services/.
DEFAULT_CIRCULAR_ROUNDS = 3


def group_exercises_into_blocks(exercises: Sequence) -> List[List]:
    """
    Режет список упражнений дня на блоки.

    Подряд идущие круговые упражнения — круговой блок, подряд идущие обычные —
    обычный. Логика перенесена из handlers/user_private.py как есть: она была
    единственным куском тренировочного процесса, не завязанным на Telegram.
    """
    blocks: List[List] = []
    for ex in exercises:
        if blocks and blocks[-1][0].circle_training == ex.circle_training:
            blocks[-1].append(ex)
        else:
            blocks.append([ex])
    return blocks


@dataclass
class Step:
    """Один шаг тренировки: какое упражнение и какой по счёту подход делать."""
    exercise_id: int
    set_number: int          # номер подхода в рамках упражнения, с 1
    total_sets: int          # сколько подходов запланировано
    block_index: int
    is_circuit: bool
    round_number: int = 1    # для круговых: какой круг
    total_rounds: int = 1

    @property
    def key(self) -> tuple:
        return (self.exercise_id, self.set_number)


def build_plan(exercises: Sequence, circular_rounds: int) -> List[Step]:
    """
    Разворачивает день в плоский список шагов — ровно в том порядке,
    в котором их надо выполнять.

    Обычный блок: упражнение → все его подходы → следующее упражнение.
    Круговой блок: круг = по одному подходу каждого упражнения; кругов circular_rounds.
    """
    plan: List[Step] = []

    for block_index, block in enumerate(group_exercises_into_blocks(exercises)):
        is_circuit = block[0].circle_training

        if is_circuit:
            for round_number in range(1, circular_rounds + 1):
                for ex in block:
                    plan.append(Step(
                        exercise_id=ex.id,
                        set_number=round_number,
                        total_sets=circular_rounds,
                        block_index=block_index,
                        is_circuit=True,
                        round_number=round_number,
                        total_rounds=circular_rounds,
                    ))
        else:
            for ex in block:
                for set_number in range(1, (ex.base_sets or 1) + 1):
                    plan.append(Step(
                        exercise_id=ex.id,
                        set_number=set_number,
                        total_sets=ex.base_sets or 1,
                        block_index=block_index,
                        is_circuit=False,
                    ))

    return plan


def done_counts(sets: Sequence) -> dict:
    """Сколько подходов каждого упражнения уже записано в этой тренировке."""
    counts: dict = {}
    for s in sets:
        counts[s.exercise_id] = counts.get(s.exercise_id, 0) + 1
    return counts


def current_step(plan: Sequence[Step], sets: Sequence) -> Step | None:
    """
    Первый невыполненный шаг плана. None — тренировка отработана целиком.

    Идём по плану и «гасим» шаги уже записанными подходами. Это устойчиво к тому,
    что человек пропустил подход или записал лишний: план — намерение, подходы — факт,
    а шаг — первое расхождение между ними.
    """
    remaining = done_counts(sets)

    for step in plan:
        left = remaining.get(step.exercise_id, 0)
        if left > 0:
            remaining[step.exercise_id] = left - 1
            continue
        return step

    return None


def rest_after(step: Step, next_step: Step | None, program) -> int:
    """
    Сколько отдыхать после только что закрытого шага.

    Здесь же чинится баг с `rest_between_exercise`: в боте это поле клали в FSM
    и никогда не читали, поэтому между упражнениями обычного блока отдыха не было
    вообще. Теперь оно работает.
    """
    if next_step is None:
        return 0

    # Круговой блок: внутри круга — короткий отдых между упражнениями,
    # на стыке кругов — длинный.
    if step.is_circuit and next_step.is_circuit and step.block_index == next_step.block_index:
        if next_step.round_number != step.round_number:
            return program.circular_rest_between_rounds
        return program.circular_rest_between_exercise

    # Обычный блок: тот же снаряд — отдых между подходами, сменили упражнение —
    # отдых между упражнениями.
    if next_step.exercise_id == step.exercise_id:
        return program.rest_between_set

    return program.rest_between_exercise
